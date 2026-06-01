"""Scout orchestrator + CLI entry point (shortlist-scout). See docs/AUTONOMOUS_SCOUT.md §3."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from ..env import load_env, redact_secrets
from .budget import select
from .calendar import is_trading_day, last_session
from .funnel import aggregate, prefilter
from .models import RunManifest, SignalStatus
from .report import render_message
from .signals import build_signals
from .state import ScoutState

_DEFAULT_CONFIG = Path(__file__).parent.parent.parent.parent / "config.yaml"


_DISCOVERY_SIGNAL_NAMES = {"yahoo_screener", "edgar_form4"}
_BOOSTER_SIGNAL_NAMES   = {"finnhub_news", "wikipedia"}


def _enabled_signal_names(scout_cfg: dict) -> list[str]:
    name_map = {"yahoo_screener": "yahoo_screener", "edgar_form4": "edgar_form4",
                "finnhub_news": "finnhub_news", "wikipedia": "wikipedia", "quiver": "quiver"}
    return [name_map[k] for k, v in scout_cfg.get("signals", {}).items()
            if v.get("enabled") and k in name_map]


def _signal_kwargs(scout_cfg: dict) -> dict[str, dict]:
    """Build per-signal constructor kwargs from config + env for live (non-demo) runs."""
    return {
        "edgar_form4":   {"max_filings": scout_cfg.get("edgar_index_daily_cap", 400)},
        "finnhub_news":  {"api_key": os.environ.get("FINNHUB_API_KEY")},
        "wikipedia":     {"ticker_map": scout_cfg.get("wikipedia_ticker_map", {})},
    }


def run(config: dict, *, demo: bool, today: date) -> int:
    scout_cfg = config.get("scout", {})
    # Non-trading days are fine: last_session() anchors to the most recent session.
    session = today if demo else last_session(today)

    state = ScoutState(Path(scout_cfg.get("state_path", "state/scout_state.json")))
    if not demo and state.run_completed(session):
        print(f"scout: run for {session} already completed; nothing to do")
        return 0

    # 1. Scan discovery signals
    weights_by_signal: dict[str, float] = {}
    sig_cfg = scout_cfg.get("signals", {})
    statuses: list[SignalStatus] = []
    emissions = []

    if demo:
        signals = build_signals(["mock"])
        boosters = []
    else:
        all_names = _enabled_signal_names(scout_cfg)
        kwargs_by_name = _signal_kwargs(scout_cfg)
        signals = build_signals(all_names, kwargs_by_name=kwargs_by_name)
        boosters = [s for s in signals if not getattr(s, "is_discovery", True)]

    discovery = [s for s in signals if getattr(s, "is_discovery", False)]
    for s in discovery:
        ems = s.scan(session)
        emissions.extend(ems)
        ran, detail = s.available()
        statuses.append(SignalStatus(s.name, ran, detail))
        # weight by config: map signal prefix back to its config key
        cfg_key = {"yahoo_screener": "yahoo_screener", "edgar_form4": "edgar_form4",
                   "mock": "yahoo_screener"}.get(s.name, s.name)
        w = sig_cfg.get(cfg_key, {}).get("weight", 1.0)
        for e in ems:
            weights_by_signal[e.signal] = w

    raw = len(emissions)
    cands = aggregate(emissions, weights_by_signal)
    after_dedup = len(cands)

    kept = prefilter(
        cands,
        in_cooldown=lambda t: state.in_cooldown(t, on=session,
                                                cooldown_days=scout_cfg.get("cooldown_days", 7)),
        is_held=state.is_held)
    after_prefilter = len(kept)

    # 1b. Run confluence boosters on already-discovered names (§3 step 2 / §4).
    # Boosters only raise interest for tickers already in `kept`; they never originate.
    if boosters:
        kept_by_ticker = {c.ticker: c for c in kept}
        for booster in boosters:
            cfg_key = booster.name  # e.g. "finnhub_news", "wikipedia"
            w = sig_cfg.get(cfg_key, {}).get("weight", 0.5)
            try:
                booster_ems = booster.scan_for([c.ticker for c in kept], session)
            except Exception as exc:  # noqa: BLE001
                booster_ems = []
                booster._status = (False, redact_secrets(str(exc)))  # type: ignore[attr-defined]
            for em in booster_ems:
                if em.ticker in kept_by_ticker:  # only fold into existing candidates
                    kept_by_ticker[em.ticker].add(em, w)
            ran, detail = booster.available()
            statuses.append(SignalStatus(booster.name, ran, detail))

    chosen, dropped = select(kept, daily_x=scout_cfg.get("daily_x", 15))

    # 2. Deep-screen via the existing harness scorer
    sources = scout_cfg.get("deep_screen_sources", ["yahoo", "fmp", "finnhub", "edgar"])
    if demo:
        from ..screen import run as run_screener
        cards = run_screener([c.ticker for c in chosen], ["mock"], config)
    else:
        from ..screen import run_harness
        cards = run_harness([c.ticker for c in chosen], sources, config)

    # 3. Auto-research (guardrailed) — skipped in demo
    briefs: dict[str, str] = {}
    researched: list[str] = []
    notes: list[str] = []
    if not demo:
        briefs, researched, note = _research_phase(cards, config, scout_cfg)
        if note:
            notes.append(note)

    manifest = RunManifest(
        session=session, signals=statuses, raw=raw, after_dedup=after_dedup,
        after_prefilter=after_prefilter, screened=len(cards), dropped_for_budget=dropped,
        researched=researched, notes=notes)

    message = render_message(cards, manifest, briefs)

    # 4. Deliver + persist
    if demo:
        print(message)
    else:
        _write_manifest(scout_cfg, manifest, message)
        from .notify import send_telegram
        if not send_telegram(message):
            print(message)  # fall back to stdout if Telegram is unconfigured
        state.record_screened([c.ticker for c in cards], session)
        state.mark_run_completed(session)
    return 0


def _research_phase(
    cards,
    config,
    scout_cfg,
    *,
    _is_available=None,
    _enrich=None,
) -> tuple[dict, list, str | None]:
    """Guardrailed auto-research: kill-switch, auth probe, hard cap, phase budget.

    Returns (briefs: dict[ticker, one_line_str], researched: list[ticker], note: str|None).

    Optional _is_available/_enrich kwargs allow injection in tests without monkeypatching
    the import machinery.  When omitted the real research module is imported lazily.

    REAL API: enrich() returns list[ResearchResult] (not dict[ticker, path]).
    ResearchResult.synthesis is the 2-3 sentence LLM text; ResearchResult.brief_path
    is the .md file path (a matching .json record is also written alongside it).
    We use synthesis directly from the result object — no need to read a JSON file
    for in-session results. For cached results (from_cache=True), synthesis is empty
    so we fall back to reading the record JSON (which has a 'synthesis' key).
    """
    if os.environ.get("SCOUT_NO_RESEARCH") == "1" or Path("scout/STOP_RESEARCH").exists():
        return {}, [], "research skipped: kill-switch"
    if _is_available is None or _enrich is None:
        try:
            from ..research import is_available as _ia, enrich as _en
            _is_available = _is_available or _ia
            _enrich = _enrich or _en
        except Exception:  # noqa: BLE001
            return {}, [], "research skipped: layer unavailable"
    if not _is_available():
        return {}, [], "research skipped: claude CLI / edgartools not available"
    n = scout_cfg.get("research_top_n", 3)
    budget_s = scout_cfg.get("research_phase_budget_s", 600)
    try:
        # Wrap the entire enrich() call in a ThreadPoolExecutor so we can enforce a
        # wall-clock ceiling (research_phase_budget_s).  N hung claude calls serialise
        # inside enrich(); without this timeout the phase budget is decorative config.
        #
        # IMPORTANT: do NOT use `with ThreadPoolExecutor(...) as pool:` — that context
        # manager calls shutdown(wait=True) on exit, which blocks until the thread
        # finishes even after a TimeoutError.  Instead construct explicitly and call
        # shutdown(wait=False) so we abandon the hung thread immediately.
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(_enrich, cards, config, top_n=n, refresh=False)
        try:
            results = future.result(timeout=budget_s)
            pool.shutdown(wait=False)
        except concurrent.futures.TimeoutError:
            pool.shutdown(wait=False, cancel_futures=True)
            return {}, [], f"research skipped: phase budget {budget_s}s exceeded"
    except Exception as e:  # noqa: BLE001
        return {}, [], f"research failed: {redact_secrets(str(e))}"

    briefs: dict[str, str] = {}
    researched: list[str] = []
    for r in results:
        if r.skipped:
            continue
        researched.append(r.ticker)
        # Use in-memory synthesis when fresh; fall back to the JSON record for cached results
        brief_text = r.synthesis if r.synthesis else _one_line_brief_from_file(r.brief_path)
        briefs[r.ticker] = brief_text[:200]
    return briefs, researched, None


def _one_line_brief_from_file(brief_path) -> str:
    """Read synthesis from the JSON record file that report.write() writes alongside the .md."""
    try:
        # report.write() writes <ticker>/<accession>.json as the JSON record
        json_path = Path(str(brief_path).replace(".md", ".json"))
        data = json.loads(json_path.read_text())
        # QualitativeAssessment fields in the JSON: 'synthesis' is the 2-3 sentence text
        return (data.get("synthesis") or data.get("summary") or "")[:200]
    except Exception:  # noqa: BLE001
        return "brief generated"


def _write_manifest(scout_cfg, manifest, message) -> None:
    out_dir = Path(scout_cfg.get("artifact_dir", "scout"))
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = manifest.session.isoformat()
    (out_dir / f"{stamp}.json").write_text(json.dumps(manifest.to_dict(), indent=2))
    (out_dir / f"{stamp}.txt").write_text(message)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="shortlist-scout",
                                 description="Autonomous candidate discovery + daily report.")
    ap.add_argument("--demo", action="store_true", help="offline run; print report to stdout")
    ap.add_argument("--config", default=str(_DEFAULT_CONFIG))
    ap.add_argument("--no-research", action="store_true", help="skip the Claude research phase")
    args = ap.parse_args(argv)

    load_env()
    if args.no_research:
        os.environ["SCOUT_NO_RESEARCH"] = "1"
    config = yaml.safe_load(Path(args.config).read_text())
    today = datetime.now(timezone.utc).date()
    try:
        return run(config, demo=args.demo, today=today)
    except Exception as e:  # noqa: BLE001
        print(f"scout: run failed: {redact_secrets(str(e))}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

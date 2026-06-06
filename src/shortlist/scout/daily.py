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
from ..models import rank_key
from .budget import select
from .calendar import last_session
from .funnel import aggregate, prefilter
from .models import RunManifest, SignalStatus
from .signals import build_signals
from .state import ScoutState

_DEFAULT_CONFIG = Path(__file__).parent.parent.parent.parent / "config.yaml"


_DISCOVERY_SIGNAL_NAMES = {"yahoo_screener", "edgar_form4"}
_BOOSTER_SIGNAL_NAMES   = {"finnhub_news", "wikipedia"}
# Config keys we know how to build a signal for. An enabled key not in here is
# ignored; a disabled key in here still gets a "✗ (disabled)" coverage line.
_KNOWN_SIGNAL_KEYS = _DISCOVERY_SIGNAL_NAMES | _BOOSTER_SIGNAL_NAMES | {"quiver"}


def _enabled_signal_names(scout_cfg: dict) -> list[str]:
    return [k for k, v in scout_cfg.get("signals", {}).items()
            if v.get("enabled") and k in _KNOWN_SIGNAL_KEYS]


def _signal_kwargs(scout_cfg: dict) -> dict[str, dict]:
    """Build per-signal constructor kwargs from config + env for live (non-demo) runs."""
    return {
        "edgar_form4":   {"max_filings": scout_cfg.get("edgar_index_daily_cap", 400)},
        "finnhub_news":  {"api_key": os.environ.get("FINNHUB_API_KEY")},
        "wikipedia":     {"ticker_map": scout_cfg.get("wikipedia_ticker_map", {})},
    }


def run(config: dict, *, demo: bool, today: date) -> int:
    scout_cfg = config.get("scout", {})

    # Autonomous daily push is feature-flagged OFF by default (see spec
    # 2026-06-06). The interactive bot is the primary driver; flip
    # scout.daily_push.enabled to true to re-arm the daily report. Demo always
    # runs (it's the offline smoke path).
    if not demo and not scout_cfg.get("daily_push", {}).get("enabled", False):
        print("scout: daily_push disabled (scout.daily_push.enabled=false); nothing to do")
        return 0

    # Honour the config cache block (enabled/path/ttl) on the scout path too — it calls
    # run_harness directly, so without this the operator's kill-switch / TTL tuning in
    # config.yaml would be silently ignored (the lazy default would use hardcoded TTLs).
    # Demo is offline (mock source), so the cache is disabled there.
    from ..cache import configure_default_cache
    cache_cfg = config.get("cache", {})
    configure_default_cache(
        enabled=(not demo) and cache_cfg.get("enabled", True),
        path=cache_cfg.get("path"),
        ttls=cache_cfg.get("ttl"),
    )

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
        # Emit a SignalStatus for each configured-but-disabled signal so the
        # coverage line in the report shows them (e.g. "quiver ✗ (disabled)").
        for cfg_key, sig_val in sig_cfg.items():
            if not sig_val.get("enabled") and cfg_key in _KNOWN_SIGNAL_KEYS:
                statuses.append(SignalStatus(cfg_key, False, "disabled"))

    discovery = [s for s in signals if getattr(s, "is_discovery", False)]
    for s in discovery:
        # Polite cooldown: after a Yahoo WAF block we skip the endpoint entirely (zero
        # requests) for the rest of the day to protect the IP's reputation.
        if s.name == "yahoo_screener" and not demo and state.yahoo_blocked_on(session):
            until = state.yahoo_blocked_until()
            statuses.append(SignalStatus(s.name, False, f"skipped: WAF cooldown through {until}"))
            continue
        # FIX 2: guard the discovery body so one failing signal can't abort the whole run.
        try:
            ems = s.scan(session)
            emissions.extend(ems)
            # Persist a rest-of-day cooldown so later runs make zero Yahoo requests.
            # Best-effort: if _save() raises it's caught below and the block is simply
            # re-discovered next run (one extra single-request bail, never a spam loop).
            if getattr(s, "waf_blocked", False) and not demo:
                state.mark_yahoo_blocked(session)
            ran, detail = s.available()
            statuses.append(SignalStatus(s.name, ran, detail))
            # weight by config: map signal prefix back to its config key
            cfg_key = {"yahoo_screener": "yahoo_screener", "edgar_form4": "edgar_form4",
                       "mock": "yahoo_screener"}.get(s.name, s.name)
            w = sig_cfg.get(cfg_key, {}).get("weight", 1.0)
            for e in ems:
                weights_by_signal[e.signal] = w
        except Exception as exc:  # noqa: BLE001
            ems = []
            statuses.append(SignalStatus(s.name, False, redact_secrets(str(exc))))
            continue

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
    assessments: dict[str, dict] = {}
    researched: list[str] = []
    notes: list[str] = []
    if not demo:
        briefs, assessments, researched, note = _research_phase(cards, config, scout_cfg)
        if note:
            notes.append(note)

    manifest = RunManifest(
        session=session, signals=statuses, raw=raw, after_dedup=after_dedup,
        after_prefilter=after_prefilter, screened=len(cards), dropped_for_budget=dropped,
        researched=researched, notes=notes)

    # 4a. Demo: print the GLANCE text and stop — never touches Pillow / network.
    if demo:
        from .report import render_message
        print(render_message(cards, manifest, briefs))
        return 0

    # 4b. Live: build artifacts, deliver, persist.
    from .notify import TelegramNotifier, deliver
    from .report import build_report
    rep_cfg = scout_cfg.get("report", {})
    artifacts = build_report(cards, manifest, assessments=assessments)
    caption = _caption(manifest, cards, rep_cfg.get("caption_top_n", 3))

    notifier = TelegramNotifier()
    result = deliver(notifier,
                     png=artifacts.png if rep_cfg.get("chart", True) else None,
                     html=artifacts.html if rep_cfg.get("attach_html", True) else None,
                     text=artifacts.text, caption=caption,
                     session=session.isoformat())
    if not result.configured:
        print(artifacts.text)  # journal fallback
    if result.configured and not result.all_ok:
        manifest.notes.append("telegram delivery failed (configured)")
    _persist(scout_cfg, manifest, artifacts)
    state.mark_run_completed(session)
    state.record_screened([c.ticker for c in cards], session)
    if result.configured and not result.all_ok:
        return 2
    return 0


def _research_phase(
    cards,
    config,
    scout_cfg,
    *,
    require_passed=True,
    top_n=None,
    _is_available=None,
    _enrich=None,
) -> tuple[dict, dict, list, str | None]:
    """Guardrailed auto-research: kill-switch, auth probe, hard cap, phase budget.

    Returns (briefs, assessments, researched, note): briefs is dict[ticker, one_line_str],
    assessments is dict[ticker, full QualitativeAssessment record], researched is
    list[ticker], note is str|None.

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
        return {}, {}, [], "research skipped: kill-switch"
    if _is_available is None or _enrich is None:
        try:
            from ..research import enrich as _en
            from ..research import is_available as _ia
            _is_available = _is_available or _ia
            _enrich = _enrich or _en
        except Exception:  # noqa: BLE001
            return {}, {}, [], "research skipped: layer unavailable"
    if not _is_available():
        return {}, {}, [], "research skipped: claude CLI / edgartools not available"
    n = top_n if top_n is not None else scout_cfg.get("research_top_n", 3)
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
        future = pool.submit(_enrich, cards, config, top_n=n, refresh=False,
                             require_passed=require_passed)
        try:
            results = future.result(timeout=budget_s)
            pool.shutdown(wait=False)
        except concurrent.futures.TimeoutError:
            pool.shutdown(wait=False, cancel_futures=True)
            return {}, {}, [], f"research skipped: phase budget {budget_s}s exceeded"
    except Exception as e:  # noqa: BLE001
        return {}, {}, [], f"research failed: {redact_secrets(str(e))}"

    briefs: dict[str, str] = {}
    assessments: dict[str, dict] = {}
    researched: list[str] = []
    for r in results:
        if r.skipped:
            continue
        researched.append(r.ticker)
        brief_text = r.synthesis if r.synthesis else _one_line_brief_from_file(r.brief_path)
        briefs[r.ticker] = brief_text[:200]
        rec = _assessment_record_from_file(r.brief_path)
        if rec:
            assessments[r.ticker] = rec
    return briefs, assessments, researched, None


def _assessment_record_from_file(brief_path) -> dict | None:
    """Read the full QualitativeAssessment record (JSON) report.write() saved next to the .md."""
    try:
        json_path = Path(str(brief_path).replace(".md", ".json"))
        return json.loads(json_path.read_text())
    except Exception:  # noqa: BLE001
        return None


def _one_line_brief_from_file(brief_path) -> str:
    """Read synthesis from the JSON record file that report.write() writes alongside the .md."""
    try:
        # report.write() writes <ticker>/<accession>.json as the JSON record
        json_path = Path(str(brief_path).replace(".md", ".json"))
        data = json.loads(json_path.read_text())
        # QualitativeAssessment fields in the JSON: 'synthesis' is the 2-3 sentence text
        return (data.get("synthesis")
                or (data.get("thesis") or {}).get("takeaway")
                or data.get("summary") or "")[:200]
    except Exception:  # noqa: BLE001
        return "brief generated"


def _caption(manifest, cards, top_n: int) -> str:
    ordered = sorted(cards, key=rank_key, reverse=True)
    top = " · ".join(f"{c.ticker} {c.composite:.0f}" for c in ordered[:top_n])
    return (f"Scout — {manifest.session.isoformat()}\nTop: {top}\n"
            f"{manifest.screened} screened from {manifest.raw} raw")[:1024]


def _persist(scout_cfg, manifest, artifacts) -> None:
    out_dir = Path(scout_cfg.get("artifact_dir", "scout")) / manifest.session.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(json.dumps(manifest.to_dict(), indent=2))
    (out_dir / "report.txt").write_text(artifacts.text)
    (out_dir / "report.html").write_text(artifacts.html)
    if artifacts.png is not None:
        (out_dir / "dashboard.png").write_bytes(artifacts.png)


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

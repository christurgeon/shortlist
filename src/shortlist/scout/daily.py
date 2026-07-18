"""Scout orchestrator + CLI entry point (shortlist-scout). See docs/AUTONOMOUS_SCOUT.md §3."""
from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import json
import os
import sys
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

from ..config import ConfigError, load_config
from ..env import load_env, redact_secrets
from ._caption import _caption
from .budget import select
from .calendar import last_session
from .firehose import cohort_events_from_emissions
from .funnel import aggregate, apply_veto, prefilter
from .models import Emission, RunManifest, SignalStatus
from .signals import build_signals
from .state import ScoutState

_DEFAULT_CONFIG = Path(__file__).parent.parent.parent.parent / "config.yaml"

FMP_RATIONED_NOTE = "Free-source screen — /deep for PEG + analyst targets."

# Persist boundary for the `validate` CLI (both the live and `--backfill` paths) so the
# digest SECTION can read provisional verdicts with NO network at digest time (design:
# docs/superpowers/plans/2026-07-05-digest-verdicts.md). Gitignored via `/scout/`.
VALIDATE_LATEST_PATH = "scout/validate-latest.json"


def digest_sources(base: list[str], include_fmp: bool) -> list[str]:
    """The daily-digest source chain. Rations FMP when ``include_fmp`` is False by
    dropping 'fmp' from the canonical ``deep_screen_sources``; otherwise returns a
    copy of ``base`` unchanged. Order-preserving; a no-op when 'fmp' is already
    absent. The bot's /screen and /deep do NOT use this — they keep the full chain."""
    if include_fmp:
        return list(base)
    return [s for s in base if s != "fmp"]


_DISCOVERY_SIGNAL_NAMES = {"yahoo_screener", "edgar_form4", "wsb_hype",
                           "edgar_activist_13d", "finra_short_interest", "edgar_8k",
                           "edgar_13f", "edgar_buyback", "edgar_13d_stake_increase"}
_BOOSTER_SIGNAL_NAMES   = {"finnhub_news", "wikipedia"}
# Config keys we know how to build a signal for. An enabled key not in here is
# ignored; a disabled key in here still gets a "✗ (disabled)" coverage line.
_KNOWN_SIGNAL_KEYS = _DISCOVERY_SIGNAL_NAMES | _BOOSTER_SIGNAL_NAMES | {"quiver"}


def _enabled_signal_names(scout_cfg: dict) -> list[str]:
    return [k for k, v in scout_cfg.get("signals", {}).items()
            if v.get("enabled") and k in _KNOWN_SIGNAL_KEYS]


def _signal_kwargs(scout_cfg: dict, last_finra_settlement: str | None = None,
                   eightk_seen: list[str] | None = None, *,
                   thirteenf_seen: list[str] | None = None,
                   buyback_seen: list[str] | None = None,
                   stake_increase_seen: list[str] | None = None,
                   stake_baselines: dict | None = None) -> dict[str, dict]:
    """Build per-signal constructor kwargs from config + env for live (non-demo) runs.

    `last_finra_settlement` (from ScoutState) lets the short-interest signal emit only on a
    newer FINRA cycle (the bi-monthly cadence guard). `eightk_seen` / `buyback_seen` /
    `stake_increase_seen` (from ScoutState) are the 8-K / buyback / stake-increase
    originators' rolling accession dedup across the walk-back overlap; `thirteenf_seen` is
    the 13F originator's processed-filing dedup; `stake_baselines` is the stake-increase
    originator's pair->last-known-% map. All passed as keyword args (the positional list
    is already unwieldy)."""
    wsb = scout_cfg.get("wsb_hype", {})
    act = scout_cfg.get("activist_13d", {})
    si = scout_cfg.get("short_interest", {})
    ek = scout_cfg.get("eightk", {})
    tf = scout_cfg.get("thirteenf", {})
    bb = scout_cfg.get("buyback", {})
    sti = act.get("stake_increase", {})
    return {
        "edgar_form4":   {"max_filings": scout_cfg.get("edgar_index_daily_cap", 400)},
        "finnhub_news":  {"api_key": os.environ.get("FINNHUB_API_KEY")},
        "wikipedia":     {"ticker_map": scout_cfg.get("wikipedia_ticker_map", {})},
        "wsb_hype":      {"min_mentions": wsb.get("min_mentions", 30),
                          "min_mention_delta_pct": wsb.get("min_mention_delta_pct", 0.5),
                          "top_n": wsb.get("top_n", 15),
                          "deny_list": wsb.get("deny_list", [])},
        "edgar_activist_13d": {"identity": os.environ.get("SEC_IDENTITY"),
                               "max_filings": act.get("daily_cap", 300),
                               "drop_spacs": act.get("drop_spacs", True),
                               "drop_affiliates": act.get("drop_affiliates", True),
                               "marquee_boost": act.get("marquee_boost", 0.2)},
        "finra_short_interest": {"last_settlement": last_finra_settlement,
                                 "min_jump_pct": si.get("min_jump_pct", 0.25),
                                 "min_dtc": si.get("min_dtc", 3.0),
                                 "max_dtc": si.get("max_dtc", 10.0),
                                 "max_prior_dtc": si.get("max_prior_dtc", 10.0),
                                 "min_avg_daily_volume": si.get("min_avg_daily_volume", 100_000.0),
                                 "min_prev_short_shares": si.get("min_prev_short_shares", 50_000.0),
                                 "deny_list": si.get("deny_list", []),
                                 "top_n": si.get("top_n", 10)},
        "edgar_8k": {"identity": os.environ.get("SEC_IDENTITY"),
                     "item_sets": ek.get("item_sets", [["1.01", "3.03"]]),
                     "deny_list": ek.get("deny_list", []),
                     "drop_spacs": ek.get("drop_spacs", True),
                     "daily_cap": ek.get("daily_cap", 6),
                     "seen_accessions": eightk_seen or []},
        "edgar_13f": {"identity": os.environ.get("SEC_IDENTITY"),
                      "funds": tf.get("funds", []),
                      "min_position_pct": tf.get("min_position_pct", 0.005),
                      "full_strength_pct": tf.get("full_strength_pct", 0.05),
                      "max_filings_per_day": tf.get("max_filings_per_day", 3),
                      "top_n": tf.get("top_n", 10),
                      "deny_list": tf.get("deny_list", []),
                      "seen_accessions": thirteenf_seen or []},
        "edgar_buyback": {"identity": os.environ.get("SEC_IDENTITY"),
                          "phrases": bb.get("phrases"),   # None => buyback.DEFAULT_PHRASES
                          "deny_list": bb.get("deny_list", []),
                          "drop_spacs": bb.get("drop_spacs", True),
                          "daily_cap": bb.get("daily_cap", 6),
                          "seen_accessions": buyback_seen or []},
        "edgar_13d_stake_increase": {
            "identity": os.environ.get("SEC_IDENTITY"),
            "max_filings": sti.get("daily_cap", 300),
            "min_increase_pp": sti.get("min_increase_pp"),   # None => stake.MIN_INCREASE_PP
            "max_prior_fetches": sti.get("max_prior_fetches", 10),
            "drop_spacs": act.get("drop_spacs", True),
            "drop_affiliates": act.get("drop_affiliates", True),
            "seen_accessions": stake_increase_seen or [],
            "baselines": stake_baselines or {}},
    }


def _build_scoreboard(state, session: date, picks_cfg: dict) -> list[dict]:
    """Prior-picks scoreboard: for each recent pick, return-since-selection vs SPY from a
    fresh keyless Yahoo chart series (split-safe). Bounded by scoreboard_max; per-name
    failure-isolated; never raises (returns [] on any failure). Uses the chart endpoint
    (VPS-safe), not the WAF-blocked screener."""
    import asyncio

    lookback = picks_cfg.get("scoreboard_lookback_days", 120)
    cap = picks_cfg.get("scoreboard_max", 10)
    prior = state.recent_picks(session, lookback)[:cap]   # newest sessions first
    if not prior:
        return []

    # Re-resolve each pick's CIK -> CURRENT ticker so a symbol reassigned within the
    # lookback window doesn't fetch the wrong company's prices (spec §14 #16). Falls back
    # to the stored ticker when the CIK is absent/unresolvable. Day-cached, keyless.
    from .cik_tickers import load_cik_to_ticker, resolve_ticker
    identity = os.environ.get("SEC_IDENTITY") or "shortlist-scout turgechr@duck.com"
    cik_index = load_cik_to_ticker(identity)

    def _current_ticker(p: dict) -> str:
        return resolve_ticker(p.get("cik"), cik_index) or p["ticker"]

    async def _run() -> list[dict]:
        from ..data.sources import YahooSource, _closes_from_chart, _dates_from_chart
        from .picks import pick_performance
        src = YahooSource()
        try:
            spy_raw = await src._get_chart("SPY")
            spy_series = list(zip(_dates_from_chart(spy_raw), _closes_from_chart(spy_raw), strict=False))
            rows: list[dict] = []
            for p in prior:
                try:
                    raw = await src._get_chart(_current_ticker(p))
                    series = list(zip(_dates_from_chart(raw), _closes_from_chart(raw), strict=False))
                    perf = pick_performance(p, series, spy_series)
                    perf["evidence"] = p.get("evidence", "")
                    rows.append(perf)
                except Exception:  # noqa: BLE001 — one bad quote must not sink the scoreboard
                    continue
            return rows
        finally:
            await src.aclose()

    try:
        rows = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 — scoreboard is best-effort, never blocks the run
        print(f"scout: scoreboard skipped ({redact_secrets(str(exc))})", file=sys.stderr)
        return []
    # Best performers first; rows with no excess (missing SPY/quote) sort last.
    rows.sort(key=lambda r: (r.get("excess") is not None, r.get("excess") or 0.0), reverse=True)
    return rows


def _log_firehose(state, emissions, session, scout_cfg) -> list:
    """Best-effort: record the pre-scorer discovery emissions to the raw-signal firehose.
    Config-gated by scout.firehose.enabled; a failure NEVER aborts the run (mirrors the
    mark_yahoo_blocked best-effort convention).

    Returns the CohortEvents actually persisted this call (post-cap truncation; `[]` when
    disabled or the write raised) — so a caller that marks downstream state as "logged"
    (the 8-K negative-veto sweep's `eightk_neg_logged`) can mark ONLY what was actually
    recorded, never a capped-out or failed-write event (FIX: exactly-once under
    truncation/failure)."""
    fh_cfg = (scout_cfg or {}).get("firehose", {})
    if not fh_cfg.get("enabled"):
        return []
    try:
        events = cohort_events_from_emissions(emissions, session)
        cap = fh_cfg.get("max_events_per_run", 200)  # 0/None => no cap (use enabled:false to disable)
        if cap and len(events) > cap:
            events = events[:cap]
        state.record_firehose(events, session)
        return events
    except Exception as exc:  # noqa: BLE001 — best-effort, never abort the scout run
        import warnings
        warnings.warn(f"scout: firehose logging failed (non-fatal): {exc}", stacklevel=2)
        return []


def _negative_veto_sweep(state, scout_cfg: dict, session: date) -> tuple[dict, list[str]]:
    """The negative-item 8-K veto sweep (spec 2026-07-07 §4): fetch the EFTS window the
    state hasn't finalized yet (bounded cold-start: at most `lookback_days`; the day cache
    is shared with the originator, so the two halves cost ONE fetch), extract negative-item
    matches (eightk.negative_events_from_rows — broad by design), log NEW matches to the
    firehose as their own signal (edgar:8k_negative, accession-deduped across the re-swept
    lag window), and update the pruned ScoutState veto map + swept-through cursor.

    Returns (veto_map, notes). Gated by scout.eightk.negative_veto.enabled — absent or
    disabled returns ({}, []) with ZERO fetches (byte-identical funnel). Never raises: a
    failed sweep returns the STALE map plus a loud note (screening unprotected must not be
    silent). The cursor lags EFTS_LAG_DAYS so late-indexed filings still veto."""
    nv = (scout_cfg.get("eightk") or {}).get("negative_veto") or {}
    if not nv.get("enabled"):
        return {}, []
    lookback = int(nv.get("lookback_days", 30))

    def _active(m: dict) -> dict:
        out = {}
        for t, rec in m.items():
            try:
                if (session - date.fromisoformat(str(rec.get("last_date")))).days < lookback:
                    out[t] = rec
            except (TypeError, ValueError):
                continue
        return out

    cursor = state.eightk_negative_swept_through()
    earliest = session - timedelta(days=lookback - 1)
    start = earliest
    if cursor:
        with contextlib.suppress(ValueError):
            start = max(earliest, date.fromisoformat(str(cursor)) + timedelta(days=1))
    try:
        from ..data import efts
        from .cik_tickers import load_cik_to_ticker, resolve_ticker
        from .eightk import NEGATIVE_SIGNAL, STRENGTH, negative_events_from_rows
        identity = os.environ.get("SEC_IDENTITY") or "shortlist-scout turgechr@duck.com"
        rows = (efts.fetch_eightk_window(start, session, identity=identity)
                if start <= session else [])
        if rows is None:
            raise RuntimeError(f"EFTS sweep failed for {start.isoformat()}:{session.isoformat()}")
        index = load_cik_to_ticker(identity)
        recs = negative_events_from_rows(
            rows, resolve_ticker_fn=lambda cik: resolve_ticker(cik, index))
        logged = set(state.eightk_neg_logged())
        fresh = [r for r in recs if r["adsh"] not in logged]
        if fresh and (scout_cfg.get("firehose") or {}).get("enabled"):
            ems = [Emission(r["ticker"], NEGATIVE_SIGNAL, STRENGTH,
                            f"8-K items {'+'.join(r['items'])} filed {r['file_date']}",
                            is_discovery=False, cik=r["cik"],
                            meta={"adsh": r["adsh"], "items": r["items"],
                                  "file_date": r["file_date"]})
                   for r in fresh]
            # Mark ONLY the accessions the firehose layer actually persisted (post-cap
            # truncation, or NONE if the write raised) — the rest stay "fresh" and simply
            # retry on the next sweep. Safe: this ledger gates ONLY the firehose
            # re-logging dedup, never the veto map itself (state.update_eightk_negative
            # below runs unconditionally off `recs`, not `fresh`).
            recorded = _log_firehose(state, ems, session, scout_cfg)
            recorded_adshes = [e.meta.get("adsh") for e in recorded if e.meta.get("adsh")]
            if recorded_adshes:
                state.add_eightk_neg_logged(recorded_adshes)
        swept = (session - timedelta(days=efts.EFTS_LAG_DAYS)).isoformat()
        state.update_eightk_negative(recs, swept_through=swept, on=session,
                                     lookback_days=lookback)
        return _active(state.eightk_negative_map()), []
    except Exception as exc:  # noqa: BLE001 — degrade LOUDLY: stale protection, never a crash
        note = (f"8-K veto sweep FAILED — screening with STALE negative-8-K state "
                f"(swept through {cursor or 'never'}): {redact_secrets(str(exc))}")
        return _active(state.eightk_negative_map()), [note]


def _veto_notes(state, vetoed, veto_map: dict) -> list[str]:
    """Named manifest notes for this run's vetoed candidates, deduped by
    (ticker, accession) via the ScoutState ledger — a vetoed name never enters
    screened-cooldown, so without the ledger the same note would re-fire daily for up to
    lookback_days. A NEWER accession for the same ticker notes afresh."""
    notes: list[str] = []
    for c in vetoed:
        rec = veto_map.get(c.ticker.upper()) or {}
        adsh = str(rec.get("adsh") or "")
        if state.eightk_veto_note_seen(c.ticker, adsh):
            continue
        items = "+".join(rec.get("items") or []) or "?"
        notes.append(f"VETOED: {c.ticker} — 8-K item {items} "
                     f"filed {rec.get('last_date', '?')}")
        state.mark_eightk_veto_noted(c.ticker, adsh)
    return notes


def _load_validation_digest(config: dict, *, today: date,
                            path: str = VALIDATE_LATEST_PATH) -> dict | None:
    """Read `scout/validate-latest.json` for the digest's display-only validation SECTION
    -- NO network call at digest time (design: docs/superpowers/plans/2026-07-05-digest-
    verdicts.md). Defensive: a missing file, malformed JSON/shape, an empty verdicts list,
    or a stale `as_of` (older than `scout.validate.latest_max_age_days`, default 14) all
    return None so the section is simply absent -- never a crash, and never a stale
    scoreboard silently displayed as current. `today` is the run's calendar date (the same
    `today` passed into `run()`), not the trading `session` -- staleness is a wall-clock
    question."""
    try:
        max_age = ((config.get("scout") or {}).get("validate") or {}).get("latest_max_age_days", 14)
        data = json.loads(Path(path).read_text())
        if not isinstance(data, dict):
            return None
        verdicts = data.get("verdicts")
        if not isinstance(verdicts, list) or not verdicts:
            return None
        as_of = date.fromisoformat(str(data.get("as_of")))
        if (today - as_of).days > max_age:
            return None
        return data
    except Exception:  # noqa: BLE001 — best-effort read, never blocks the digest
        return None


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
        kwargs_by_name = _signal_kwargs(scout_cfg, state.finra_last_settlement(),
                                        state.eightk_seen_accessions(),
                                        thirteenf_seen=state.thirteenf_seen_accessions(),
                                        buyback_seen=state.buyback_seen_accessions(),
                                        stake_increase_seen=state.stake_increase_seen_accessions(),
                                        stake_baselines=state.stake_baselines())
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
            # Record the FINRA cycle just processed so the same bi-monthly cohort isn't
            # re-surfaced daily until a newer settlement publishes (the cadence guard).
            if s.name == "finra_short_interest" and not demo and getattr(s, "settlement", None):
                state.set_finra_cycle(s.settlement)
            # Persist the 8-K accessions surfaced this run so the walk-back overlap
            # (session-2..session) doesn't re-emit them on the next runs.
            if s.name == "edgar_8k" and not demo and getattr(s, "new_accessions", None):
                state.add_eightk_accessions(s.new_accessions)
            # Persist the 13F filings PROCESSED this run (even empty-diff ones) so a fund's
            # already-diffed latest 13F-HR isn't re-downloaded daily; carry-over filings stay
            # unseen and surface on a later session.
            if s.name == "edgar_13f" and not demo and getattr(s, "processed_accessions", None):
                state.add_thirteenf_accessions(s.processed_accessions)
            # Persist the buyback accessions surfaced this run so the walk-back overlap
            # (session-2..session) doesn't re-emit them on the next runs.
            if s.name == "edgar_buyback" and not demo and getattr(s, "new_accessions", None):
                state.add_buyback_accessions(s.new_accessions)
            # Persist the stake-increase originator's surfaced accessions (walk-back overlap
            # dedup) + updated pair baselines (newest parsed % per campaign pair) so the
            # cold-start prior-fetch budget isn't re-spent on already-known pairs.
            if s.name == "edgar_13d_stake_increase" and not demo:
                if getattr(s, "new_accessions", None):
                    state.add_stake_increase_accessions(s.new_accessions)
                if getattr(s, "baseline_updates", None):
                    state.update_stake_baselines(s.baseline_updates)
            ran, detail = s.available()
            statuses.append(SignalStatus(s.name, ran, detail))
            # weight by config: map signal name back to its config key. Names are
            # identity except the demo "mock" signal, which borrows yahoo_screener's weight.
            cfg_key = "yahoo_screener" if s.name == "mock" else s.name
            w = sig_cfg.get(cfg_key, {}).get("weight", 1.0)
            for e in ems:
                weights_by_signal[e.signal] = w
        except Exception as exc:  # noqa: BLE001
            ems = []
            statuses.append(SignalStatus(s.name, False, redact_secrets(str(exc))))
            continue

    raw = len(emissions)
    _log_firehose(state, emissions, session, scout_cfg)
    cands = aggregate(emissions, weights_by_signal)
    after_dedup = len(cands)

    kept = prefilter(
        cands,
        in_cooldown=lambda t: state.in_cooldown(t, on=session,
                                                cooldown_days=scout_cfg.get("cooldown_days", 7)),
        is_held=state.is_held)
    after_prefilter = len(kept)

    # 1a. Negative-item 8-K veto (scout.eightk.negative_veto, ships ON): drop names with a
    # fresh negative-item 8-K LOUDLY, between prefilter and select, before they burn one of
    # the ~10 FMP deep-screen slots. Demo is offline (no sweep); an absent/disabled block
    # returns ({}, []) so the funnel below is byte-identical to the pre-feature path.
    veto_map, veto_notes = ({}, []) if demo else _negative_veto_sweep(state, scout_cfg, session)
    kept, vetoed_cands = apply_veto(kept, veto_map)
    veto_notes.extend(_veto_notes(state, vetoed_cands, veto_map))

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

    # 2. Deep-screen via the harness scorer (mock source offline in --demo)
    from ..data.macro import fetch_macro
    from ..screen import run_harness
    base_sources = scout_cfg.get("deep_screen_sources", ["yahoo", "fmp", "finnhub", "edgar"])
    include_fmp = scout_cfg.get("daily_push", {}).get("include_fmp", True)  # default True = back-compat
    sources = ["mock"] if demo else digest_sources(base_sources, include_fmp)
    macro = None if demo else fetch_macro(config)  # --demo is offline: no FRED call
    cards = run_harness([c.ticker for c in chosen], sources, config, macro=macro)

    # 3. Auto-research (guardrailed) — skipped in demo, and skippable by config so the
    # daily push can run as a screen+gate+rank digest (surface names to pass to /deep)
    # without the daily Claude/FMP-research burn. Default True preserves the legacy push.
    briefs: dict[str, str] = {}
    assessments: dict[str, dict] = {}
    researched: list[str] = []
    notes: list[str] = list(veto_notes)
    # Caveat only when FMP was actually rationed from a chain that had it — never a
    # misleading note on a run that used FMP (or never had it).
    if not demo and not include_fmp and "fmp" in base_sources:
        notes.append(FMP_RATIONED_NOTE)
    research_enabled = scout_cfg.get("daily_push", {}).get("research", True)
    if not demo and research_enabled:
        briefs, assessments, researched, note, skipped = _research_phase(
            cards, config, scout_cfg)
        if note:
            notes.append(note)
        for t, why in skipped.items():
            notes.append(f"{t}: research unavailable ({why})")
    elif not demo:
        notes.append("research disabled by config (scout.daily_push.research=false)")

    manifest = RunManifest(
        session=session, signals=statuses, raw=raw, after_dedup=after_dedup,
        after_prefilter=after_prefilter, screened=len(cards), dropped_for_budget=dropped,
        researched=researched, notes=notes, vetoed=len(vetoed_cands))

    # 4a. Demo: print the GLANCE text and stop — never touches Pillow / network.
    if demo:
        from .report import render_message
        print(render_message(cards, manifest, briefs))
        return 0

    # 4b. Live: build artifacts, deliver, persist.
    from .notify import TelegramNotifier, deliver
    from .report import build_report
    rep_cfg = scout_cfg.get("report", {})
    # Prior-picks scoreboard (read BEFORE recording today's picks) so the digest shows how
    # past selections performed vs SPY — the over-time tracking deliverable. Failure-isolated.
    picks_cfg = scout_cfg.get("picks", {})
    prior_picks = (_build_scoreboard(state, session, picks_cfg)
                   if picks_cfg.get("enabled", True) else [])
    validation = _load_validation_digest(config, today=today)
    artifacts = build_report(cards, manifest, assessments=assessments, macro=macro,
                             prior_picks=prior_picks, validation=validation)
    caption = _caption(manifest, cards, rep_cfg.get("caption_top_n", 3))

    notifier = TelegramNotifier()
    result = deliver(notifier,
                     png=artifacts.png if rep_cfg.get("chart", True) else None,
                     html=artifacts.html if rep_cfg.get("attach_html", True) else None,
                     text=artifacts.text, caption=caption,
                     session=session.isoformat())
    if not result.configured:
        print(artifacts.text)  # journal fallback
        print(f"scout: telegram not configured; journaled {session} report "
              f"({len(cards)} names)", file=sys.stderr)
    elif result.all_ok:
        print(f"scout: delivered {session} report to telegram ({len(cards)} names)",
              file=sys.stderr)
    else:
        manifest.notes.append("telegram delivery failed (configured)")
        print(f"scout: telegram delivery failed for {session} "
              f"({', '.join(result.failures)})", file=sys.stderr)
    _persist(scout_cfg, manifest, artifacts)
    state.mark_run_completed(session)
    state.record_screened([c.ticker for c in cards], session)
    # Record this session's picks (gated ones too — for raw-signal measurement) so future
    # scoreboards can track them. Idempotent upsert; never blocks delivery.
    if picks_cfg.get("enabled", True):
        try:
            from .picks import pick_from_card
            cand_by_ticker = {c.ticker: c for c in chosen}
            recs = [pick_from_card(card, cand_by_ticker[card.ticker], session)
                    for card in cards if card.ticker in cand_by_ticker]
            if recs:
                state.record_picks(recs, session)
        except Exception as exc:  # noqa: BLE001 — ledger write must not crash a delivered run
            print(f"scout: recording picks failed ({redact_secrets(str(exc))})", file=sys.stderr)
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
) -> tuple[dict, dict, list, str | None, dict]:
    """Guardrailed auto-research: kill-switch, auth probe, hard cap, phase budget.

    Returns (briefs, assessments, researched, note, skipped): briefs is
    dict[ticker, one_line_str], assessments is dict[ticker, full QualitativeAssessment
    record], researched is list[ticker], note is str|None, and skipped is
    dict[ticker, reason_str] — the per-ticker reasons enrich() declined a name
    (e.g. "no 10-K", "assessment failed", "filing error: …"), surfaced so the
    report/bot can explain gaps instead of silently omitting research.

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
        return {}, {}, [], "research skipped: kill-switch", {}
    if _is_available is None or _enrich is None:
        try:
            from ..research import enrich as _en
            from ..research import is_available as _ia
            _is_available = _is_available or _ia
            _enrich = _enrich or _en
        except Exception:  # noqa: BLE001
            return {}, {}, [], "research skipped: layer unavailable", {}
    if not _is_available():
        return {}, {}, [], "research skipped: claude CLI / edgartools not available", {}
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
        except concurrent.futures.TimeoutError:
            return {}, {}, [], f"research skipped: phase budget {budget_s}s exceeded", {}
        finally:
            # Always shut down — on success, timeout, OR an exception raised inside
            # _enrich() (which escapes to the outer handler). wait=False so we never
            # block on a hung thread (see the IMPORTANT note above re: `with`).
            pool.shutdown(wait=False)
    except Exception as e:  # noqa: BLE001
        return {}, {}, [], f"research failed: {redact_secrets(str(e))}", {}

    briefs: dict[str, str] = {}
    assessments: dict[str, dict] = {}
    researched: list[str] = []
    skipped: dict[str, str] = {}
    for r in results:
        if r.skipped:
            skipped[r.ticker] = r.skipped
            continue
        researched.append(r.ticker)
        brief_text = r.synthesis if r.synthesis else _one_line_brief_from_file(r.brief_path)
        briefs[r.ticker] = brief_text[:200]
        rec = _assessment_record_from_file(r.brief_path)
        if rec:
            assessments[r.ticker] = rec
    return briefs, assessments, researched, None, skipped


def _record_json(brief_path) -> dict | None:
    """Load the JSON record report.write() saves alongside the .md (<ticker>/<accession>.json).

    Suffix-safe (.with_suffix avoids replacing any ".md" substring); returns None on any
    read/parse failure.
    """
    try:
        return json.loads(Path(brief_path).with_suffix(".json").read_text())
    except Exception:  # noqa: BLE001
        return None


def _assessment_record_from_file(brief_path) -> dict | None:
    """Read the full QualitativeAssessment record (JSON) report.write() saved next to the .md."""
    return _record_json(brief_path)


def _one_line_brief_from_file(brief_path) -> str:
    """Read synthesis from the JSON record file that report.write() writes alongside the .md."""
    data = _record_json(brief_path)
    if data is None:
        return "brief generated"
    # QualitativeAssessment fields in the JSON: 'synthesis' is the 2-3 sentence text
    return (data.get("synthesis")
            or (data.get("thesis") or {}).get("takeaway")
            or data.get("summary") or "")[:200]


def _persist(scout_cfg, manifest, artifacts) -> None:
    out_dir = Path(scout_cfg.get("artifact_dir", "scout")) / manifest.session.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(json.dumps(manifest.to_dict(), indent=2))
    (out_dir / "report.txt").write_text(artifacts.text)
    (out_dir / "report.html").write_text(artifacts.html)
    if artifacts.png is not None:
        (out_dir / "dashboard.png").write_bytes(artifacts.png)


# --- Phase-1 signal-validation evaluator (`shortlist-scout validate`) ---
# Offline, read-only diagnostic: measures each firehose signal's forward-return quality
# and emits a KILL/HOLD/INSUFFICIENT verdict (never PROMOTE). See scout/validate.py,
# scout/preregister.py, scout/factors.py, docs/superpowers/specs/
# 2026-07-01-signal-validation-harness-backfill-design.md.

_DELISTING_BAND = (None, -0.30, -0.55, -1.00)


async def _fetch_validate_data(tickers: list[str], cache_dir: str, today_iso: str):
    """Fetch the FF3 monthly factors + per-ticker price history needed by the evaluator.
    Both fetches are day-cached; per-ticker failures are isolated (a bad symbol doesn't
    sink the whole eval — it simply has no history, so its events stay non-measurable)."""
    import httpx

    from ..backtest.prices import _UA, fetch_history
    from .factors import fetch_ff3_monthly

    async with httpx.AsyncClient(headers={"User-Agent": _UA}, timeout=30.0) as client:
        try:
            ff3 = await fetch_ff3_monthly(client, cache_dir=cache_dir, today=today_iso)
        except Exception as exc:  # noqa: BLE001
            print(f"scout validate: FF3 factor fetch failed ({redact_secrets(str(exc))})",
                  file=sys.stderr)
            ff3 = {}
        hists = {}
        for tk in tickers:
            try:
                hists[tk] = await fetch_history(tk, client, cache_dir=cache_dir, today=today_iso)
            except Exception as exc:  # noqa: BLE001
                print(f"scout validate: price fetch failed for {tk} "
                     f"({redact_secrets(str(exc))})", file=sys.stderr)
    return ff3, hists


def _slug_for_signal(signal: str) -> str:
    """Firehose events carry colon-separated signal names (e.g. 'edgar:activist_13d');
    pre-registration files are named with underscores (edgar_activist_13d.yaml)."""
    return signal.replace(":", "_")


def _parse_prereg_date(x) -> date | None:
    """Defensively parse a pre-reg date field (a `date` object or ISO string). Returns None
    on a missing/malformed value -- back-compat with pre-reg files that predate a field
    (e.g. `verdict_as_of`, I1) rather than raising."""
    if x is None:
        return None
    try:
        return x if isinstance(x, date) else date.fromisoformat(str(x))
    except (ValueError, TypeError):
        return None


def _delisting_band_flip(evs, signal, k_months, hists, ff3, as_of, weighting="equal") -> bool:
    """Re-measure the cohort across the delisting-return sensitivity band (spec §6.6) and
    flag a flip when the FF3 alpha sign disagrees across band members that produce a
    computable alpha. A flip means the verdict is an artifact of the delisting assumption,
    not a robust conclusion -- decide() downgrades any HOLD to INSUFFICIENT on a flip.

    `use_event_delisting=False`: the band's whole point is to vary the delisting-return
    assumption and see if the verdict's sign holds up. A per-event CLASSIFIED terminal
    return (from the 13D backfill) would override the blanket band value and mask that
    variation -- the classifier shrinks the guard's bite but must not remove it (spec §6.6)."""
    from .validate import calendar_time_portfolio, ff3_alpha, measure_cohort

    signs: set[int] = set()
    for dr in _DELISTING_BAND:
        m = measure_cohort(evs, signal, k_months, hists, dr, as_of=as_of,
                           use_event_delisting=False)
        ctp = calendar_time_portfolio(m.events, k_months, weighting=weighting)
        alpha, _betas = ff3_alpha(ctp, ff3)
        if alpha is not None and alpha != 0:
            signs.add(1 if alpha > 0 else -1)
    return len(signs) > 1


def run_validate(config: dict, *, today: date, lookback_days: int,
                 events_override: list[dict] | None = None) -> list:
    """The `validate` eval flow: load the firehose cohort, group by signal, fetch FF3 +
    price history, measure/decide per signal (gated by pre-registration tamper-checks).
    Returns a list of `validate.SignalVerdict`. Read-only -- never writes config.yaml or
    scores/gates/ranks a live screen.

    `events_override`: when given (the `validate --backfill PATH` CLI path), these
    JSONL-loaded synthetic-cohort dicts are used INSTEAD of `ScoutState.firehose_events` --
    `ScoutState` is never constructed or read at all (live and synthetic events must never
    pool, M1). Every resulting verdict is labeled SYNTHETIC (below) so it reads as
    rank/KILL-only, not a live signal-quality verdict.
    """
    import asyncio

    from .preregister import load_prereg, verify_untampered
    from .validate import (
        SignalVerdict,
        calendar_time_portfolio,
        decide,
        double_sort,
        measure_cohort,
    )

    scout_cfg = config.get("scout", {})
    val_cfg = scout_cfg.get("validate", {})
    if events_override is not None:
        events = events_override
    else:
        state = ScoutState(Path(scout_cfg.get("state_path", "state/scout_state.json")))
        events = state.firehose_events(today, lookback_days)

    by_signal: dict[str, list[dict]] = {}
    for e in events:
        sig = e.get("signal")
        if sig:
            by_signal.setdefault(sig, []).append(e)

    if not by_signal:
        return []

    # Backfill sentinel tickers ("CIK:0001823575", "CIK:unknown-<acc>" -- see
    # scout/backfill.py) have no resolvable price history and would each fire a doomed
    # Yahoo fetch_history request (VPS WAF protection). The live firehose never emits
    # this convention, so skipping it here only affects synthetic backfill cohorts; the
    # events themselves stay in the cohort and simply have no history -> non-measurable.
    tickers = sorted({(e.get("ticker") or "").upper() for e in events
                      if e.get("ticker") and not str(e.get("ticker")).startswith("CIK:")})
    cache_dir = val_cfg.get("factor_cache_dir", ".cache/famafrench")
    ff3, hists = asyncio.run(_fetch_validate_data(tickers, cache_dir, today.isoformat()))

    repo_root = str(_DEFAULT_CONFIG.parent)
    verdicts: list[SignalVerdict] = []
    for signal in sorted(by_signal):
        evs = by_signal[signal]
        slug = _slug_for_signal(signal)
        try:
            # yaml.YAMLError is neither OSError nor ValueError, so a MALFORMED committed
            # pre-reg (not just a missing one) must be caught here too — otherwise one bad
            # YAML would abort the whole eval loop and starve every other signal of a
            # verdict. Keep the guard PER-SIGNAL (never around the loop) so a bad file
            # degrades only its own signal to INSUFFICIENT and the loop continues.
            prereg = load_prereg(slug, repo_root=repo_root)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            # Hard-error display path (M2 reviewer note): no measurement was ever run, so
            # honestly there's no immature/mature split to report -- every event in the
            # cohort is uncounted (n_events=len(evs), n_immature=0, n_selected=len(evs)),
            # never fabricated as measured.
            verdicts.append(SignalVerdict(
                signal=signal, verdict="INSUFFICIENT", ir=None, alpha_monthly=None,
                alpha_ci=None, effective_blocks=0, n_selected=len(evs), n_measurable=0,
                measurable_fraction=0.0, sensitivity_flip=False,
                notes=[f"pre-registration for slug '{slug}' missing or unparsable — "
                      f"cannot evaluate ({redact_secrets(str(exc))})"],
                n_immature=0, n_events=len(evs)))
            continue

        k_months = int(prereg.get("k_months", 12))
        weighting = prereg.get("weighting", "equal")
        primary_delisting_return = prereg.get("delisting_return")
        # I1 (v2 design): a run before the pre-registered canonical `verdict_as_of` is
        # permanently labeled INTERIM -- absent key -> None -> no label (back-compat with
        # prereg files that predate this field).
        verdict_as_of = _parse_prereg_date(prereg.get("verdict_as_of"))
        interim = verdict_as_of is not None and today < verdict_as_of
        measurement = measure_cohort(evs, signal, k_months, hists,
                                     primary_delisting_return, as_of=today)
        ctp_rows = calendar_time_portfolio(measurement.events, k_months, weighting=weighting)
        flip = _delisting_band_flip(evs, signal, k_months, hists, ff3, today, weighting)
        verdict = decide(measurement, ctp_rows, ff3, k_months, prereg,
                         sensitivity_flip=flip, cohort_type="raw")

        ok, reason = verify_untampered(slug, repo_root=repo_root, run_as_of=today)
        if not ok:
            verdict.notes.append(f"NOT PRE-REGISTERED: {reason}")
        if interim:
            verdict.notes.append(
                f"INTERIM — before registered verdict_as_of {verdict_as_of.isoformat()}")
        verdicts.append(verdict)

        # --- Scored-cohort verdict (design B2 + v2 §6/§8): does the scorer's composite +
        # gate improve on the raw/undifferentiated cohort? Gated on `scored_evs` being
        # non-empty so events with NO `composite` field at all (old-shaped JSONLs, or the
        # live firehose before backfill reconstruction) never manufacture a phantom second
        # verdict — back-compat.
        scored_evs = [e for e in evs
                     if e.get("composite") is not None and e.get("gated") is False]
        if scored_evs:
            scored_measurement = measure_cohort(scored_evs, signal, k_months, hists,
                                               primary_delisting_return, as_of=today)
            scored_ctp_rows = calendar_time_portfolio(scored_measurement.events, k_months,
                                                      weighting=weighting)
            scored_flip = _delisting_band_flip(scored_evs, signal, k_months, hists, ff3,
                                              today, weighting)
            scored_verdict = decide(scored_measurement, scored_ctp_rows, ff3, k_months,
                                   prereg, sensitivity_flip=scored_flip,
                                   cohort_type="scored_gated")
            scored_verdict.notes.append(
                "scored cohort reconstructed keylessly (no analyst/insider fields; SIC "
                "best-effort) — composites not comparable to live")

            # Double-sort over the GATE-AGNOSTIC composite-defined set (design B1 + v2 §2):
            # it tests the composite's ORDERING power, not the gate — a strict superset of
            # `scored_evs` whenever a composite-defined event was itself gated.
            ds_evs = [e for e in evs if e.get("composite") is not None]
            ds_measurement = measure_cohort(ds_evs, signal, k_months, hists,
                                           primary_delisting_return, as_of=today)
            ds_result = double_sort(
                ds_measurement.events, k_months, ff3,
                min_bucket_events=prereg.get("min_bucket_events", 5),
                min_independent_blocks=prereg.get("min_independent_blocks", 2),
                weighting=weighting)
            scored_verdict.double_sort = ds_result
            if ds_result is None:
                # v2 §2's "insufficient_blocks marker in the run log" — a coarse WHY so a
                # None double-sort never reads as "not attempted" vs "attempted and thin".
                scored_verdict.notes.append(
                    "double-sort: insufficient blocks or bucket size")

            if interim:
                scored_verdict.notes.append(
                    f"INTERIM — before registered verdict_as_of {verdict_as_of.isoformat()}")
            if not ok:
                scored_verdict.notes.append(f"NOT PRE-REGISTERED: {reason}")
            verdicts.append(scored_verdict)
    if events_override is not None:
        for v in verdicts:
            v.notes.append("SYNTHETIC backfill cohort — rank/KILL only (M1)")
    return verdicts


def _print_validate_table(verdicts: list) -> None:
    if not verdicts:
        print("scout validate: no firehose events in the lookback window")
        return
    header = (f"{'SIGNAL':<28}{'COHORT':<14}{'VERDICT':<14}{'IR':>8}{'ALPHA/mo':>10}"
             f"{'BLOCKS':>8}{'N_SEL':>7}{'N_MEAS':>8}{'FRAC':>7}{'FLIP':>6}")
    print(header)
    print("-" * len(header))
    for v in verdicts:
        ir = f"{v.ir:.2f}" if v.ir is not None else "-"
        alpha = f"{v.alpha_monthly:.4f}" if v.alpha_monthly is not None else "-"
        row = (f"{v.signal:<28}{v.cohort_type:<14}{v.verdict:<14}{ir:>8}{alpha:>10}"
              f"{v.effective_blocks:>8}{v.n_selected:>7}{v.n_measurable:>8}"
              f"{v.measurable_fraction:>7.2f}{'Y' if v.sensitivity_flip else 'N':>6}")
        # B2/I4: a young live cohort must read "0/0 (+350 immature)", never a bare "0/0" --
        # the immature count is display-only, appended past the fixed-width columns.
        n_immature = getattr(v, "n_immature", 0)
        if n_immature:
            row += f"  (+{n_immature} immature)"
        print(row)
        for note in v.notes:
            print(f"    - {note}")
        # Compact double-sort line (design v2 §8): high-vs-low composite spread stats.
        # Absence-when-attempted-but-thin is already covered by the "insufficient blocks
        # or bucket size" note above (v.notes), so this line only fires when double_sort
        # actually returned a result.
        if v.double_sort is not None:
            ds = v.double_sort
            ds_alpha = (f"{ds['spread_alpha_monthly']:.4f}"
                       if ds.get("spread_alpha_monthly") is not None else "-")
            ci = ds.get("spread_ci")
            ds_ci = f"[{ci[0]:.4f}, {ci[1]:.4f}]" if ci is not None else "-"
            print(f"    double-sort: spread α/mo={ds_alpha}, CI={ds_ci}, "
                 f"blocks={ds.get('effective_blocks')}, "
                 f"n={ds.get('n_high')}/{ds.get('n_low')}")
    print()
    # M1: the (mature-only, H2) framing documents the table's PERMANENT denominator
    # convention -- unconditional (not gated on this run happening to carry immature
    # events), so a reader of any single run never has to infer the convention from
    # whether it happened to be load-bearing this time.
    print("N_SEL/N_MEAS/FRAC above are mature-only (H2, not the raw backfill's pooled "
         "count) -- see scout backfill's own summary for the (all events, incl. "
         "immature) fraction.")
    print("Display / provisional / survivorship-accounted — not evidence, not advice. "
         "NEVER a PROMOTE signal.")


def _persist_validate_latest(verdicts: list, *, today: date, source: str,
                             path: str = VALIDATE_LATEST_PATH) -> None:
    """Write `verdicts` to `path` for the digest SECTION to read later (no network at
    digest time -- see docs/superpowers/plans/2026-07-05-digest-verdicts.md). Shape:
    `{"as_of": iso, "source": "live"|"backfill:<basename>", "verdicts": [asdict(v), ...]}`.

    `dataclasses.asdict(v)` turns `SignalVerdict.alpha_ci` (a tuple) into a list and
    `double_sort` (already a plain dict, itself sometimes carrying a tuple `spread_ci`)
    into nested plain containers -- `json.dumps` serializes tuples as JSON arrays natively,
    so the round-trip through `json.loads` is lossless except tuple-vs-list identity, which
    the reading side (a plain Section, never a dataclass) never cares about.

    Never raises: a write failure (missing perms, disk full, an unexpected non-serializable
    value) warns to stderr and must never change the CLI's exit code -- persistence is
    best-effort telemetry, not the CLI's job."""
    try:
        payload = {
            "as_of": today.isoformat(),
            "source": source,
            "verdicts": [asdict(v) for v in verdicts],
        }
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, default=str, indent=2))
    except Exception as exc:  # noqa: BLE001 — best-effort persistence, never blocks the CLI
        print(f"scout validate: failed to persist {path} ({redact_secrets(str(exc))})",
             file=sys.stderr)


def _run_validate_cli(config: dict, *, today: date, lookback_days: int | None,
                      as_json: bool, backfill_path: str | None = None) -> int:
    scout_cfg = config.get("scout", {})
    val_cfg = scout_cfg.get("validate", {})
    lb = lookback_days if lookback_days is not None else val_cfg.get("lookback_days", 365)
    events_override = None
    source = "live"
    if backfill_path:
        from .backfill import load_backfill_events
        events_override = load_backfill_events(backfill_path)
        source = f"backfill:{Path(backfill_path).name}"
    verdicts = run_validate(config, today=today, lookback_days=lb,
                            events_override=events_override)
    _persist_validate_latest(verdicts, today=today, source=source)
    if as_json:
        print(json.dumps([asdict(v) for v in verdicts], default=str, indent=2))
    else:
        _print_validate_table(verdicts)
    return 0


def _print_backfill_summary(summary: dict) -> None:
    print(f"scout backfill: {summary.get('out_path', '?')}")
    fraction_note = summary.get("fraction_note", "")
    print(f"  selected={summary.get('n_selected', 0)} "
         f"measurable={summary.get('n_measurable', 0)} "
         f"fraction={summary.get('fraction', 0.0):.2f} {fraction_note} "
         f"written_this_run={summary.get('written', 0)}")
    by_reason = summary.get("by_reason") or {}
    if by_reason:
        print("  non-measurable reasons: " +
             ", ".join(f"{k}={v}" for k, v in sorted(by_reason.items())))
    delist = summary.get("delisting_by_reason") or {}
    if delist:
        print("  delisting classifications: " +
             ", ".join(f"{k}={v}" for k, v in sorted(delist.items())))
    failed = summary.get("failed_chunks")
    if failed:
        print(f"  FAILED chunks (re-run to resume): {failed}")
    low_conf = summary.get("low_confidence")
    if low_conf:
        print(f"  low-confidence symbology resolutions: {low_conf}")
    print()
    print("Batch backfill (raw cohort) — synthetic, rank/KILL only. Not evidence, not advice.")


def _run_backfill_cli(config: dict, *, signal: str, start: date, end: date,
                      out_path: str | None, as_json: bool) -> int:
    """Run a signal's batch backfill and print its summary. Never raises here: a missing
    SEC_IDENTITY or an unsupported --signal is a clear, immediate error (exit 2), not a
    traceback. Dispatch is by name through the backfill module attribute so tests can
    monkeypatch `shortlist.scout.backfill.run_backfill_13d` etc."""
    runners = {"13d": "run_backfill_13d", "8k": "run_backfill_8k",
               "8k-neg": "run_backfill_8k_neg", "buyback": "run_backfill_buyback"}
    runner_name = runners.get(signal)
    if runner_name is None:
        print(f"scout backfill: unsupported --signal '{signal}' "
              f"(supported: {', '.join(runners)})", file=sys.stderr)
        return 2
    identity = os.environ.get("SEC_IDENTITY")
    if not identity:
        print("scout backfill: SEC_IDENTITY environment variable is required "
             "(SEC fair-access identification, e.g. 'name@example.com') — set it and retry",
             file=sys.stderr)
        return 2

    from . import backfill
    summary = getattr(backfill, runner_name)(config, start=start, end=end,
                                             identity=identity, out_path=out_path)
    if as_json:
        print(json.dumps(summary, default=str, indent=2))
    else:
        _print_backfill_summary(summary)
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    """Top-level parser with optional `validate`/`backfill` subcommands (`dest="subcommand"`,
    NOT required). The bare (no-subcommand) invocation keeps its historical flat flags
    (--demo/--config/--no-research) so `shortlist-scout` / `shortlist-scout --demo` parse
    and run the daily flow exactly as before the subparser refactor."""
    ap = argparse.ArgumentParser(prog="shortlist-scout",
                                 description="Autonomous candidate discovery + daily report.")
    ap.add_argument("--demo", action="store_true", help="offline run; print report to stdout")
    ap.add_argument("--config", default=str(_DEFAULT_CONFIG))
    ap.add_argument("--no-research", action="store_true", help="skip the Claude research phase")

    sub = ap.add_subparsers(dest="subcommand")
    vp = sub.add_parser(
        "validate",
        help="evaluate discovery-signal forward-return quality (Phase 1, offline/read-only)")
    vp.add_argument("--json", action="store_true", help="dump verdicts as JSON")
    vp.add_argument("--lookback-days", type=int, default=None,
                    help="firehose lookback window in days "
                         "(default: config scout.validate.lookback_days, else 365)")
    vp.add_argument("--backfill", default=None, metavar="PATH",
                    help="evaluate a batch-backfill JSONL cohort (shortlist-scout backfill "
                         "output) INSTEAD of the live ScoutState firehose; verdicts are labeled "
                         "SYNTHETIC (rank/KILL only, M1)")

    bp = sub.add_parser(
        "backfill",
        help="batch-backfill a discovery signal's historical cohort (offline, writes JSONL)")
    bp.add_argument("--signal", choices=["13d", "8k", "8k-neg", "buyback"], required=True,
                    help="which signal to backfill ('13d' = edgar:activist_13d, "
                         "'8k' = edgar:8k positive pocket, '8k-neg' = edgar:8k_negative "
                         "veto cohort, 'buyback' = edgar:buyback_auth authorizations)")
    bp.add_argument("--start", required=True, type=date.fromisoformat,
                    help="ISO start date, e.g. 2022-08-01")
    bp.add_argument("--end", required=True, type=date.fromisoformat,
                    help="ISO end date, e.g. 2022-08-31")
    bp.add_argument("--out", default=None, metavar="PATH",
                    help="output JSONL path (default: scout.backfill.out_dir/<signal>-<start>-<end>.jsonl)")
    bp.add_argument("--json", action="store_true", help="dump the run summary as JSON")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_arg_parser()
    args = ap.parse_args(argv)

    load_env()
    if args.no_research:
        os.environ["SCOUT_NO_RESEARCH"] = "1"
    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"scout: {e}", file=sys.stderr)
        return 2
    today = datetime.now(timezone.utc).date()

    subcommand = getattr(args, "subcommand", None)
    if subcommand == "validate":
        try:
            return _run_validate_cli(config, today=today, lookback_days=args.lookback_days,
                                     as_json=args.json, backfill_path=args.backfill)
        except Exception as e:  # noqa: BLE001
            print(f"scout: validate failed: {redact_secrets(str(e))}", file=sys.stderr)
            return 1

    if subcommand == "backfill":
        try:
            return _run_backfill_cli(config, signal=args.signal, start=args.start,
                                     end=args.end, out_path=args.out, as_json=args.json)
        except Exception as e:  # noqa: BLE001
            print(f"scout: backfill failed: {redact_secrets(str(e))}", file=sys.stderr)
            return 1

    try:
        return run(config, demo=args.demo, today=today)
    except Exception as e:  # noqa: BLE001
        print(f"scout: run failed: {redact_secrets(str(e))}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

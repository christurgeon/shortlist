"""Batch backfill coordinator (13d / 8k / 8k-neg; raw + scored cohort) — spec §6.5/§8, P2 Plan 3 / Plan 3b.

Walk history (backtest.edgar_history) -> resolve PiT ticker (symbology, CIK-keyed) -> assemble
CohortEvents (origin="backfill") -> OPTIONALLY reconstruct a PiT score() (scout.backfill
`score_event`/`merge_metrics`, gated by `scout.backfill.score_events`, default true) -> measure
survivorship-accounted returns with per-event CLASSIFIED delisting terminals (delisting.py) ->
append to an idempotent gitignored JSONL under scout/backfill/ (NEVER ScoutState — synthetic and
live must not pool, M1).

Entry timing (F12): event_date = first trading day STRICTLY AFTER the filing date (13Ds land
after close; the filing-day close would capture the announcement pop, not the drift).
Filter ordering (the measurable-fraction denominator): signal-definition filters (initial-13D,
SPAC, affiliate) EXCLUDE records entirely; everything after them is selected, and resolution/
price/delisting failures only mark events non-measurable (counted, never dropped).
Serial by design: one Symbology (archive.org throttle), one PriceHistory in memory at a time
(VPS: ~1.9 GB RAM shared with a live trading bot). Score reconstruction reuses that single
PriceHistory + one shared SPY PriceHistory fetched once per run, plus one companyfacts payload
in memory at a time (sec-throttled, month-cached — shared with the XBRL backtest).
"""
from __future__ import annotations

import calendar as _cal
import json
import time
import warnings
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from ..backtest.prices import PriceHistory
from ..env import redact_secrets
from .buyback import SIGNAL as SIGNAL_BUYBACK
from .buyback import STRENGTH as _BUYBACK_STRENGTH
from .calendar import is_trading_day
from .delisting import classify_delisting, normalize_items, terminal_price
from .edgar_index import _is_real_ticker, activist_stakes_from_records
from .eightk import DEFAULT_ITEM_SETS as _EIGHTK_ITEM_SETS
from .eightk import NEGATIVE_SIGNAL as SIGNAL_8K_NEG
from .eightk import SIGNAL as SIGNAL_8K
from .eightk import STRENGTH as _EIGHTK_STRENGTH
from .eightk import _junk_suffix, match_item_sets, match_negative
from .firehose import CohortEvent
from .quality import is_affiliate_filing, is_spac_or_shell
from .stake import SIGNAL as SIGNAL_STAKE

SIGNAL = "edgar:activist_13d"


def fetch_history_sync(ticker: str, *, identity: str, today: date,
                       cache_dir: str = ".cache/yahoo", _transport=None) -> Optional[PriceHistory]:
    """Sync bridge over the async backtest.prices.fetch_history (it needs an AsyncClient).
    One asyncio.run + one short-lived AsyncClient per call — serial by design (VPS).
    Never raises -> None on failure (warned, redacted).

    CAVEAT: `asyncio.run` raises RuntimeError if called from inside an already-running
    event loop -- this degrades the same way as any other failure here (warn + None), it
    does NOT propagate. CLI/batch backfill contexts are synchronous (no surrounding loop),
    so this is a non-issue today; it would only bite a future caller that invokes this from
    async code."""
    import asyncio

    import httpx

    from ..backtest.prices import fetch_history

    async def _one():
        async with httpx.AsyncClient(timeout=30.0, headers={"User-Agent": identity},
                                     transport=_transport) as ac:
            return await fetch_history(ticker, ac, cache_dir=cache_dir,
                                       today=today.isoformat())
    try:
        return asyncio.run(_one())
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"backfill: price fetch failed for {ticker}: "
                      f"{redact_secrets(str(exc))}", stacklevel=2)
        return None


def _normalize_cik10(cik) -> Optional[str]:
    """int or (zero-padded or not) str CIK -> the 10-digit zero-padded string
    `backtest.xbrl.fetch_companyfacts`/`build_cik_index` expect.
    Returns None (never raises) on a malformed/None cik."""
    try:
        return f"{int(cik):010d}"
    except (TypeError, ValueError):
        return None


def fetch_companyfacts_sync(cik, *, identity: str, cache_dir: str = ".cache/sec_xbrl",
                           month: str, _transport=None) -> Optional[dict]:
    """Sync bridge over the async backtest.xbrl.fetch_companyfacts (needs an AsyncClient) —
    the fetch_history_sync pattern: read the disk cache FIRST (sync, disk-only — a warm
    cache never spawns an event loop); on miss, one asyncio.run + short-lived AsyncClient.

    Cache `month` is the FETCH-time month (caller passes `today.strftime("%Y-%m")`), NEVER
    the event's as_of month (spec v2 §1) — the companyfacts payload is latest-always (PiT
    truncation is extract_panel's job), so a fetch-month key SHARES the cache with the XBRL
    backtest and lets a stale `_NO_US_GAAP` negative marker refresh monthly.

    Never raises -> None on a genuine no-us-gaap payload (not warned — an expected miss,
    like an IFRS/20-F issuer) or on any fetch failure (warned, redacted)."""
    from ..backtest.xbrl import fetch_companyfacts, read_companyfacts_cache

    cik10 = _normalize_cik10(cik)
    if cik10 is None:
        warnings.warn(f"backfill: companyfacts — malformed CIK {cik!r}", stacklevel=2)
        return None
    cached = read_companyfacts_cache(cik10, cache_dir=cache_dir, month=month)
    if cached is not None:
        return cached

    import asyncio

    import httpx

    async def _one():
        async with httpx.AsyncClient(timeout=30.0, headers={"User-Agent": identity},
                                     transport=_transport) as ac:
            return await fetch_companyfacts(cik10, ac, cache_dir=cache_dir, month=month)
    try:
        return asyncio.run(_one())
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"backfill: companyfacts fetch failed for CIK{cik10}: "
                      f"{redact_secrets(str(exc))}", stacklevel=2)
        return None


_SIC_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"


def fetch_sic_sync(cik, *, identity: str, cache_dir: str = ".cache/sec_xbrl",
                   month: str, _transport=None) -> Optional[str]:
    """SEC submissions endpoint -> SIC code for a CIK (spec v2 §3 — SIC is fetched, not
    skipped, so `score()`'s normal sector masking applies to a reconstructed event; an
    unknown-bucket approximation would over-gate financials whose leverage/FCF gates are
    structurally undefined). Month-cached to `{cache_dir}/SIC{cik10}-{month}.json`,
    INCLUDING a cached null for a 200-with-no-sic (so a real negative isn't refetched
    within the month) — but a NETWORK failure is warned and NOT cached (re-attempted on
    the next call). Never raises -> None on failure."""
    import httpx

    cik10 = _normalize_cik10(cik)
    if cik10 is None:
        warnings.warn(f"backfill: SIC — malformed CIK {cik!r}", stacklevel=2)
        return None
    cp = Path(cache_dir) / f"SIC{cik10}-{month}.json"
    try:
        if cp.exists():
            cached = json.loads(cp.read_text())
            if isinstance(cached, dict) and "sic" in cached:
                return cached["sic"]
    except (ValueError, OSError):
        pass  # corrupt cache -> refetch

    try:
        with httpx.Client(timeout=30.0, headers={"User-Agent": identity},
                          transport=_transport) as client:
            resp = client.get(_SIC_URL.format(cik10=cik10))
            resp.raise_for_status()
            raw = resp.json()
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"backfill: SIC fetch failed for CIK{cik10}: "
                      f"{redact_secrets(str(exc))}", stacklevel=2)
        return None  # network failure -> NOT cached, re-attempted next call

    sic = raw.get("sic") or None  # empty string ("") -> None
    try:
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps({"sic": sic}))
    except Exception:
        pass  # cache write failure is non-fatal
    return sic


def free_disk_gb(path: str = ".") -> float:
    """Free disk (GB) on the filesystem holding `path`, walking up to the nearest EXISTING
    ancestor (the cache dir may not exist yet on a first run)."""
    import shutil
    p = Path(path).resolve()
    while not p.exists() and p.parent != p:
        p = p.parent
    return shutil.disk_usage(p).free / 1e9


def next_trading_day(d: date) -> date:
    """First trading day STRICTLY after d (F12 entry shift)."""
    nxt = d + timedelta(days=1)
    while not is_trading_day(nxt):
        nxt += timedelta(days=1)
    return nxt


def _key(cik_or_acc: str, filing_date: date) -> str:
    return f"{SIGNAL}|{cik_or_acc}|{filing_date.isoformat()}"


def assemble_events(records_by_day: dict, resolve_ticker, *, drop_spacs: bool = True,
                    drop_affiliates: bool = True, marquee_boost: float = 0.2) -> list[CohortEvent]:
    """Records -> selected CohortEvents. Excluded (SPAC/affiliate) never appear; unresolved
    tickers appear as CIK: sentinels so the evaluator counts them non-measurable (§6.1)."""
    events: list[CohortEvent] = []
    for fday, recs in records_by_day.items():
        entry = next_trading_day(fday)
        resolved_rows: list[dict] = []
        sentinel_ciks: dict[str, dict] = {}
        for r in recs:
            if drop_spacs and is_spac_or_shell(r.get("subject_name") or ""):
                continue                          # excluded by signal definition (not selected)
            if drop_affiliates and is_affiliate_filing(r.get("activist") or "",
                                                       r.get("subject_name") or ""):
                continue
            cik = r.get("cik")
            if cik is None:                       # header failed upstream: selected, sentinel
                acc = r.get("accession") or "no-accession"
                events.append(CohortEvent(
                    signal=SIGNAL, ticker=f"CIK:unknown-{acc}", cik=None, event_date=entry,
                    as_of_price=None, strength=0.7, gated=None, composite=None,
                    origin="backfill",
                    meta={"filing_date": fday.isoformat(), "key": _key(acc, fday),
                          "non_measurable_hint": "header_failed"}))
                continue
            tkr = resolve_ticker(cik, fday)       # PiT at FILING date (pre-entry information)
            if tkr is None:                       # selected but unresolvable: sentinel
                sentinel_ciks.setdefault(cik, r)
                continue
            # Defensive guard: resolved-returned ticker that fails _is_real_ticker (e.g. "N/A",
            # numeric-only) routes to sentinel path instead of silently vanishing inside
            # activist_stakes_from_records. Reliance on Task-1's _dedup_by_accession ensures
            # sentinel CIK uniqueness even when the same header generates multiple bad tickers.
            norm = _is_real_ticker(tkr)
            if not norm:
                sentinel_ciks.setdefault(cik, r)
                continue
            resolved_rows.append({**r, "ticker": norm})
        for cik in sentinel_ciks:
            events.append(CohortEvent(
                signal=SIGNAL, ticker=f"CIK:{cik}", cik=cik, event_date=entry,
                as_of_price=None, strength=0.7, gated=None, composite=None, origin="backfill",
                meta={"filing_date": fday.isoformat(), "key": _key(cik, fday),
                      "non_measurable_hint": "unresolved_ticker"}))
        if resolved_rows:
            emissions = activist_stakes_from_records(
                resolved_rows, drop_spacs=drop_spacs, drop_affiliates=drop_affiliates,
                marquee_boost=marquee_boost)
            for em in emissions:
                events.append(CohortEvent(
                    signal=SIGNAL, ticker=em.ticker, cik=em.cik, event_date=entry,
                    as_of_price=None, strength=em.strength, gated=None, composite=None,
                    origin="backfill",
                    meta={"filing_date": fday.isoformat(),
                          "key": _key(em.cik or em.ticker, fday)}))
    return events


def assemble_eightk_events(rows: list[dict], resolve_ticker, *, signal: str,
                           negative: bool = False, item_sets=None,
                           drop_spacs: bool = True) -> list[CohortEvent]:
    """Normalized EFTS rows (data/efts.py shape) -> selected CohortEvents for the 8-K legs.

    Mirrors the LIVE aggregators' signal definition (scout/eightk.py) with the backfill's
    denominator discipline layered on (the 13D assemble_events pattern): signal-definition
    filters EXCLUDE records entirely (file_type != "8-K" — the 8-K/A root_forms leak —
    then item mismatch; positive leg only: SIC-6770 blank check, SPAC/shell display-NAME,
    5th-letter junk suffix), while RESOLUTION failures become selected `CIK:<cik>`
    sentinels the evaluator counts non-measurable — never silently dropped.

    The 8-K FILER IS the subject (no header fetch — the row's cik is the target, unlike
    13Ds). `resolve_ticker(cik, filing_date)` is PiT at the FILING date; entry is
    next_trading_day(filing_date) (F12 — strictly-after-filing neutralizes the
    announcement-day move). The negative leg applies NO quality drops (broad by design —
    a SPAC bankruptcy still belongs in the veto cohort). The EFTS row's first SIC rides
    into meta["sic"] so the score loop skips a submissions fetch. One event per filer per
    day; a row with an unparseable file_date is skipped (never raises)."""
    sets_ = [tuple(s) for s in (item_sets or _EIGHTK_ITEM_SETS)]
    events: list[CohortEvent] = []
    seen_acc: set[str] = set()
    seen_cik_day: set[tuple[str, str]] = set()
    for r in rows:
        if (r.get("file_type") or "") != "8-K":
            continue                          # amendment leak — excluded, FIRST
        adsh = r.get("adsh") or ""
        if not adsh or adsh in seen_acc:
            continue
        seen_acc.add(adsh)
        items = normalize_items(r.get("items"))
        matched = match_negative(items) if negative else match_item_sets(items, sets_)
        if matched is None:
            continue
        if not negative:
            if "6770" in (r.get("sics") or []):
                continue                      # blank-check SIC — signal definition
            names = r.get("display_names") or []
            if drop_spacs and names and is_spac_or_shell(str(names[0])):
                continue                      # name check ONLY — never a ticker source
        cik = r.get("cik")
        try:
            fday = date.fromisoformat(str(r.get("file_date")))
        except (TypeError, ValueError):
            continue                          # unusable row (no date to key on)
        if (cik, fday.isoformat()) in seen_cik_day:
            continue                          # one event per filer per day
        seen_cik_day.add((cik, fday.isoformat()))
        sic = (r.get("sics") or [None])[0]
        meta = {"filing_date": fday.isoformat(), "adsh": adsh, "items": matched,
                "key": f"{signal}|{cik}|{fday.isoformat()}"}
        if sic:
            meta["sic"] = str(sic)
        entry = next_trading_day(fday)
        tkr = resolve_ticker(cik, fday)       # PiT at FILING date
        norm = _is_real_ticker(tkr) if tkr else None
        if not norm:                          # selected but unresolvable: sentinel
            events.append(CohortEvent(
                signal=signal, ticker=f"CIK:{cik}", cik=cik, event_date=entry,
                as_of_price=None, strength=_EIGHTK_STRENGTH, gated=None, composite=None,
                origin="backfill",
                meta={**meta, "non_measurable_hint": "unresolved_ticker"}))
            continue
        if not negative and _junk_suffix(norm):
            continue                          # security-class suffix — signal definition
        events.append(CohortEvent(
            signal=signal, ticker=norm, cik=cik, event_date=entry, as_of_price=None,
            strength=_EIGHTK_STRENGTH, gated=None, composite=None, origin="backfill",
            meta=meta))
    return events


def assemble_buyback_events(rows: list[dict], resolve_ticker, *, signal: str,
                            drop_spacs: bool = True) -> list[CohortEvent]:
    """Phrase-tagged EFTS rows (data/efts.fetch_phrase_window shape) -> selected CohortEvents
    for the buyback authorization leg. Mirrors assemble_eightk_events MINUS the item-set match
    (the phrase match already happened at fetch time; each row carries its `phrase`): file_type
    != "8-K" drop (amendment leak) -> accession dedup -> SIC-6770/SPAC-name drops -> resolution
    (unresolvable -> selected CIK: sentinel, never dropped) -> 5th-letter junk suffix. The
    FILER IS the subject (no header fetch — the row's cik is the target); resolve_ticker is PiT
    at the FILING date, entry is next_trading_day(filing) (F12). One event per filer per day."""
    events: list[CohortEvent] = []
    seen_acc: set[str] = set()
    seen_cik_day: set[tuple[str, str]] = set()
    for r in rows:
        if (r.get("file_type") or "") != "8-K":
            continue                          # amendment leak — excluded, FIRST
        adsh = r.get("adsh") or ""
        if not adsh or adsh in seen_acc:
            continue                          # cross-phrase accession dedup
        seen_acc.add(adsh)
        if "6770" in (r.get("sics") or []):
            continue                          # blank-check SIC — signal definition
        names = r.get("display_names") or []
        if drop_spacs and names and is_spac_or_shell(str(names[0])):
            continue                          # name check ONLY — never a ticker source
        cik = r.get("cik")
        try:
            fday = date.fromisoformat(str(r.get("file_date")))
        except (TypeError, ValueError):
            continue                          # unusable row (no date to key on)
        if (cik, fday.isoformat()) in seen_cik_day:
            continue                          # one event per filer per day
        seen_cik_day.add((cik, fday.isoformat()))
        sic = (r.get("sics") or [None])[0]
        meta = {"filing_date": fday.isoformat(), "adsh": adsh,
                "items": [str(i) for i in (r.get("items") or [])],
                "phrase": str(r.get("phrase") or ""),
                "key": f"{signal}|{cik}|{fday.isoformat()}"}
        if sic:
            meta["sic"] = str(sic)
        entry = next_trading_day(fday)
        tkr = resolve_ticker(cik, fday)       # PiT at FILING date
        norm = _is_real_ticker(tkr) if tkr else None
        if not norm:                          # selected but unresolvable: sentinel
            events.append(CohortEvent(
                signal=signal, ticker=f"CIK:{cik}", cik=cik, event_date=entry,
                as_of_price=None, strength=_BUYBACK_STRENGTH, gated=None, composite=None,
                origin="backfill",
                meta={**meta, "non_measurable_hint": "unresolved_ticker"}))
            continue
        if _junk_suffix(norm):
            continue                          # security-class suffix — signal definition
        events.append(CohortEvent(
            signal=signal, ticker=norm, cik=cik, event_date=entry, as_of_price=None,
            strength=_BUYBACK_STRENGTH, gated=None, composite=None, origin="backfill",
            meta=meta))
    return events


def _horizon_end(d: date, months: int) -> date:
    y, m = d.year + (d.month - 1 + months) // 12, (d.month - 1 + months) % 12 + 1
    return date(y, m, min(d.day, _cal.monthrange(y, m)[1]))


def measure_event(ev: CohortEvent, hist, k_months: int, *, today: date,
                  fetch_delisting_records, window_days: int = 365) -> CohortEvent:
    """Survivorship-accounted measurability + per-event CLASSIFIED delisting terminal (§6.1/§6.6).
    Enriches ev.meta in place semantics via dataclasses.replace; never raises."""
    meta = dict(ev.meta)

    def _done(measurable: bool, reason=None, **extra) -> CohortEvent:
        meta["measurable"] = measurable
        meta["non_measurable_reason"] = reason
        meta.update(extra)
        return replace(ev, meta=meta)

    if ev.ticker.startswith("CIK:"):
        return _done(False, meta.get("non_measurable_hint") or "unresolved_ticker")
    if hist is None or not getattr(hist, "dates", None):
        return _done(False, "no_price_series")
    entry = hist.close_asof(ev.event_date)
    ev = replace(ev, as_of_price=entry)
    horizon = _horizon_end(ev.event_date, k_months)
    if horizon > today:
        return _done(False, "immature")
    if entry is None or entry <= 0:
        return _done(False, "no_entry_price")
    if hist.forward_return(ev.event_date, k_months) is not None:
        return _done(True)                                    # priced path — the common case
    if hist.dates[-1] >= horizon:
        return _done(False, "trading_gap")                    # R-A1: hole, not a delisting
    if not ev.cik:
        return _done(False, "no_cik")
    recs = fetch_delisting_records(ev.cik)
    if recs is None:
        return _done(False, "delisting_fetch_failed")
    verdict = classify_delisting(recs, window_days=window_days)
    if verdict is None:
        return _done(False, "series_ends_no_form25")
    if verdict.terminal_return is None:
        return _done(False, "delisting_unclassified",
                     delisting_reason=verdict.reason,
                     delisting_date=verdict.delisting_date.isoformat())
    if verdict.delisting_date < ev.event_date:
        return _done(False, "delisted_before_event",
                     delisting_reason=verdict.reason,
                     delisting_date=verdict.delisting_date.isoformat())
    term = terminal_price(verdict, hist.dates, hist.closes)
    if term is None:
        return _done(False, "no_terminal_price", delisting_reason=verdict.reason)
    return _done(True, None,
                 delisting_event_return=term / entry - 1.0,
                 delisting_reason=verdict.reason,
                 delisting_date=verdict.delisting_date.isoformat())


def load_backfill_events(path: str) -> list[dict]:
    """JSONL -> list of CohortEvent-shaped dicts. Missing file -> []; bad lines warned+skipped."""
    p = Path(path)
    if not p.exists():
        return []
    rows: list[dict] = []
    bad = 0
    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            bad += 1
    if bad:
        warnings.warn(f"backfill: skipped {bad} malformed line(s) in {path}", stacklevel=2)
    return rows


def append_events(path: str, events: list) -> int:
    """Append events whose meta['key'] is new (idempotent resume). Returns count written."""
    existing = {r.get("meta", {}).get("key") for r in load_backfill_events(path)}
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with p.open("a") as fh:
        for ev in events:
            k = ev.meta.get("key")
            if k in existing:
                continue
            fh.write(json.dumps(ev.to_dict()) + "\n")
            existing.add(k)
            written += 1
    return written


def summarize(rows: list[dict]) -> dict:
    by_reason: dict = {}
    by_vintage: dict = {}
    delist: dict = {}
    n_meas = 0
    n_scored = 0
    for r in rows:
        meta = r.get("meta") or {}
        measurable = bool(meta.get("measurable"))
        n_meas += measurable
        if r.get("composite") is not None:
            n_scored += 1
        try:
            year = int(str(r.get("event_date", ""))[:4])
        except ValueError:
            year = 0
        b = by_vintage.setdefault(year, {"selected": 0, "measurable": 0})
        b["selected"] += 1
        b["measurable"] += measurable
        reason = meta.get("non_measurable_reason")
        if not measurable and reason:
            by_reason[reason] = by_reason.get(reason, 0) + 1
        dr = meta.get("delisting_reason")
        if dr:
            delist[dr] = delist.get(dr, 0) + 1
    n = len(rows)
    return {"n_selected": n, "n_measurable": n_meas,
            "fraction": (n_meas / n) if n else 0.0,
            # M1 (v2 design): this fraction pools EVERY row in the raw batch JSONL,
            # immature events included -- distinct from validate.py's mature-only H2
            # denominator (measurable_fraction()), so the two surfaces can't be misread as
            # contradicting. No math change, annotation only.
            "fraction_note": "(all events, incl. immature)",
            "n_scored": n_scored,
            "scored_fraction": (n_scored / n) if n else 0.0,
            "by_reason": by_reason, "by_vintage": by_vintage,
            "delisting_by_reason": delist}


def _month_chunks(start: date, end: date) -> list[tuple[date, date]]:
    chunks = []
    d = start
    while d <= end:
        last = date(d.year, d.month, _cal.monthrange(d.year, d.month)[1])
        chunks.append((d, min(last, end)))
        d = last + timedelta(days=1)
    return chunks


def _parse_prereg_date(x) -> Optional[date]:
    """Parse a date object or ISO string, returning None on malformed input."""
    try:
        return x if isinstance(x, date) else date.fromisoformat(str(x))
    except (ValueError, TypeError):
        return None


def _activist_fetch_factory(bf: dict, today: date):
    from ..backtest.edgar_history import fetch_activist_window
    return fetch_activist_window


def _efts_fetch_factory(bf: dict, today: date):
    """Window fetcher for the 8-K legs, with the SAME call shape as
    fetch_activist_window (so the injected-`_fetch_window` seam is signal-agnostic)."""
    cache_dir = bf.get("efts_cache_dir", ".cache/efts")

    def _fetch(c_start, c_end, identity, *, throttle_s, max_records):
        from ..data.efts import fetch_eightk_window
        rows = fetch_eightk_window(c_start, c_end, identity=identity,
                                   cache_dir=cache_dir, today=today,
                                   throttle_s=throttle_s)
        if rows is not None and max_records is not None and len(rows) > max_records:
            warnings.warn(f"backfill: EFTS window {c_start}:{c_end} truncated at "
                          f"max_records={max_records} — narrow the range", stacklevel=2)
            rows = rows[:max_records]
        return rows
    return _fetch


def _buyback_fetch_factory(bf: dict, today: date):
    """Window fetcher for the buyback leg — the phrase-query analogue of _efts_fetch_factory,
    same call shape as fetch_activist_window (signal-agnostic injected-`_fetch_window` seam).
    The cohort runs UNCAPPED/UNDENIED over the live phrase set (daily_cap/deny_list are
    live-only knobs the backfill never applies — the 8-K precedent)."""
    from .buyback import DEFAULT_PHRASES
    cache_dir = bf.get("buyback_cache_dir", ".cache/efts_buyback")
    phrases = bf.get("buyback_phrases") or list(DEFAULT_PHRASES)

    def _fetch(c_start, c_end, identity, *, throttle_s, max_records):
        from ..data.efts import fetch_phrase_window
        rows = fetch_phrase_window(phrases, c_start, c_end, identity=identity,
                                   cache_dir=cache_dir, today=today, throttle_s=throttle_s)
        if rows is not None and max_records is not None and len(rows) > max_records:
            warnings.warn(f"backfill: buyback window {c_start}:{c_end} truncated at "
                          f"max_records={max_records} — narrow the range", stacklevel=2)
            rows = rows[:max_records]
        return rows
    return _fetch


def _amendment_fetch_factory(bf: dict, today: date):
    """Window fetcher for the 13D/A stake-increase leg — the fetch_activist_window
    analogue that also returns amendments + doc-fetched stake_pct (same injected-
    `_fetch_window` call shape)."""
    def _fetch(c_start, c_end, identity, *, throttle_s, max_records):
        from ..backtest.edgar_history import fetch_amendment_window
        return fetch_amendment_window(c_start, c_end, identity, throttle_s=throttle_s,
                                      max_records=max_records)
    return _fetch


def _assemble_13d_a_factory(bf: dict, today: date):
    """Run-level stateful assembler: chunks arrive oldest-first, so an in-window initial
    13D (or earlier amendment) seeds the pair baseline before later amendments diff
    against it. First-sighting amendments SEED AND NEVER EMIT (registered rule); parse
    abstention is a selection exclusion, never a sentinel. min_increase_pp is the CODE
    constant (stake.MIN_INCREASE_PP) — config tunes live only."""
    from .quality import is_13d_amendment
    from .stake import MIN_INCREASE_PP, STRENGTH, pair_key
    from .stake import SIGNAL as STAKE_SIGNAL
    baselines: dict[str, dict] = {}

    def _assemble(recs, resolve_ticker):
        events: list[CohortEvent] = []
        for r in sorted(recs, key=lambda x: x["filing_date"]):
            subj = r.get("subject_name") or ""
            if is_spac_or_shell(subj):
                continue
            if is_affiliate_filing(r.get("activist") or "", subj):
                continue
            pk = pair_key(r.get("filer_cik"), r.get("cik"))
            pct = r.get("stake_pct")
            if pk is None or pct is None:
                continue                                   # abstention: excluded
            fday = r["filing_date"]
            if not is_13d_amendment(r.get("form") or ""):
                baselines[pk] = {"pct": pct, "date": fday.isoformat()}
                continue                                   # initials seed only
            prior = baselines.get(pk, {}).get("pct")
            baselines[pk] = {"pct": pct, "date": fday.isoformat()}
            if prior is None or pct - prior < MIN_INCREASE_PP:
                continue                                   # seed-only / immaterial
            entry = next_trading_day(fday)
            cik = r.get("cik")
            tkr = resolve_ticker(cik, fday) if cik else None
            norm = _is_real_ticker(tkr) if tkr else ""
            meta = {"filing_date": fday.isoformat(), "prior_pct": prior, "new_pct": pct,
                    "key": f"{STAKE_SIGNAL}|{r.get('accession') or cik}|{fday.isoformat()}"}
            if not norm:                                   # SELECTED but non-measurable
                events.append(CohortEvent(
                    signal=STAKE_SIGNAL, ticker=f"CIK:{cik}", cik=cik, event_date=entry,
                    as_of_price=None, strength=STRENGTH, gated=None, composite=None,
                    origin="backfill",
                    meta={**meta, "non_measurable_hint": "unresolved_ticker"}))
                continue
            events.append(CohortEvent(
                signal=STAKE_SIGNAL, ticker=norm, cik=cik, event_date=entry,
                as_of_price=None, strength=STRENGTH, gated=None, composite=None,
                origin="backfill", meta=meta))
        return events

    return _assemble


def _assemble_13d(recs: list[dict], resolve_ticker) -> list[CohortEvent]:
    return assemble_events(group_by_day_records(recs), resolve_ticker)


def _assemble_8k(rows: list[dict], resolve_ticker) -> list[CohortEvent]:
    return assemble_eightk_events(rows, resolve_ticker, signal=SIGNAL_8K)


def _assemble_8k_neg(rows: list[dict], resolve_ticker) -> list[CohortEvent]:
    return assemble_eightk_events(rows, resolve_ticker, signal=SIGNAL_8K_NEG,
                                  negative=True)


def _assemble_buyback(rows: list[dict], resolve_ticker) -> list[CohortEvent]:
    return assemble_buyback_events(rows, resolve_ticker, signal=SIGNAL_BUYBACK)


# Per-signal backfill specs (spec 2026-07-07 §5): CLI name -> {firehose signal string,
# prereg slug (Task 6 YAML filenames), default window fetcher factory, assembler}.
# The "13d" row reproduces the pre-generalization coordinator byte-for-byte.
# "13d-a" (Task 8) uses "assemble_factory" instead of "assemble" -- a run-level stateful
# closure (chronological pair-baseline map across chunks), not a pure per-chunk function.
_BACKFILL_SPECS: dict[str, dict] = {
    "13d": {"signal": SIGNAL, "slug": "edgar_activist_13d",
            "fetch_factory": _activist_fetch_factory, "assemble": _assemble_13d},
    "8k": {"signal": SIGNAL_8K, "slug": "edgar_8k",
           "fetch_factory": _efts_fetch_factory, "assemble": _assemble_8k},
    "8k-neg": {"signal": SIGNAL_8K_NEG, "slug": "edgar_8k_negative",
               "fetch_factory": _efts_fetch_factory, "assemble": _assemble_8k_neg},
    "buyback": {"signal": SIGNAL_BUYBACK, "slug": "edgar_buyback_auth",
                "fetch_factory": _buyback_fetch_factory, "assemble": _assemble_buyback},
    "13d-a": {"signal": SIGNAL_STAKE, "slug": "edgar_13d_stake_increase",
              "fetch_factory": _amendment_fetch_factory,
              "assemble_factory": _assemble_13d_a_factory},
}


def run_backfill(config: dict, *, signal_key: str, start: date, end: date, identity: str,
                 today: Optional[date] = None, out_path: Optional[str] = None,
                 _fetch_window=None, _symbology=None, _fetch_history=None,
                 _fetch_delisting=None, _fetch_facts=None, _fetch_sic=None,
                 _prereg=None, _free_gb=None) -> dict:
    """Generic batch backfill: walk -> assemble -> OPTIONALLY score (PiT reconstruction) ->
    measure -> idempotent JSONL. Serial + rate-limited by design (runs on the production
    VPS). `signal_key` selects a _BACKFILL_SPECS row ("13d" | "8k" | "8k-neg" | "buyback" |
    "13d-a"); the 13d row is byte-identical to the pre-generalization coordinator (pinned by
    tests/test_scout_backfill.py passing unchanged). Returns the run summary.

    `scout.backfill.score_events` (default true) gates the scored-cohort reconstruction
    (design A4): false reproduces the byte-identical raw-only JSONL (gated/composite
    stay None, nothing else about a written row changes)."""
    spec = _BACKFILL_SPECS[signal_key]
    bf = (config.get("scout") or {}).get("backfill") or {}
    sec_throttle = float(bf.get("sec_throttle_s", 0.2))
    yh_throttle = float(bf.get("yahoo_throttle_s", 0.5))
    max_records = int(bf.get("max_records", 20000))
    out_dir = bf.get("out_dir", "scout/backfill")
    score_events = bool(bf.get("score_events", True))
    xbrl_cache_dir = bf.get("xbrl_cache_dir", ".cache/sec_xbrl")
    today = today or date.today()
    out_path = out_path or str(Path(out_dir) /
                               f"{signal_key}-{start.isoformat()}-{end.isoformat()}.jsonl")
    fetch_month = today.strftime("%Y-%m")          # v2 §1: FETCH-time month, never as_of's

    # Free-disk preflight (design 2026-07-07 §5): the month-keyed companyfacts cache means
    # a different-month run reuses nothing (.cache/sec_xbrl is multi-GB) — abort BEFORE any
    # fetch rather than wedge the VPS mid-cohort. `_free_gb` is the test seam.
    min_free = float(bf.get("min_free_disk_gb", 8.0))
    free = (_free_gb or free_disk_gb)(xbrl_cache_dir)
    if free < min_free:
        raise RuntimeError(
            f"backfill: only {free:.1f} GB free on the cache filesystem (floor "
            f"{min_free:.0f} GB) — prune old .cache/sec_xbrl months and retry")

    if _prereg is None:
        from .preregister import load_prereg
        repo_root = str(Path(__file__).parent.parent.parent.parent)
        _prereg = load_prereg(spec["slug"], repo_root=repo_root)
    k_months = int(_prereg.get("k_months", 12))

    # v2 §6: prereg window check — absent window_start/window_end (pre-Task-6 yaml) is a
    # silent back-compat no-op; a present-but-mismatched window warns + labels the run.
    window_not_preregistered = False
    w_start_raw, w_end_raw = _prereg.get("window_start"), _prereg.get("window_end")
    if w_start_raw is not None and w_end_raw is not None:
        prereg_start, prereg_end = _parse_prereg_date(w_start_raw), _parse_prereg_date(w_end_raw)
        if prereg_start is None or prereg_end is None:
            warnings.warn(
                "backfill: malformed prereg window value(s) — treating window as unregistered",
                stacklevel=2)
        elif prereg_start != start or prereg_end != end:
            warnings.warn(
                f"backfill: run window {start.isoformat()}:{end.isoformat()} does not match "
                f"the pre-registered window {prereg_start.isoformat()}:{prereg_end.isoformat()} "
                f"— this run is NOT pre-registered", stacklevel=2)
            window_not_preregistered = True

    if _fetch_window is None:
        _fetch_window = spec["fetch_factory"](bf, today)
    if _fetch_history is None:
        def _fetch_history(tkr):
            time.sleep(yh_throttle)               # polite even with the day cache
            return fetch_history_sync(tkr, identity=identity, today=today)
    if _fetch_delisting is None:
        from .delisting import fetch_filing_records

        def _fetch_delisting(cik):
            time.sleep(sec_throttle)
            return fetch_filing_records(cik, identity)
    if _fetch_facts is None:
        def _fetch_facts(cik):
            time.sleep(sec_throttle)
            return fetch_companyfacts_sync(cik, identity=identity, cache_dir=xbrl_cache_dir,
                                           month=fetch_month)
    if _fetch_sic is None:
        def _fetch_sic(cik):
            time.sleep(sec_throttle)
            return fetch_sic_sync(cik, identity=identity, cache_dir=xbrl_cache_dir,
                                  month=fetch_month)

    # SPY fetched ONCE for the whole run (design A4) — every scored event's price leg shares
    # it; only needed when scoring is on.
    spy_hist = _fetch_history("SPY") if score_events else None

    # Anything that can raise (file I/O) happens BEFORE the owned resource below is
    # acquired, so a failure here can never leak an un-closed Symbology.
    existing_keys = {r.get("meta", {}).get("key") for r in load_backfill_events(out_path)}
    failed_chunks: list[str] = []
    written_total = 0
    n_sic_missing = 0

    # Symbology acquisition is the LAST setup step before try/finally — nothing risky
    # may sit between it and the try (leak-window guard).
    owns_sym = _symbology is None
    if _symbology is None:
        from .symbology import Symbology
        _symbology = Symbology(identity,
                               cache_dir=bf.get("symbology_cache_dir", ".cache/symbology"))
    try:
        _assemble_spec = (spec["assemble_factory"](bf, today)
                          if "assemble_factory" in spec else spec["assemble"])
        for c_start, c_end in _month_chunks(start, end):
            recs = _fetch_window(c_start, c_end, identity, throttle_s=sec_throttle,
                                 max_records=max_records)
            if recs is None:
                failed_chunks.append(f"{c_start}:{c_end}")
                continue
            # _symbology is provably non-None here (built just above when absent).
            events = _assemble_spec(recs, _symbology.resolve_ticker)
            fresh = [e for e in events if e.meta.get("key") not in existing_keys]
            measured = []
            for ev in fresh:
                if window_not_preregistered:
                    ev = replace(ev, meta={**ev.meta, "window_not_preregistered": True})
                hist = None
                if not ev.ticker.startswith("CIK:"):
                    hist = _fetch_history(ev.ticker)
                if score_events and hist is not None:
                    facts = _fetch_facts(ev.cik) if ev.cik else None
                    sic = ev.meta.get("sic")      # EFTS rows carry sics inline — free
                    if sic is None and ev.cik:
                        sic = _fetch_sic(ev.cik)
                    if ev.cik and sic is None:
                        n_sic_missing += 1
                    gated, composite = score_event(ev, hist, facts, spy_hist, sic, config)
                    ev = replace(ev, gated=gated, composite=composite)
                measured.append(measure_event(ev, hist, k_months, today=today,
                                              fetch_delisting_records=_fetch_delisting))
                del hist                          # one PriceHistory at a time (VPS RAM budget)
            written_total += append_events(out_path, measured)
            for ev in measured:
                existing_keys.add(ev.meta.get("key"))
    finally:
        if owns_sym and _symbology is not None:
            _symbology.close()

    summary = summarize(load_backfill_events(out_path))
    summary["out_path"] = out_path
    summary["written"] = written_total
    summary["n_sic_missing"] = n_sic_missing
    if window_not_preregistered:
        summary["window_not_preregistered"] = True
    if failed_chunks:
        summary["failed_chunks"] = failed_chunks
        warnings.warn(f"backfill: {len(failed_chunks)} chunk(s) failed — re-run to resume: "
                      f"{failed_chunks}", stacklevel=2)
    if _symbology is not None and getattr(_symbology, "low_confidence", None):
        summary["low_confidence"] = list(_symbology.low_confidence)
    return summary


def run_backfill_13d(config: dict, *, start: date, end: date, identity: str,
                     today: Optional[date] = None, out_path: Optional[str] = None,
                     **seams) -> dict:
    """13D leg — byte-identical to the pre-generalization coordinator (pinned by
    tests/test_scout_backfill.py + tests/test_scout_backfill_cli.py passing unchanged)."""
    return run_backfill(config, signal_key="13d", start=start, end=end, identity=identity,
                        today=today, out_path=out_path, **seams)


def run_backfill_13d_a(config: dict, *, start: date, end: date, identity: str,
                       today: Optional[date] = None, out_path: Optional[str] = None,
                       **seams) -> dict:
    """13D/A material stake-increase escalation leg (edgar:13d_stake_increase; prereg
    edgar_13d_stake_increase.yaml, K=3m). Expected sign: POSITIVE (Bebchuk-Brav-Jiang 2015
    campaign-drift family) — ships disabled at weight 0.5 pending this backfill verdict, the
    buyback/8-K measure-first precedent."""
    return run_backfill(config, signal_key="13d-a", start=start, end=end,
                        identity=identity, today=today, out_path=out_path, **seams)


def run_backfill_8k(config: dict, *, start: date, end: date, identity: str,
                    today: Optional[date] = None, out_path: Optional[str] = None,
                    **seams) -> dict:
    """Positive-pocket 8-K leg (edgar:8k, 1.01∧3.03; prereg edgar_8k.yaml, K=3m)."""
    return run_backfill(config, signal_key="8k", start=start, end=end, identity=identity,
                        today=today, out_path=out_path, **seams)


def run_backfill_8k_neg(config: dict, *, start: date, end: date, identity: str,
                        today: Optional[date] = None, out_path: Optional[str] = None,
                        **seams) -> dict:
    """Negative-item veto cohort (edgar:8k_negative; prereg edgar_8k_negative.yaml, K=3m).
    Expected sign: NEGATIVE — a KILL-shaped verdict CONFIRMS the veto shipping ON."""
    return run_backfill(config, signal_key="8k-neg", start=start, end=end,
                        identity=identity, today=today, out_path=out_path, **seams)


def run_backfill_buyback(config: dict, *, start: date, end: date, identity: str,
                         today: Optional[date] = None, out_path: Optional[str] = None,
                         **seams) -> dict:
    """Buyback-authorization leg (edgar:buyback_auth; prereg edgar_buyback_auth.yaml, K=3m).
    Expected sign: POSITIVE (Ikenberry-Lakonishok-Vermaelen 1995 / Peyer-Vermaelen 2009).
    The cohort runs uncapped/undenied over the live phrase set — daily_cap/deny_list are
    live-only knobs the backfill never applies (the 8-K precedent)."""
    return run_backfill(config, signal_key="buyback", start=start, end=end,
                        identity=identity, today=today, out_path=out_path, **seams)


def group_by_day_records(recs: list[dict]) -> dict:
    from ..backtest.edgar_history import group_by_day
    return group_by_day(recs)


def merge_metrics(primary, secondary):
    """None-overlay merge of two partial StockMetrics (fundamentals leg primary, price leg
    secondary). The two legs populate disjoint fields today; primary-wins is belt-and-braces
    (the _merge_flat precedent, data/models.py).

    `sources` is handled separately: it defaults to `{}` (never None), so the None-overlay
    loop above never fires for it and the secondary leg's provenance would otherwise be
    silently dropped. Merged explicitly (primary wins per-key) whenever either side is
    non-empty."""
    from dataclasses import fields, replace
    updates = {}
    for f in fields(primary):
        if f.name == "sources":
            continue
        if getattr(primary, f.name) is None:
            v = getattr(secondary, f.name)
            if v is not None:
                updates[f.name] = v
    sec_sources = getattr(secondary, "sources", {}) or {}
    pri_sources = getattr(primary, "sources", {}) or {}
    if sec_sources or pri_sources:
        updates["sources"] = {**sec_sources, **pri_sources}
    return replace(primary, **updates) if updates else primary


def score_event(ev, hist, facts, spy, sic, config):
    """PiT score() reconstruction for one backfill event (design A3 + v2). as_of is the
    FILING date (meta['filing_date']) — event_date is the F12-shifted ENTRY day and using it
    would leak the announcement-pop session into the price legs. Never raises -> (None, None)."""
    try:
        if facts is None or hist is None or not getattr(hist, "dates", None):
            return (None, None)
        as_of = date.fromisoformat(ev.meta["filing_date"])
        from ..providers._xbrl_facts import extract_panel, panel_to_metrics
        panel = extract_panel(facts, as_of)
        price = hist.nominal_close_asof(as_of)
        # v2 §5: clamp — the callback's 5-day forward tolerance must never reach past as_of
        def price_at(d):
            return hist.nominal_price_on(d) if d <= as_of else None
        m1 = panel_to_metrics(panel, ticker=ev.ticker, sic=sic, price=price, price_at=price_at)
        dates, closes = hist.through(as_of)
        spy_d, spy_c = spy.through(as_of) if spy is not None else ([], [])
        from ..data.bridge import snapshot_to_metrics
        from ..data.sources import snapshot_from_closes_dated
        m2 = snapshot_to_metrics(snapshot_from_closes_dated(ev.ticker, dates, closes, spy_d, spy_c))
        from .. import scoring
        card = scoring.score(merge_metrics(m1, m2), config)
        return (bool(card.gates), card.composite)
    except Exception as exc:  # noqa: BLE001 — a single unscoreable event must not sink the batch
        warnings.warn(f"backfill: score_event failed for {ev.ticker}: "
                      f"{redact_secrets(str(exc))}", stacklevel=2)
        return (None, None)

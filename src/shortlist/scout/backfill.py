"""13D backfill coordinator (raw cohort) — spec §6.5/§8, P2 Plan 3.

Walk history (backtest.edgar_history) -> resolve PiT ticker (symbology, CIK-keyed) -> assemble
CohortEvents (origin="backfill") -> measure survivorship-accounted returns with per-event
CLASSIFIED delisting terminals (delisting.py) -> append to an idempotent gitignored JSONL under
scout/backfill/ (NEVER ScoutState — synthetic and live must not pool, M1).

Entry timing (F12): event_date = first trading day STRICTLY AFTER the filing date (13Ds land
after close; the filing-day close would capture the announcement pop, not the drift).
Filter ordering (the measurable-fraction denominator): signal-definition filters (initial-13D,
SPAC, affiliate) EXCLUDE records entirely; everything after them is selected, and resolution/
price/delisting failures only mark events non-measurable (counted, never dropped).
Serial by design: one Symbology (archive.org throttle), one PriceHistory in memory at a time
(VPS: ~1.9 GB RAM shared with a live trading bot).
"""
from __future__ import annotations

from datetime import date, timedelta

from .calendar import is_trading_day
from .edgar_index import activist_stakes_from_records
from .firehose import CohortEvent
from .quality import is_affiliate_filing, is_spac_or_shell

SIGNAL = "edgar:activist_13d"


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
            resolved_rows.append({**r, "ticker": tkr})
        for cik, r in sentinel_ciks.items():
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

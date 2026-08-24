#!/usr/bin/env python3
"""Earnings-date evidence for `2026-08-24-options-surface-design.md` §6.2.

Answers the two questions the implied-earnings-move line depends on, both of which the
first draft of that design got wrong by checking presence instead of accuracy:

  A. HOW RELIABLE IS `Earnings.next_date`?  Replays every stored snapshot's `next_date`
     and counts revisions made while the predicted date was still in the future. Measured
     2026-08-24 over 42 tickers x 31-48 captures: 14 revisions, median 7 days, max 8
     (CSCO oscillated 2026-08-11 <-> 2026-08-19 four times). That maximum is where the
     config's `earnings_date_uncertainty_days: 8` comes from. It matters because a date
     revised LATER can leave an already-selected expiry sitting BEFORE the print, pricing
     no event at all while the brief says it prices one.

  B. WHAT IS THE AUTHORITATIVE ANNOUNCEMENT DATE?  8-K Item 2.02 (Results of Operations).
     Validated at +0 days against print dates recovered independently from (A)'s
     roll-forwards on AAPL/GOOGL/MSFT. Finnhub cannot answer this: its rows carry only the
     fiscal `period` (quarter-end) and the free-tier calendar holds no past entries
     (`data/sources/finnhub.py:_earnings`).

It then computes the realized post-announcement moves the design prints beside the implied
one, which is what makes an implied move interpretable at all.

Usage:
    uv run python docs/audits/scripts/probe_earnings_timing.py                 # A only (offline)
    uv run python docs/audits/scripts/probe_earnings_timing.py --moves AAPL INTC KO

`--moves` needs SEC_IDENTITY and network. Read-only; the store is opened for reading only.
"""
from __future__ import annotations

import argparse
import datetime
import glob
import gzip
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

DEFAULT_STORE = "/opt/shortlist/state/snapshots"


def _iso(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s)


def next_date_revisions(store: Path) -> None:
    """(A) How often, and by how much, does a predicted earnings date move?"""
    series: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for ticker in sorted(os.listdir(store)):
        for path in sorted(glob.glob(str(store / ticker / "*.json.gz"))):
            try:
                with gzip.open(path, "rt") as fh:
                    snap = json.load(fh)
            except Exception:                    # noqa: BLE001 — a corrupt file is not the subject
                continue
            earnings = snap.get("earnings") or {}
            if earnings.get("next_date"):
                series[ticker].append((snap.get("as_of", "")[:10], earnings["next_date"]))
    if not series:
        print(f"no snapshots with an earnings.next_date under {store}")
        return

    revisions: list[tuple[str, str, str, str, int]] = []
    changes = 0
    for ticker, rows in series.items():
        rows.sort()
        previous: Optional[str] = None
        for as_of, predicted in rows:
            if previous is not None and predicted != previous:
                changes += 1
                # A revision is a change made while BOTH the old and the new predicted
                # date are still ahead of us. A change on or after the old date is just
                # the normal roll-forward to the next quarter.
                if _iso(as_of) < _iso(previous) and _iso(as_of) < _iso(predicted):
                    delta = (_iso(predicted) - _iso(previous)).days
                    revisions.append((ticker, as_of, previous, predicted, delta))
            previous = predicted

    counts = [len(v) for v in series.values()]
    print("=== (A) next_date reliability ===")
    print(f"  tickers {len(series)}, captures/ticker {min(counts)}-{max(counts)}")
    print(f"  next_date changes seen: {changes}  (most are normal roll-forwards)")
    print(f"  genuine PRE-EVENT revisions: {len(revisions)}")
    if revisions:
        mags = sorted(abs(r[4]) for r in revisions)
        print(f"    |revision| days: min {mags[0]}  median {mags[len(mags) // 2]}  max {mags[-1]}")
        print(f"    >3 days: {sum(1 for m in mags if m > 3)} of {len(mags)}")
        print(f"    -> config earnings_date_uncertainty_days should be >= {mags[-1]}")
        for ticker, as_of, was, now, delta in sorted(revisions, key=lambda r: -abs(r[4]))[:12]:
            print(f"      {ticker:6} seen {as_of}  {was} -> {now}  {delta:+d}d")


def realized_moves(tickers: list[str], quarters: int) -> None:
    """(B) 8-K Item 2.02 announcement dates -> realized close-to-close moves."""
    import httpx
    from edgar import Company, set_identity

    identity = os.environ.get("SEC_IDENTITY")
    if not identity:
        raise SystemExit("SEC_IDENTITY must be set for --moves")
    set_identity(identity)

    print("\n=== (B) realized post-announcement moves (8-K Item 2.02) ===")
    for ticker in tickers:
        try:
            r = httpx.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                          params={"range": "2y", "interval": "1d"},
                          headers={"User-Agent": "Mozilla/5.0 (shortlist probe)"}, timeout=30)
            result = r.json()["chart"]["result"][0]
            series = [(datetime.datetime.fromtimestamp(t, datetime.timezone.utc).date(), c)
                      for t, c in zip(result["timestamp"],
                                      result["indicators"]["quote"][0]["close"],
                                      strict=False)
                      if c is not None]
            dates = [d for d, _ in series]
            close = dict(series)

            announcements = []
            for filing in Company(ticker).get_filings(form="8-K").head(30):
                # Anchored, NOT a substring search: a bare `"2.02" in items` also
                # matches 12.02, a different disclosure, and a wrong announcement
                # date silently shifts every move computed from it.
                if not re.search(r"(?<![\d.])2\.02(?![\d])",
                                 str(getattr(filing, "items", "") or "")):
                    continue
                fd = filing.filing_date
                announcements.append(fd if isinstance(fd, datetime.date) else _iso(str(fd)))

            moves = []
            for announced in sorted(announcements, reverse=True)[:quarters]:
                before = [d for d in dates if d <= announced]
                after = [d for d in dates if d > announced]
                if before and after:
                    pct = (close[after[0]] / close[before[-1]] - 1) * 100
                    moves.append((announced, round(pct, 1)))
            if not moves:
                print(f"  {ticker}: no usable 8-K 2.02 + price pairs")
                continue
            rendered = ", ".join(f"{m:+.1f}%" for _, m in moves)
            mags = sorted(abs(m) for _, m in moves)
            print(f"  {ticker:6} {rendered}   |median| {mags[len(mags) // 2]:.1f}%")
        except Exception as e:                   # noqa: BLE001 — a probe reports, never aborts
            print(f"  {ticker}: ERR {type(e).__name__}: {e}")
    print("\n  NOTE an 8-K 2.02 filed after the close reacts on the NEXT session; one filed\n"
          "  pre-open reacts the same day. The close-to-close span above is correct for\n"
          "  after-close filers (most large caps) and shifts by a session otherwise. The\n"
          "  filing's acceptance timestamp disambiguates it and is not used here.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", type=Path, default=Path(DEFAULT_STORE),
                    help=f"snapshot store root (default {DEFAULT_STORE})")
    ap.add_argument("--moves", nargs="*", metavar="TICKER",
                    help="also compute realized post-announcement moves for these tickers")
    ap.add_argument("--quarters", type=int, default=6)
    args = ap.parse_args()

    if args.store.exists():
        next_date_revisions(args.store)
    else:
        print(f"store not found at {args.store} — skipping (A)")
    if args.moves:
        realized_moves(args.moves, args.quarters)


if __name__ == "__main__":
    main()

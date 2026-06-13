"""Portfolio-awareness leaf: load a hand-maintained holdings file and, given the
ScoreCards the harness already produced, compute exposure + monitoring alerts.

Pure and dependency-light — stdlib only, plus the ScoreCard type and the no_data
predicate. No I/O beyond reading the holdings file; no network; no optional deps.
Safe to import on the always-on bot path.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .models import ScoreCard
from .validation import no_data as _no_data


@dataclass(frozen=True)
class Holding:
    ticker: str
    shares: float


def load_holdings(path) -> tuple[list[Holding], list[str]]:
    """Parse `ticker,shares` rows leniently. Returns (holdings, warnings).

    - A header row (first column exactly "TICKER", case-insensitive) is silently
      skipped; any OTHER row with non-numeric shares is treated as malformed and warned.
    - `shares` is parsed as a float; negative values (short positions) are accepted as-is.
    - Extra columns ignored; blank/malformed rows skipped with a warning.
    - Tickers upper-cased + stripped; duplicate tickers summed (warned).
    - Missing/unreadable file -> ([], [warning]); never raises.
    """
    p = Path(path)
    if not p.exists():
        return [], [f"Portfolio file not found: {p}"]
    warnings: list[str] = []
    totals: dict[str, float] = {}
    order: list[str] = []
    try:
        rows = list(csv.reader(p.read_text(encoding="utf-8-sig").splitlines()))
    except OSError as e:                      # unreadable file
        return [], [f"Could not read {p}: {e}"]
    for raw in rows:
        if not raw or not "".join(raw).strip():
            continue                          # blank line
        ticker = raw[0].strip().upper()
        shares_s = (raw[1] if len(raw) > 1 else "").strip()
        if not ticker or len(raw) < 2:
            warnings.append(f"Skipped row (missing ticker/shares): {raw}")
            continue
        try:
            shares = float(shares_s)
        except ValueError:
            if ticker == "TICKER":            # header row — silently skip
                continue
            warnings.append(f"Skipped row (shares not a number): {raw}")
            continue
        if ticker not in totals:
            order.append(ticker)
            totals[ticker] = shares
        else:
            totals[ticker] += shares
            warnings.append(f"Duplicate ticker {ticker}: summed shares.")
    return [Holding(t, totals[t]) for t in order], warnings

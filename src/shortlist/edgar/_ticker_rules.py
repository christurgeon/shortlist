"""Shared ticker and 8-K item normalization. Pure, no I/O."""
from __future__ import annotations

import re
from typing import Iterable

# 5th-letter security-type codes on a 5-letter symbol that mean NOT US common stock:
# F=foreign ordinary (the OTC *F junk), Y=ADR, W=warrant, U=unit, R=rights, Q=bankruptcy.
# Only applied to 5-char symbols; 4-char tickers ending in these letters (e.g. WOOF) are fine.
# `X` is an open-end mutual fund, which scored ungated as a top-ranked stock.
# Evidence: docs/audits/2026-08-07-funnel-gate-mismatch.md §3.
_FIFTH_LETTER_SUFFIXES = frozenset("FYWURQX")

_ITEM_RE = re.compile(r"\d+\.\d{2}")

# Tokens filers and edgartools put in place of an unresolved issuer ticker. They are
# truthy, so a bare `if not ticker` passes them, and bucketing by ticker would merge
# unrelated issuers into one phantom cluster.
_PLACEHOLDER_TICKERS = frozenset({"", "NONE", "NA", "N/A", "NULL"})

# aff10b5One / isOfficer appear as both 0|1 and false|true in real Form 4 filings.
# Shared by dera.py and insider.py.
_TRUE = {"1", "true", "yes", "y"}


def junk_suffix(ticker: str) -> bool:
    """5th-letter security-type suffix on a 5-letter symbol. 4-char tickers ending in these
    letters (WOOF) are fine; dotted/hyphenated share classes are NOT dropped."""
    return len(ticker) == 5 and ticker.isalpha() and ticker[-1] in _FIFTH_LETTER_SUFFIXES


def real_ticker(raw: str | None) -> str:
    """The stripped, uppercased symbol, or "" for a placeholder, a punctuation-only form
    or a CIK-as-ticker. A real symbol has at least one letter (BRK.B, AXIA3)."""
    t = (raw or "").strip().upper()
    if t in _PLACEHOLDER_TICKERS or not any(c.isalpha() for c in t):
        return ""
    return t


def normalize_items(raw) -> tuple[str, ...]:
    """Extract 8-K item codes ('1.03', '2.01', ...) from whatever shape the source hands us:
    the submissions-JSON comma string, edgartools' labelled list, or junk (-> ()). Never raises."""
    if raw is None:
        return ()
    if isinstance(raw, str):
        parts: Iterable = [raw]
    else:
        try:
            parts = list(raw)
        except TypeError:
            return ()
    out: list[str] = []
    for p in parts:
        for code in _ITEM_RE.findall(str(p)):
            if code not in out:
                out.append(code)
    return tuple(out)

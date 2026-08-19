"""Shared ticker and 8-K item normalization. Pure, no I/O."""
from __future__ import annotations

import re
from typing import Iterable

# 5th-letter security-type codes on a 5-letter symbol that mean NOT US common stock:
# F=foreign ordinary (the OTC *F junk), Y=ADR, W=warrant, U=unit, R=rights, Q=bankruptcy.
# Only applied to 5-char symbols — 4-char tickers ending in these letters (e.g. WOOF) are fine.
# `X` (open-end mutual fund) was added 2026-08-07: three X-suffixed funds (FTECX, VFLEX,
# BBASX) reached the live picks ledger through `edgar_form4`, and BBASX scored composite
# 100.0 UNGATED — a mutual fund delivered to the analyst as a top-ranked stock idea.
# Evidence: docs/audits/2026-08-07-funnel-gate-mismatch.md §3.
_FIFTH_LETTER_SUFFIXES = frozenset("FYWURQX")

_ITEM_RE = re.compile(r"\d+\.\d{2}")

# aff10b5One / isOfficer appear as BOTH 0|1 AND false|true in real Form 4 filings
# (live-verified 2026-07-26). Shared by dera.py and insider.py, which both parse those
# same boolean-ish fields off raw filing XML/TSV.
_TRUE = {"1", "true", "yes", "y"}


def junk_suffix(ticker: str) -> bool:
    """5th-letter security-type suffix on a 5-letter symbol. 4-char tickers ending in these
    letters (WOOF) are fine; dotted/hyphenated share classes are NOT dropped."""
    return len(ticker) == 5 and ticker.isalpha() and ticker[-1] in _FIFTH_LETTER_SUFFIXES


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

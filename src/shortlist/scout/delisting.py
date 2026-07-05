"""Delisting detect + reason classifier for the Phase-2 backfill (sign-integrity, spec §6.6).

Detection: a Form 25/25-NSE (delisting notice) or Form 15 (deregistration) for the subject CIK.
Reason from structured 8-K item codes in the window before the delisting (no NLP):
Item 1.03 -> bankruptcy (Shumway venue partial: NYSE -30% / Nasdaq -55%); Item 2.01 + 5.01 in the
SAME 8-K -> M&A/take-private (terminal = last traded close, NOT a penalty); neither -> unclassified
-> non-measurable (never a guessed sign). Precedence (R-B3): a 1.03 anywhere in the window
overrides a later 2.01+5.01 (post-Ch.11 asset sales are bankruptcy artifacts).

Pure classification core over lightweight FilingRecords + a thin CIK-keyed edgartools fetcher
(the edgar Company lookup is ALWAYS int-cast from the subject CIK, never a ticker — the
BBBY->Overstock ticker-reuse landmine). Never raises to the caller.
See docs/superpowers/specs/2026-07-01-signal-validation-harness-backfill-design.md §6.6/§8/§12/§16.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Optional

BANKRUPTCY = "bankruptcy"
MNA = "mna"
UNCLASSIFIED = "unclassified"

_ITEM_RE = re.compile(r"\d+\.\d{2}")
# Shumway (1997) delisting-bias partials by listing venue (spec §6.6). Unknown venue -> the
# harsher Nasdaq figure (conservative for a long book; the sensitivity band re-stresses anyway).
_SHUMWAY = {"nyse": -0.30, "nasdaq": -0.55}


def _base_form(form) -> str:
    """'25-NSE/A' -> '25-NSE'; None-safe."""
    return str(form or "").split("/")[0].strip().upper()


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


def venue_from_filer(name) -> Optional[str]:
    """'nyse' | 'nasdaq' | None from a Form-25 filer name (the exchange files Form 25)."""
    n = str(name or "").lower()
    if "nasdaq" in n:
        return "nasdaq"
    if "nyse" in n or "new york stock exchange" in n:
        return "nyse"
    return None


def shumway_partial(venue: Optional[str]) -> float:
    return _SHUMWAY.get(venue or "", -0.55)

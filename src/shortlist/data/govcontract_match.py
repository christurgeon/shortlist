"""Pure recipient-name matching for the USAspending gov-contracts source.

USAspending `recipient_search_text` over-matches (JVs, subsidiaries, unrelated
names). This module normalizes names and scores a [0,1] confidence so the source
can KEEP only transactions whose recipient clears a threshold and ABSTAIN
otherwise (honest degradation — never a wrong attribution). No I/O.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Iterable, Optional

# Corporate suffixes / noise words stripped before comparison.
_SUFFIXES = {"corp", "corporation", "inc", "incorporated", "llc", "lp", "co",
             "company", "holdings", "holding", "the", "ltd", "plc", "group"}

# Non-exhaustive seed map: SEC-ticker -> known USAspending recipient name tokens
# (already in normalize_name() form) for subsidiaries/divisions whose names don't
# resemble the parent's SEC title. Extension point; v1 covers a few marquee
# defense/industrial parents.
_ALIAS_SEED: dict[str, tuple[str, ...]] = {
    "RTX": ("raytheon", "pratt whitney", "collins aerospace"),
    "LMT": ("sikorsky",),
    "GD": ("gulfstream", "electric boat", "bath iron works"),
    "NOC": ("northrop grumman systems",),
    "BA": ("boeing",),
}


def normalize_name(name: str) -> str:
    """Casefold, strip punctuation, drop corporate suffixes/noise words."""
    if not name:
        return ""
    s = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    toks = [t for t in s.split() if t and t not in _SUFFIXES]
    return " ".join(toks)


def match_confidence(sec_name: str, recipient_name: str,
                     alias_for: Optional[Iterable[str]] = None) -> float:
    """Confidence in [0,1] that `recipient_name` is the same entity as `sec_name`.
    SequenceMatcher ratio on normalized strings, lifted to 1.0 when the recipient
    matches a known alias-seed token for any ticker in `alias_for`."""
    a, b = normalize_name(sec_name), normalize_name(recipient_name)
    if not a or not b:
        return 0.0
    base = SequenceMatcher(None, a, b).ratio()
    for tk in (alias_for or ()):
        for tok in _ALIAS_SEED.get(tk.upper(), ()):  # token already in normalized form
            if tok in b:
                return max(base, 1.0)
    return base


def aliases_for(ticker: str) -> tuple[str, ...]:
    """Public accessor so the source can pass alias_for=(ticker,) ergonomically."""
    return _ALIAS_SEED.get(ticker.upper(), ())

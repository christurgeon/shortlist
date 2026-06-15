"""Generic entity-name matching for name-keyed external sources.

Government filing feeds (USAspending recipients, Senate LDA lobbying clients) key
on free-text legal names, not tickers. This leaf normalizes names and scores a
[0,1] confidence so a source can KEEP only rows whose entity clears a threshold
and ABSTAIN otherwise (honest degradation — never a wrong attribution). No I/O.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

# Corporate suffixes / noise words stripped before comparison.
_SUFFIXES = {"corp", "corporation", "inc", "incorporated", "llc", "lp", "co",
             "company", "holdings", "holding", "the", "ltd", "plc", "group"}


def normalize_name(name: str) -> str:
    """Casefold, strip punctuation, drop corporate suffixes/noise words."""
    if not name:
        return ""
    s = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    toks = [t for t in s.split() if t and t not in _SUFFIXES]
    return " ".join(toks)


def match_confidence(name_a: str, name_b: str) -> float:
    """Confidence in [0,1] that two names denote the same entity — a
    SequenceMatcher ratio on the normalized strings. 0.0 if either is empty."""
    a, b = normalize_name(name_a), normalize_name(name_b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()

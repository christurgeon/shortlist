"""Pure recipient-name matching for the USAspending gov-contracts source.

USAspending `recipient_search_text` over-matches (JVs, subsidiaries, unrelated
names). This module normalizes names and scores a [0,1] confidence so the source
can KEEP only transactions whose recipient clears a threshold and ABSTAIN
otherwise (honest degradation — never a wrong attribution). No I/O.
"""
from __future__ import annotations

from difflib import SequenceMatcher
from typing import Iterable, Optional

from .entity_match import normalize_name

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


def match_confidence(sec_name: str, recipient_name: str,
                     alias_for: Optional[Iterable[str]] = None) -> float:
    """Confidence in [0,1] that `recipient_name` is the same entity as `sec_name`.
    SequenceMatcher ratio on normalized strings, lifted to 1.0 when the recipient
    matches a known alias-seed token for any ticker in `alias_for`."""
    a, b = normalize_name(sec_name), normalize_name(recipient_name)
    if not a or not b:
        return 0.0
    base = SequenceMatcher(None, a, b).ratio()
    b_tokens = set(b.split())
    for tk in (alias_for or ()):
        for tok in _ALIAS_SEED.get(tk.upper(), ()):  # token already in normalized form
            # whole-word (token-set) containment, NOT raw substring — a short alias
            # token must appear as complete words, never as a fragment of another name.
            if set(tok.split()) <= b_tokens:
                return 1.0  # base is a SequenceMatcher ratio, always <= 1.0 already
    return base


def aliases_for(ticker: str) -> tuple[str, ...]:
    """Public accessor so the source can pass alias_for=(ticker,) ergonomically."""
    return _ALIAS_SEED.get(ticker.upper(), ())

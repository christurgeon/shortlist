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

# SEC ticker -> phrases (in normalize_name() form) naming subsidiaries whose
# recipient names don't resemble the parent's SEC title. A phrase matches as whole
# contiguous words. It lifts only rows the ticker's own search returned, but that
# search also returns unrelated names (USAspending's parent links go stale), so keep
# each phrase specific and pin it, and its near-miss negatives, in
# tests/test_govcontract_match.py.
_ALIAS_SEED: dict[str, tuple[str, ...]] = {
    "RTX": ("raytheon", "pratt whitney", "collins aerospace"),
    "LMT": ("sikorsky",),
    "GD": ("gulfstream", "electric boat", "bath iron works"),
    "NOC": ("northrop grumman systems",),
    "BA": ("boeing",),
    "UNH": ("optum", "optumserve", "optumrx", "optumhealth", "optum360", "optuminsight",
            "change healthcare", "united healthcare"),
    "JNJ": ("johnson johnson health care systems", "depuy synthes", "janssen biotech",
            "auris health", "abiomed", "mentor worldwide", "neuwave medical"),
    "CVX": ("chevron usa", "chevron u s a"),
    "XOM": ("exxonmobil", "exxon mobil"),
    "CMCSA": ("comcast",),
    "MRK": ("merck sharp dohme",),
    "JPM": ("jpmorgan chase", "j p morgan", "jp morgan"),
    "GS": ("goldman sachs", "j aron"),
    "QCOM": ("qualcomm",),
    "DIS": ("twentieth century fox",),
}


def match_confidence(sec_name: str, recipient_name: str,
                     alias_for: Optional[Iterable[str]] = None) -> float:
    """Confidence in [0,1] that `recipient_name` is the same entity as `sec_name`.
    SequenceMatcher ratio on normalized strings, lifted to 1.0 when the recipient
    contains a seed phrase for any ticker in `alias_for`."""
    a, b = normalize_name(sec_name), normalize_name(recipient_name)
    if not a or not b:
        return 0.0
    padded = f" {b} "
    for tk in (alias_for or ()):
        if any(f" {phrase} " in padded for phrase in _ALIAS_SEED.get(tk.upper(), ())):
            return 1.0
    return SequenceMatcher(None, a, b).ratio()

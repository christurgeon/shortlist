"""Select the top-X candidates that fit today's deep-screen ceiling (§4.1)."""
from __future__ import annotations

from .models import Candidate


def select(candidates: list[Candidate], daily_x: int) -> tuple[list[Candidate], int]:
    """Return (chosen, dropped_count). Chosen = top daily_x by interest desc."""
    ordered = sorted(candidates, key=lambda c: c.interest, reverse=True)
    chosen = ordered[:daily_x]
    return chosen, max(0, len(ordered) - len(chosen))

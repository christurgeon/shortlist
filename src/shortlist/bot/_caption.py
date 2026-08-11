"""At-a-glance Telegram caption builder. Stdlib + ``models.rank_key`` only."""
from __future__ import annotations

from ..models import rank_key

# Routine presence-based filing advisories — informative but high-frequency, so they're
# kept OUT of the at-a-glance caption (they still show in the HTML Flags column). Every
# other (discretionary) advisory — social_hype, crowded_short, value_trap, … — surfaces.
_CAPTION_SUPPRESS_FLAGS = frozenset({"recent_8k", "passive_13g", "planned_insider_sale_144"})


def _caption(session, cards, top_n: int = 3) -> str:
    ordered = sorted(cards, key=rank_key, reverse=True)
    top = " · ".join(f"{c.ticker} {c.composite:.0f}" for c in ordered[:top_n])
    lines = [f"Shortlist — {session.isoformat()}", f"Top: {top}"]
    for c in ordered:
        notable = [f for f in (getattr(c, "flags", ()) or ()) if f not in _CAPTION_SUPPRESS_FLAGS]
        if notable:
            lines.append(f"🏷️ {c.ticker}: {', '.join(notable)}")
    return "\n".join(lines)[:1024]

"""At-a-glance Telegram caption builder — a tiny leaf shared by daily.py and bot.py.

Kept deliberately light (stdlib + the lightweight ``models.rank_key`` only) so the
always-on bot path can import it without pulling in the heavy ``daily`` module.
"""
from __future__ import annotations

from ..models import rank_key

# Routine presence-based filing advisories — informative but high-frequency, so they're
# kept OUT of the at-a-glance caption (they still show in the HTML Flags column). Every
# other (discretionary) advisory — social_hype, crowded_short, value_trap, … — surfaces.
_CAPTION_SUPPRESS_FLAGS = frozenset({"recent_8k", "passive_13g", "planned_insider_sale_144"})


def _caption(manifest, cards, top_n: int = 3) -> str:
    ordered = sorted(cards, key=rank_key, reverse=True)
    top = " · ".join(f"{c.ticker} {c.composite:.0f}" for c in ordered[:top_n])
    lines = [f"Scout — {manifest.session.isoformat()}", f"Top: {top}"]
    for c in ordered:
        notable = [f for f in (getattr(c, "flags", ()) or ()) if f not in _CAPTION_SUPPRESS_FLAGS]
        if notable:
            lines.append(f"🏷️ {c.ticker}: {', '.join(notable)}")
    lines.append(f"{manifest.screened} screened from {manifest.raw} raw")
    return "\n".join(lines)[:1024]

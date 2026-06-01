"""Render a ScoutReport to a Telegram-friendly message + the RunManifest dict."""
from __future__ import annotations

from shortlist.models import ScoreCard

from .models import RunManifest


def render_message(cards: list[ScoreCard], manifest: RunManifest,
                   briefs: dict[str, str] | None = None) -> str:
    briefs = briefs or {}
    lines = [f"📊 Scout shortlist — session {manifest.session.isoformat()}", ""]

    for i, c in enumerate(cards, 1):
        flag = f"  ⚠️ {', '.join(c.gates)}" if c.gates else ""
        lines.append(f"{i}. {c.ticker}  {c.composite:.1f}{flag}")
        lines.append(f"   Q{_n(c.quality)} M{_n(c.moat)} G{_n(c.growth)} "
                     f"Opp{_n(c.opportunity)} Ins{_n(c.insider)}")
        if c.ticker in briefs:
            lines.append(f"   📝 {briefs[c.ticker]}")
    lines.append("")

    sig = " · ".join(
        f"{s.name} {'✓' if s.ran else '✗'} ({s.detail})" for s in manifest.signals)
    lines.append(f"Signals: {sig}")
    lines.append(
        f"Funnel: {manifest.raw} raw → {manifest.after_dedup} deduped → "
        f"{manifest.after_prefilter} after prefilter → {manifest.screened} screened "
        f"({manifest.dropped_for_budget} dropped: budget)")
    for note in manifest.notes:
        lines.append(f"Note: {note}")
    return "\n".join(lines)


def _n(v) -> str:
    return f"{v:.0f}" if v is not None else "·"

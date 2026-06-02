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
        mark = "" if getattr(c, "scored", True) else "  (not scored)"
        lines.append(f"{i}. {c.ticker}  {c.composite:.1f}{flag}{mark}")
        conf = getattr(c, "confidence", None)
        conf_str = f" Conf{conf:.2f}" if conf is not None else ""
        lines.append(f"   Q{_n(c.quality)} M{_n(c.moat)} G{_n(c.growth)} "
                     f"Opp{_n(c.opportunity)} Ins{_n(c.insider)} Rsk{_n(c.risk)}{conf_str}")
        # Data-layer coverage honesty: harness cards now carry a per-ticker Coverage
        # diagnostic; surface its interpretive note (e.g. "FMP gated this symbol") so a
        # null sub-score reads as an explained gap, not an unexplained one.
        if c.coverage is not None and c.coverage.note:
            lines.append(f"   ⊘ {c.coverage.note}")
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

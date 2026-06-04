"""Reporting layer facade. build_report() produces all artifacts; render_message() kept
for back-compat (demo + existing callers/tests)."""
from __future__ import annotations

import base64
from dataclasses import dataclass

from .sections import render_html_body, render_text, Detail
from .html import document
from .viewmodel import build_view_model

__all__ = ["build_report", "ReportArtifacts", "render_message"]


@dataclass
class ReportArtifacts:
    png: bytes | None
    html: str
    text: str       # FULL detail (on-disk .txt + journal fallback body)


def _render_png(vm):
    """Lazy bridge to the Pillow renderer; None if Pillow/renderer unavailable.
    This is the ONLY place Pillow is reached on the build path; demo never calls it."""
    try:
        from .png import render_glance
    except Exception:        # noqa: BLE001 — Pillow not installed
        return None
    try:
        return render_glance(vm)
    except Exception:        # noqa: BLE001 — never let chart break delivery
        return None


def build_report(cards, manifest, *, assessments: dict[str, dict]) -> ReportArtifacts:
    vm = build_view_model(cards, manifest, assessments=assessments)
    png = _render_png(vm)
    b64 = base64.b64encode(png).decode() if png else None
    title = f"Scout daily dashboard — {vm.session.isoformat()}"
    html = document(title, b64, render_html_body(vm))
    text = render_text(vm, Detail.FULL)
    return ReportArtifacts(png=png, html=html, text=text)


def render_message(cards, manifest, briefs: dict[str, str] | None = None) -> str:
    """Back-compat text renderer (Telegram GLANCE fallback + demo stdout). Briefs are
    rendered via the _Research section by synthesizing a minimal assessment each."""
    synth = {t: {"synthesis": b} for t, b in (briefs or {}).items()}
    vm = build_view_model(cards, manifest, assessments=synth)
    return render_text(vm, Detail.GLANCE)

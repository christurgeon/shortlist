import io
import pytest
from datetime import date
from shortlist.scout.report.viewmodel import (
    ReportVM, LeaderVM, MetricsVM, FunnelVM, SignalStatusVM)

pytest.importorskip("PIL")
from PIL import Image
from shortlist.scout.report.png import render_glance

_ALL = ["quality", "moat", "growth", "value", "momentum", "insider", "risk"]


def _leader(t, c, subs=None):
    return LeaderVM(ticker=t, name=None, composite=c,
                    subscores=subs or {"quality": 90, "moat": None, "growth": 60, "value": 40,
                                       "momentum": 5, "insider": 50, "risk": 70},
                    masked=set(), gates=[], flags=[], confidence=0.8, thin=False,
                    scored=True, coverage_note=None, metrics=MetricsVM(), assessment=None)


def _vm(leaders):
    return ReportVM(session=date(2026, 6, 4), leaders=leaders,
                    signals=[SignalStatusVM("edgar_form4", True, "x")],
                    funnel=FunnelVM(len(leaders), len(leaders), len(leaders), len(leaders), 0),
                    notes=[])


def test_render_returns_valid_png_bytes():
    out = render_glance(_vm([_leader(f"T{i}", 80 - i) for i in range(6)]))
    assert isinstance(out, bytes) and out[:8] == b"\x89PNG\r\n\x1a\n"
    assert Image.open(io.BytesIO(out)).format == "PNG"


def test_height_scales_with_row_count():
    h3 = Image.open(io.BytesIO(render_glance(_vm([_leader(f"T{i}", 70) for i in range(3)])))).height
    h12 = Image.open(io.BytesIO(render_glance(_vm([_leader(f"T{i}", 70) for i in range(12)])))).height
    assert h12 > h3


def test_empty_renders_a_valid_card_not_a_crash():
    out = render_glance(_vm([]))
    assert Image.open(io.BytesIO(out)).format == "PNG"


def test_all_none_subscores_render(tmp_path):
    nones = dict.fromkeys(_ALL)
    out = render_glance(_vm([_leader("BNK", 0.0, subs=nones)]))
    assert Image.open(io.BytesIO(out)).format == "PNG"   # masked bank -> all gray, no crash

# tests/test_macro_section.py
from __future__ import annotations
from datetime import date
from shortlist.data.macro import MacroContext
from shortlist.bot.report.viewmodel import ReportVM
from shortlist.bot.report.sections import _MacroHeader, Detail

OFF = MacroContext("2026-06-01", 4.5, -0.2, 5.4, 27.0, 3.6, "risk-off", True)

def _vm(macro):
    return ReportVM(session=date(2026, 6, 1), leaders=[], notes=[], macro=macro)

def test_section_omitted_when_no_macro():
    assert _MacroHeader().applies(_vm(None)) is False

def test_section_applies_with_macro():
    assert _MacroHeader().applies(_vm(OFF)) is True

def test_section_text_mentions_regime_and_oas():
    lines = _MacroHeader().render_text(_vm(OFF), Detail.FULL)
    blob = " ".join(lines)
    assert "risk-off" in blob and "OAS" in blob

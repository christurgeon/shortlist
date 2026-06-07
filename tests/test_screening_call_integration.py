"""End-to-end seam test: the persisted-JSON shape written by research.report.write
must be exactly what the scout view-model reads back. Two units that each pass their
own unit tests can still disagree on shape; this locks the contract across them."""
import json

from shortlist.research import report
from shortlist.research.models import QualitativeAssessment, ScreeningCall, Thesis
from shortlist.scout.report.viewmodel import _assessment_vm, call_one_liner


def _assessment(call):
    a = QualitativeAssessment(ticker="X", as_of="2026-06-07", filing_accession="acc",
                              filing_date="2026-01-01", model="m")
    a.thesis = Thesis(bull_case="b", bear_case="leverage rising",
                      what_would_change_my_mind=["margins compress"], takeaway="t")
    a.screening_call = call
    return a


def test_clean_call_roundtrips_through_viewmodel(tmp_path):
    call = ScreeningCall(stance="BUY", conviction="MEDIUM", rationale="Durable moat.",
                         decided_without=["value axis — FMP gated this symbol (402)"],
                         as_of_price=42.0)
    report.write(_assessment(call), tmp_path)
    rec = json.loads((tmp_path / "X" / "acc.json").read_text())

    vm = _assessment_vm(rec)
    assert vm.call_stance == "BUY"
    assert vm.call_label == "Buy"
    assert vm.call_conviction == "MEDIUM"
    assert vm.call_rationale == "Durable moat."
    assert vm.call_watch == "margins compress"
    assert vm.call_decided_without == ["value axis — FMP gated this symbol (402)"]
    assert call_one_liner(rec) == "Buy · conviction Medium — but watch: margins compress"
    # accountability snapshot persisted for a future retrospective hit-rate
    assert rec["screening_call"]["as_of_price"] == 42.0


def test_clamped_call_roundtrips_without_leaking_bull_rationale(tmp_path):
    # A gated name researched via /deep (require_passed=False) produces a clamped call.
    # The scout surface must show the clamp reason, NOT the model's pre-clamp bull text.
    call = ScreeningCall(stance="AVOID", conviction="MEDIUM",
                         rationale="compelling bull thesis", stance_clamped=True,
                         clamp_note="tripped negative_fcf gate")
    report.write(_assessment(call), tmp_path)
    rec = json.loads((tmp_path / "X" / "acc.json").read_text())

    vm = _assessment_vm(rec)
    assert vm.call_stance == "AVOID"
    assert "Auto-downgraded" in vm.call_rationale
    assert "tripped negative_fcf gate" in vm.call_rationale
    assert "compelling bull" not in vm.call_rationale

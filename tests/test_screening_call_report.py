import json
from shortlist.research.models import (QualitativeAssessment, ScreeningCall, Thesis)
from shortlist.research import report


def _assess(call):
    a = QualitativeAssessment(ticker="X", as_of="2026-06-07", filing_accession="acc",
                              filing_date="2026-01-01", model="m")
    a.thesis = Thesis(bull_case="b", bear_case="leverage rising",
                      what_would_change_my_mind=["margins compress"], takeaway="t")
    a.screening_call = call
    return a


def test_no_call_renders_no_block():
    md = report.to_markdown(_assess(None))
    assert "Screening call" not in md


def test_clean_call_badge_and_block():
    call = ScreeningCall(stance="BUY", conviction="MEDIUM", rationale="Durable moat.")
    md = report.to_markdown(_assess(call))
    assert "SCREENING CALL: Buy" in md
    assert "conviction Medium" in md
    assert "but watch: margins compress" in md      # derived from falsifier
    assert "screen only — not advice" in md
    assert "Durable moat." in md
    assert "Decided without" not in md               # nothing to show


def test_gaps_rendered_when_present():
    call = ScreeningCall(stance="HOLD", conviction="LOW", rationale="r",
                         decided_without=["value axis — FMP gated this symbol (402)"],
                         not_applicable=["moat axis — not applicable (financials)"])
    md = report.to_markdown(_assess(call))
    assert "Decided without:" in md and "value axis" in md
    assert "Not applicable:" in md and "moat axis" in md


def test_clamped_demotes_rationale():
    call = ScreeningCall(stance="AVOID", conviction="MEDIUM", rationale="compelling bull",
                         stance_clamped=True, clamp_note="tripped negative_fcf gate")
    md = report.to_markdown(_assess(call))
    assert "Auto-downgraded: tripped negative_fcf gate" in md
    # the model's bull rationale is demoted, not the headline "Why"
    assert "pre-clamp view" in md.lower()
    assert "compelling bull" in md


def test_watch_falls_back_to_bear_case():
    a = _assess(ScreeningCall(stance="BUY", conviction="MEDIUM", rationale="r"))
    a.thesis = Thesis(bull_case="b", bear_case="margin pressure",
                      what_would_change_my_mind=[], takeaway="t")
    md = report.to_markdown(a)
    assert "but watch: margin pressure" in md


def test_json_record_persists_call(tmp_path):
    call = ScreeningCall(stance="BUY", conviction="HIGH", rationale="r", as_of_price=42.0)
    report.write(_assess(call), tmp_path)
    rec = json.loads((tmp_path / "X" / "acc.json").read_text())
    assert rec["screening_call"]["stance"] == "BUY"
    assert rec["screening_call"]["as_of_price"] == 42.0

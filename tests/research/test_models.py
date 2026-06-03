import pytest

from shortlist.research.models import (
    FilingText, Finding, Moat, QualitativeAssessment, assessment_from_payload,
)

PAYLOAD = {
    "business_model_summary": "Designs and sells devices.",
    "moat": {"summary": "Brand + ecosystem.", "sources": ["brand", "switching costs"],
             "trajectory": "stable"},
    "risks": [{"claim": "Supply concentration", "evidence": "substantially all manufacturing is outsourced"}],
    "red_flags": [],
    "management_capital_allocation": "Heavy buybacks.",
    "synthesis": "High quality, fully valued.",
}


def test_filing_text_combined_and_has_content():
    ft = FilingText(ticker="X", accession="a1", filing_date="2026-01-01",
                    business="b", mda="", risk_factors="r")
    assert ft.combined() == "b\n\nr"     # empty section skipped
    assert ft.has_content() is True
    assert FilingText("X", "a1", "2026-01-01").has_content() is False


def test_assessment_from_payload_builds_nested_types():
    a = assessment_from_payload(
        PAYLOAD, ticker="AAPL", as_of="2026-05-31T00:00:00+00:00",
        accession="0000320193-25-000123", filing_date="2025-10-31",
        model="claude-sonnet-4-6", cost_usd=0.03, stop_reason="end_turn")
    assert a.ticker == "AAPL"
    assert isinstance(a.moat, Moat) and a.moat.trajectory == "stable"
    assert a.moat.sources == ["brand", "switching costs"]
    assert len(a.risks) == 1 and isinstance(a.risks[0], Finding)
    assert a.risks[0].verified is False        # grounding not run yet
    assert a.red_flags == []
    assert a.model == "claude-sonnet-4-6" and a.cost_usd == 0.03


def test_assessment_from_payload_rejects_missing_keys():
    with pytest.raises(ValueError):
        assessment_from_payload({"moat": {}}, ticker="X", as_of="t", accession="a",
                                filing_date="d", model="m", cost_usd=None, stop_reason=None)


def test_assessment_from_payload_rejects_bad_moat_type():
    bad = {**PAYLOAD, "moat": "not-an-object"}
    with pytest.raises(ValueError):
        assessment_from_payload(bad, ticker="X", as_of="t", accession="a",
                                filing_date="d", model="m", cost_usd=None, stop_reason=None)

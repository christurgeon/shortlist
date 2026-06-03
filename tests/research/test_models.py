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


def test_conflict_and_thesis_defaults():
    from shortlist.research.models import Conflict, Thesis
    c = Conflict(signal="value", tension="cheap vs declining")
    assert c.filing_says == "" and c.verdict == "silent" and c.verified is False
    t = Thesis()
    assert t.bull_case == "" and t.bear_case == "" and t.takeaway == ""
    assert t.what_would_change_my_mind == []


def test_default_valid_signals_covers_axes_gates_flags():
    from shortlist.research.models import default_valid_signals
    s = default_valid_signals()
    assert "value" in s and "narrative_tone" in s and "short_interest" in s
    assert "gate:negative_fcf" in s and "gate:heavy_insider_selling" in s
    assert "flag:crowded_short" in s and "flag:activist_13d" in s
    assert "flag:planned_insider_sale_144" in s
    assert "gate:negativeFCF" not in s          # typos do not resolve


def test_assessment_synthesis_property_returns_takeaway():
    from shortlist.research.models import QualitativeAssessment, Thesis
    a = QualitativeAssessment(
        ticker="X", as_of="t", filing_accession="a", filing_date="d", model="m",
        thesis=Thesis(takeaway="One-line take."))
    assert a.synthesis == "One-line take."


def test_assessment_asdict_excludes_property_includes_thesis():
    import dataclasses
    from shortlist.research.models import QualitativeAssessment, Thesis
    a = QualitativeAssessment(
        ticker="X", as_of="t", filing_accession="a", filing_date="d", model="m",
        thesis=Thesis(takeaway="t"))
    d = dataclasses.asdict(a)
    assert "synthesis" not in d            # property is NOT serialized (the blocker)
    assert d["thesis"]["takeaway"] == "t"  # nested dataclass IS serialized
    assert d["reconciliation"] == [] and d["silent_count"] == 0

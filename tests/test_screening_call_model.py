from shortlist.research.models import (
    STANCES, CONVICTIONS, ScreeningCall, _screening_call,
    stance_label, call_disclaimer,
    QualitativeAssessment,
)


def test_stances_ordered_most_bullish_first():
    assert STANCES == ("STRONG_BUY", "BUY", "HOLD", "AVOID", "STRONG_AVOID")
    assert CONVICTIONS == ("HIGH", "MEDIUM", "LOW")


def test_parse_well_formed():
    c = _screening_call({"call": {"stance": "BUY", "conviction": "HIGH",
                                  "rationale": "Durable moat."}})
    assert isinstance(c, ScreeningCall)
    assert c.stance == "BUY" and c.conviction == "HIGH"
    assert c.rationale == "Durable moat."


def test_parse_missing_key_returns_none():
    assert _screening_call({"thesis": {}}) is None
    assert _screening_call({"call": "not-a-dict"}) is None


def test_parse_bad_values_coerced():
    c = _screening_call({"call": {"stance": "MOON", "conviction": "WILD"}})
    assert c.stance == "HOLD" and c.conviction == "LOW"


def test_label_helpers():
    assert stance_label("STRONG_BUY") == "Strong Buy"
    assert stance_label("BUY", {"research": {"screening_call": {
        "labels": {"BUY": "Accumulate"}}}}) == "Accumulate"
    assert call_disclaimer() == "screen only — not advice"


def test_assessment_field_defaults_none():
    a = QualitativeAssessment(ticker="X", as_of="", filing_accession="",
                              filing_date="", model="")
    assert a.screening_call is None


def test_null_rationale_becomes_empty():
    c = _screening_call({"call": {"stance": "BUY", "rationale": None}})
    assert c.rationale == ""


def test_parse_normalizes_case_and_spaces():
    c = _screening_call({"call": {"stance": "Buy", "conviction": "high"}})
    assert c.stance == "BUY" and c.conviction == "HIGH"
    c2 = _screening_call({"call": {"stance": "Strong Buy", "conviction": "Medium"}})
    assert c2.stance == "STRONG_BUY" and c2.conviction == "MEDIUM"

import pytest

from shortlist.research.models import (
    FilingText, Finding, Moat, assessment_from_payload,
)

PAYLOAD = {
    "business_model_summary": "Designs and sells devices.",
    "moat": {"summary": "Brand + ecosystem.", "sources": ["brand", "switching costs"],
             "trajectory": "stable"},
    "risks": [{"claim": "Supply concentration", "evidence": "substantially all manufacturing is outsourced"}],
    "red_flags": [],
    "management_capital_allocation": "Heavy buybacks.",
    "reconciliation": [
        {"signal": "value", "tension": "cheap vs declining FCF",
         "filing_says": "Free cash flow declined", "verdict": "contradicts"}],
    "thesis": {"bull_case": "Strong brand.", "bear_case": "Slowing growth.",
               "what_would_change_my_mind": ["FCF reaccelerates"],
               "takeaway": "High quality, fully valued."},
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
        model="claude-sonnet-5", cost_usd=0.03, stop_reason="end_turn")
    assert a.ticker == "AAPL"
    assert isinstance(a.moat, Moat) and a.moat.trajectory == "stable"
    # sources are Findings now; the legacy bare-string payload above coerces to
    # claim-only items with no quote (see test_evidence_discipline.py)
    assert [s.claim for s in a.moat.sources] == ["brand", "switching costs"]
    assert all(isinstance(s, Finding) and s.evidence == "" for s in a.moat.sources)
    assert len(a.risks) == 1 and isinstance(a.risks[0], Finding)
    assert a.risks[0].verified is False        # grounding not run yet
    assert a.red_flags == []
    assert a.model == "claude-sonnet-5" and a.cost_usd == 0.03
    assert a.synthesis == "High quality, fully valued."   # property → thesis.takeaway
    assert a.thesis.bull_case == "Strong brand."
    assert a.reconciliation[0].signal == "value"
    assert a.reconciliation[0].verdict == "contradicts"


def test_assessment_from_payload_rejects_missing_keys():
    with pytest.raises(ValueError):
        assessment_from_payload({"moat": {}}, ticker="X", as_of="t", accession="a",
                                filing_date="d", model="m", cost_usd=None, stop_reason=None)


def test_assessment_from_payload_rejects_bad_moat_type():
    bad = {**PAYLOAD, "moat": "not-an-object"}
    with pytest.raises(ValueError):
        assessment_from_payload(bad, ticker="X", as_of="t", accession="a",
                                filing_date="d", model="m", cost_usd=None, stop_reason=None)


def test_assessment_from_payload_rejects_missing_thesis():
    payload = {k: v for k, v in PAYLOAD.items() if k != "thesis"}
    with pytest.raises(ValueError):
        assessment_from_payload(payload, ticker="X", as_of="t", accession="a",
                                filing_date="d", model="m", cost_usd=None,
                                stop_reason=None)


def test_assessment_from_payload_missing_reconciliation_is_empty():
    payload = {k: v for k, v in PAYLOAD.items() if k != "reconciliation"}
    a = assessment_from_payload(payload, ticker="X", as_of="t", accession="a",
                                filing_date="d", model="m", cost_usd=None,
                                stop_reason=None)
    assert a.reconciliation == []


def test_findings_are_capped_at_max_risks_and_max_red_flags():
    payload = {**PAYLOAD,
               "risks": [{"claim": f"r{i}", "evidence": ""} for i in range(20)],
               "red_flags": [{"claim": f"f{i}", "evidence": ""} for i in range(20)]}
    a = assessment_from_payload(payload, ticker="X", as_of="t", accession="a",
                                filing_date="d", model="m", cost_usd=None,
                                stop_reason=None, max_risks=3, max_red_flags=2)
    assert len(a.risks) == 3
    assert len(a.red_flags) == 2


def test_findings_default_cap_is_eight():
    """Matches assess.py's prompt default (rcfg.get('max_risks', 8)) — the model
    is told 8 is the ceiling, so the parser's default must agree."""
    payload = {**PAYLOAD, "risks": [{"claim": f"r{i}", "evidence": ""} for i in range(20)]}
    a = assessment_from_payload(payload, ticker="X", as_of="t", accession="a",
                                filing_date="d", model="m", cost_usd=None,
                                stop_reason=None)
    assert len(a.risks) == 8


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


def test_build_thesis_defaults_and_caps():
    from shortlist.research.models import _thesis, Thesis
    t = _thesis({"thesis": {"bull_case": "b", "what_would_change_my_mind":
                            ["a", "b", "c", "d"]}}, max_falsifiers=3)
    assert isinstance(t, Thesis)
    assert t.bull_case == "b" and t.bear_case == "" and t.takeaway == ""
    assert t.what_would_change_my_mind == ["a", "b", "c"]   # capped to 3


def test_build_thesis_requires_dict():
    import pytest
    from shortlist.research.models import _thesis
    with pytest.raises(ValueError):
        _thesis({"thesis": "not-a-dict"})
    with pytest.raises(ValueError):
        _thesis({})                          # missing entirely → not a dict → raise


def test_build_reconciliation_filters_and_caps():
    from shortlist.research.models import _reconciliation, default_valid_signals
    vs = default_valid_signals()
    payload = {"reconciliation": [
        {"signal": "value", "tension": "cheap vs declining",
         "filing_says": "q", "verdict": "contradicts"},
        {"signal": "bogus", "tension": "x", "verdict": "silent"},      # unresolved → drop
        {"signal": "growth", "tension": "y", "verdict": "wat"},        # bad verdict → coerce silent
        "not-a-dict",                                                   # malformed → drop
        {"signal": "flag:activist_13d", "tension": "z", "verdict": "confirms",
         "filing_says": "q2"},
    ]}
    out = _reconciliation(payload, valid_signals=vs, max_conflicts=10)
    sigs = [c.signal for c in out]
    assert sigs == ["value", "growth", "flag:activist_13d"]
    assert out[1].verdict == "silent"        # coerced
    # cap
    capped = _reconciliation(payload, valid_signals=vs, max_conflicts=1)
    assert len(capped) == 1


def test_build_reconciliation_missing_key_is_empty():
    from shortlist.research.models import _reconciliation, default_valid_signals
    assert _reconciliation({}, valid_signals=default_valid_signals()) == []


def test_filing_bundle_haystack_and_cache_key():
    from shortlist.research.models import FilingBundle, FilingText
    tenk = FilingText("AAPL", "acc-10k", "2025-10-31", business="b", mda="m",
                      risk_factors="r")
    b = FilingBundle(tenk=tenk, tenq_mda="quarterly md&a", added_risks_text="new risk",
                     primary_accession="acc-10k", cache_key="acc-10k+acc-10q",
                     filing_date="2025-10-31")
    hay = b.haystack()
    assert "b" in hay and "m" in hay and "r" in hay
    assert "quarterly md&a" in hay and "new risk" in hay
    assert b.cache_key == "acc-10k+acc-10q"

def test_assessment_parses_added_risks():
    from shortlist.research.models import assessment_from_payload
    payload = {
        "business_model_summary": "x", "moat": {"summary": "m"},
        "risks": [], "red_flags": [], "management_capital_allocation": "y",
        "thesis": {"bull_case": "", "bear_case": "", "what_would_change_my_mind": [],
                   "takeaway": "t"},
        "added_risks": [{"claim": "New cyber risk", "evidence": "A breach could harm us."}],
    }
    a = assessment_from_payload(payload, ticker="AAPL", as_of="t", accession="acc",
                                filing_date="d", model="m", cost_usd=None,
                                stop_reason=None)
    assert len(a.added_risks) == 1
    assert a.added_risks[0].claim == "New cyber risk"
    assert a.cache_key == ""        # default; set by assess(), not the payload

def test_assessment_added_risks_capped():
    from shortlist.research.models import assessment_from_payload
    payload = {
        "business_model_summary": "x", "moat": {"summary": "m"},
        "risks": [], "red_flags": [], "management_capital_allocation": "y",
        "thesis": {"bull_case": "", "bear_case": "", "what_would_change_my_mind": [],
                   "takeaway": "t"},
        "added_risks": [{"claim": f"r{i}", "evidence": "e"} for i in range(20)],
    }
    a = assessment_from_payload(payload, ticker="A", as_of="t", accession="acc",
                                filing_date="d", model="m", cost_usd=None,
                                stop_reason=None, max_added_risks=3)
    assert len(a.added_risks) == 3

def test_text_similarity_is_not_in_the_haystack():
    """A computed number must never be quotable as a filing fact."""
    from shortlist.research.models import FilingBundle, FilingText
    tenk = FilingText("A", "acc", "2026-01-01", business="b", mda="m", risk_factors="r")
    b = FilingBundle(tenk=tenk, primary_accession="acc", cache_key="acc",
                     filing_date="2026-01-01", text_similarity=0.42)
    assert b.text_similarity == 0.42
    # Assert on the source of truth, not on one formatting of the number: the
    # haystack is exactly the filing texts, nothing else.
    assert b.haystack() == "b\n\nm\n\nr"


def test_assessment_added_risks_tolerates_malformed_items():
    # advisory list: a non-dict item is skipped, NOT raised (must not sink the brief)
    from shortlist.research.models import assessment_from_payload
    payload = {
        "business_model_summary": "x", "moat": {"summary": "m"},
        "risks": [], "red_flags": [], "management_capital_allocation": "y",
        "thesis": {"bull_case": "", "bear_case": "", "what_would_change_my_mind": [],
                   "takeaway": "t"},
        "added_risks": ["not a dict", {"claim": "real", "evidence": "e"}],
    }
    a = assessment_from_payload(payload, ticker="A", as_of="t", accession="acc",
                                filing_date="d", model="m", cost_usd=None,
                                stop_reason=None)
    assert len(a.added_risks) == 1 and a.added_risks[0].claim == "real"


def test_segments_label_each_document_and_haystack_is_their_join():
    """spec §3.4: haystack() becomes the join of segments(), so every existing
    caller is unchanged and adding a segment cannot change what verifies."""
    from shortlist.research.models import EightKText, FilingBundle, FilingText
    tenk = FilingText("A", "acc", "2026-01-01", business="b", mda="m", risk_factors="r")
    e = EightKText(accession="a1", filed="2026-07-30", items="2.02,9.01",
                   label="8-K 2026-07-30 (Item 2.02, EX-99.1)", text="revenue rose")
    b = FilingBundle(tenk=tenk, primary_accession="acc", cache_key="acc",
                     filing_date="2026-01-01", tenq_mda="q", added_risks_text="new",
                     eightks=[e])
    assert b.segments() == [("10-K", "b\n\nm\n\nr"), ("10-Q MD&A", "q"),
                            ("newly disclosed risks", "new"),
                            ("8-K 2026-07-30 (Item 2.02, EX-99.1)", "revenue rose")]
    assert b.haystack() == "\n\n".join(t for _, t in b.segments())


def test_segments_drops_empty_documents_so_no_label_matches_the_empty_string():
    from shortlist.research.models import FilingBundle, FilingText
    tenk = FilingText("A", "acc", "2026-01-01", business="b", mda="m", risk_factors="r")
    b = FilingBundle(tenk=tenk, primary_accession="acc", cache_key="acc",
                     filing_date="2026-01-01")
    assert b.segments() == [("10-K", "b\n\nm\n\nr")]
    assert b.haystack() == "b\n\nm\n\nr"        # unchanged with no 8-K, no 10-Q

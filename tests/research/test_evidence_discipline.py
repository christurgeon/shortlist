"""Grounding discipline for the two narrative sections that used to be bare prose.

`moat.sources` and `management_findings` carry {claim, evidence} pairs like risks do,
but with ONE deliberate difference: an empty `evidence` is a valid answer there (the
claim is the analyst's inference, or rests on a filing section the haystack never
sends — Item 5 and the financial statements are not in `FilingBundle.segments()`).
Those items are kept and labelled, never silently dropped and never counted as
fabrications. See docs/audits/2026-08-17-moat-management-evidence-design.md.
"""

from shortlist.research import report
from shortlist.research.assess import _locate, _norm, _verify_grounding
from shortlist.research.models import (
    Finding,
    Moat,
    QualitativeAssessment,
    Thesis,
    _evidence_pairs,
    assessment_from_payload,
)

PAYLOAD = {
    "business_model_summary": "Sells devices.",
    "moat": {"summary": "Ecosystem.",
             "sources": [{"claim": "Long customer contracts",
                          "evidence": "our customer agreements typically have terms of three to five years"},
                         {"claim": "Brand commands a price premium", "evidence": ""}],
             "trajectory": "stable"},
    "risks": [],
    "red_flags": [],
    "management_capital_allocation": "Returns most free cash flow to shareholders.",
    "management_findings": [
        {"claim": "Repurchased $89.3B of stock",
         "evidence": "we repurchased 89.3 billion of our common stock during the year"}],
    "thesis": {"bull_case": "b", "bear_case": "c", "what_would_change_my_mind": [],
               "takeaway": "t"},
}


class _Bundle:
    """Duck-typed FilingBundle: one labelled segment, like the real one."""

    def __init__(self, text="our customer agreements typically have terms of three "
                           "to five years and renew automatically"):
        self._text = text

    def segments(self):
        return [("10-K", self._text)]

    def haystack(self):
        return self._text


def _assessment(**kw):
    base = dict(
        ticker="AAPL", as_of="t", filing_accession="a", filing_date="d", model="m",
        moat=Moat(summary="Ecosystem."), thesis=Thesis(),
    )
    base.update(kw)
    return QualitativeAssessment(**base)


# --- parse tolerance -------------------------------------------------------

def test_evidence_pairs_accepts_both_shapes_and_skips_junk():
    """The legacy bare-string shape must never raise: `_finding_from` would throw
    AttributeError, which assess.py's parse-retry does NOT catch, dropping the
    whole brief over a cosmetically-old moat list."""
    out = _evidence_pairs(["brand", {"claim": "scale", "evidence": "we operate 42 centers"}, 17, None])
    assert [f.claim for f in out] == ["brand", "scale"]
    assert out[0].evidence == ""          # a bare string carries no quote
    assert out[1].evidence == "we operate 42 centers"
    assert all(isinstance(f, Finding) for f in out)


def test_evidence_pairs_respects_its_cap():
    out = _evidence_pairs(["a", "b", "c"], limit=2)
    assert [f.claim for f in out] == ["a", "b"]


def test_payload_builds_findings_for_moat_sources_and_management():
    a = assessment_from_payload(PAYLOAD, ticker="AAPL", as_of="t", accession="a",
                                filing_date="d", model="m", cost_usd=None,
                                stop_reason=None)
    assert all(isinstance(s, Finding) for s in a.moat.sources)
    assert a.moat.sources[0].claim == "Long customer contracts"
    assert a.moat.sources[1].evidence == ""
    assert len(a.management_findings) == 1
    assert a.management_findings[0].claim == "Repurchased $89.3B of stock"


def test_management_findings_is_optional_and_never_required():
    """New key: a model that omits it must still produce a brief."""
    payload = {k: v for k, v in PAYLOAD.items() if k != "management_findings"}
    a = assessment_from_payload(payload, ticker="X", as_of="t", accession="a",
                                filing_date="d", model="m", cost_usd=None,
                                stop_reason=None)
    assert a.management_findings == []


def test_legacy_bare_string_sources_still_parse():
    payload = {**PAYLOAD, "moat": {"summary": "s", "sources": ["brand", "scale"],
                                   "trajectory": "stable"}}
    a = assessment_from_payload(payload, ticker="X", as_of="t", accession="a",
                                filing_date="d", model="m", cost_usd=None,
                                stop_reason=None)
    assert [s.claim for s in a.moat.sources] == ["brand", "scale"]
    assert all(s.evidence == "" for s in a.moat.sources)


# --- grounding -------------------------------------------------------------

def test_empty_evidence_never_locates():
    """_locate's length guard is the ONLY thing stopping `"" in hay` (always True)
    from verifying an empty quote against the first segment. Pin it."""
    assert _norm("") == ""
    assert _norm("   \n\t ") == ""
    assert _locate("", [("10-K", "any haystack text at all")]) is None


def test_a_quoted_moat_source_verifies_and_records_its_source():
    a = _assessment(moat=Moat(summary="s", sources=[
        Finding("Long contracts",
                "our customer agreements typically have terms of three to five years")]))
    _verify_grounding(a, _Bundle())
    assert a.moat.sources[0].verified is True
    assert a.moat.sources[0].source == "10-K"
    assert a.unverified_count == 0
    assert a.inference_count == 0


def test_a_fabricated_moat_quote_is_unverified_and_counted():
    a = _assessment(moat=Moat(summary="s", sources=[
        Finding("Invented", "a sentence that appears nowhere in the filing")]))
    _verify_grounding(a, _Bundle())
    assert a.moat.sources[0].verified is False
    assert a.moat.sources[0].source == ""
    assert a.unverified_count == 1
    assert a.inference_count == 0


def test_a_declared_inference_is_not_a_fabrication():
    """The whole point: an empty quote is a CORRECT answer here. It must not inflate
    `unverified_count`, which report.py renders as 'could not be verified' — the one
    number a reader uses to distrust a brief."""
    a = _assessment(moat=Moat(summary="s", sources=[Finding("Brand equity", "")]),
                    management_findings=[Finding("Disciplined capital allocation", "  ")])
    _verify_grounding(a, _Bundle())
    assert a.moat.sources[0].verified is False
    assert a.management_findings[0].verified is False
    assert a.unverified_count == 0            # neither is a fabrication
    assert a.inference_count == 2             # whitespace-only counts as inference


def test_management_findings_are_verified_like_any_other():
    a = _assessment(management_findings=[
        Finding("Contracts run three to five years",
                "our customer agreements typically have terms of three to five years")])
    _verify_grounding(a, _Bundle())
    assert a.management_findings[0].verified is True
    assert a.management_findings[0].source == "10-K"


# --- rendering -------------------------------------------------------------

def test_the_three_render_states_are_distinguishable():
    a = _assessment(
        moat=Moat(summary="Ecosystem.", trajectory="stable", sources=[
            Finding("Quoted", "a real quote", verified=True, source="10-K"),
            Finding("Fabricated", "not in the filing", verified=False),
            Finding("Inferred", "", verified=False)]),
        management_capital_allocation="Returns cash.",
        management_findings=[Finding("Buybacks", "", verified=False)],
        inference_count=2)
    md = report.to_markdown(a)
    assert "## Sources of advantage" in md
    assert "## Management & capital allocation" in md
    assert "> a real quote" in md                       # verified quote shown
    assert "_(unverified)_" in md                       # fabrication still called out
    assert "_(unquoted — inference or from a section not provided)_" in md
    assert "2 claim(s) stated without a filing quote" in md


def test_an_inference_never_claims_a_verified_source():
    a = _assessment(moat=Moat(summary="s", sources=[Finding("Inferred", "")]))
    md = report.to_markdown(a)
    assert "verified against" not in md


def test_rendering_a_legacy_bare_string_source_does_not_raise():
    """Rendering must never be the thing that drops a brief. Any caller can still
    construct Moat(sources=["brand"]); a str has no .verified."""
    a = _assessment(moat=Moat(summary="s", sources=["brand"]))
    md = report.to_markdown(a)
    assert "- **brand** _(unquoted — inference or from a section not provided)_" in md


def test_brief_with_no_new_lists_renders_unchanged():
    """A brief carrying neither list must not sprout empty sections or a zero count."""
    a = _assessment(management_capital_allocation="Buybacks.")
    md = report.to_markdown(a)
    assert "## Sources of advantage" not in md
    assert "stated without a filing quote" not in md

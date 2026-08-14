"""10-Q Part II Item 1A — the quarter's risk-factor CHANGES.

Why a diff and not the raw section: measured across 10 large caps (2026-08-14 probe),
the raw Part II Item 1A runs 204 -> 84,281 chars because four of ten names restate
EVERY risk factor quarterly (GILD 84K, NVDA 43K, AAPL 19K, DIS 17.6K) — text the 10-K
Item 1A already carries. Diffing it against the 10-K collapses every one of those to
under 3K (NVDA 2,949; AAPL 2,324; DIS 1,629; GILD 1,602) while keeping the genuinely
new blocks, and boilerplate-only filers collapse to ~200 chars or nothing.
"""
import pytest

from shortlist.research.filings import _tenq_added_risks
from shortlist.research.models import FilingBundle, FilingText

CFG = {"research": {"tenq_risk_update": {"enabled": True}}}


class _FakeTenQ:
    """Real TenQ has NO `risk_factors` attribute (verified live on 10/10 names) —
    Part II Item 1A comes out only via get_item_with_part."""
    def __init__(self, item_1a, expect=("Part II", "Item 1A")):
        self._text, self._expect = item_1a, expect

    def get_item_with_part(self, part, item, markdown=True):
        return self._text if (part, item) == self._expect else None


def _blocks(*paras):
    return "\n\n".join(paras)


OLD_A = "Supply chain disruption could reduce our ability to ship product on time."
OLD_B = "Currency fluctuation may adversely affect our reported results."
NEW_A = "Access to sufficient AI compute capacity may constrain our product roadmap."


def test_only_the_new_blocks_survive_the_diff():
    """The restating filer (NVDA/GILD shape): the 10-Q repeats the whole 10-K
    Item 1A plus one new block. Only the new block reaches the model."""
    tenq = _FakeTenQ(_blocks(OLD_A, OLD_B, NEW_A))
    out = _tenq_added_risks(tenq, _blocks(OLD_A, OLD_B), CFG)
    assert NEW_A in out
    assert OLD_A not in out and OLD_B not in out


def test_a_pure_boilerplate_update_stays_tiny_rather_than_being_dropped():
    """BA/JPM shape: 'no material changes ... see the 10-K'. It is short and carries
    no new risk, but it IS the disclosure, so it passes through rather than being
    special-cased on a 'boilerplate' regex — NVDA's section opens with the very same
    sentence and then lists 2,949 chars of genuinely new risk factors."""
    boiler = ("There have been no material changes in our risk factors from those "
              "disclosed in Part I, Item 1A of our Annual Report on Form 10-K.")
    out = _tenq_added_risks(_FakeTenQ(boiler), _blocks(OLD_A, OLD_B), CFG)
    assert out.strip() == boiler
    assert len(out) < 400


def test_no_new_risk_yields_empty_string():
    """KO/TSLA shape — the diff finds nothing, so the prompt gains nothing."""
    assert _tenq_added_risks(_FakeTenQ(_blocks(OLD_A, OLD_B)), _blocks(OLD_A, OLD_B), CFG) == ""


def test_missing_tenk_baseline_abstains_rather_than_dumping_the_whole_section():
    """No 10-K Item 1A means no baseline to diff against. Abstain — emitting the raw
    section here is exactly the 84K-char dump the diff exists to prevent."""
    assert _tenq_added_risks(_FakeTenQ(_blocks(OLD_A, NEW_A)), "", CFG) == ""


def test_absent_item_1a_and_extraction_failure_are_both_empty():
    class _Boom:
        def get_item_with_part(self, *a, **k):
            raise RuntimeError("edgartools item boundary error")
    assert _tenq_added_risks(_FakeTenQ(None), _blocks(OLD_A), CFG) == ""
    assert _tenq_added_risks(object(), _blocks(OLD_A), CFG) == ""
    assert _tenq_added_risks(_Boom(), _blocks(OLD_A), CFG) == ""


def test_output_is_capped_independently_of_the_yoy_risk_diff():
    """Its own config block: tuning the quarterly update must not move the YoY 10-K
    diff (`research.risk_diff`), which feeds a different prompt section."""
    long_new = "Novel quarterly risk. " * 500
    cfg = {"research": {"tenq_risk_update": {"max_chars": 100},
                        "risk_diff": {"max_chars": 12000}}}
    out = _tenq_added_risks(_FakeTenQ(_blocks(OLD_A, long_new)), _blocks(OLD_A), cfg)
    assert 0 < len(out) <= 100


def test_disabled_block_is_a_byte_identical_no_op():
    tenq = _FakeTenQ(_blocks(OLD_A, NEW_A))
    off = {"research": {"tenq_risk_update": {"enabled": False}}}
    assert _tenq_added_risks(tenq, _blocks(OLD_A), off) == ""
    # ...and absent config ships ON (the feature's default), like `eightk`.
    assert NEW_A in _tenq_added_risks(tenq, _blocks(OLD_A), {})


@pytest.mark.parametrize("text", ["", None])
def test_bundle_segment_appears_only_when_there_is_text(text):
    """Grounding provenance: the update is filing text, so it enters the haystack —
    but as its OWN segment, so a verified quote is attributed to the 10-Q rather
    than silently widening '10-K' to cover it."""
    b = FilingBundle(tenk=FilingText(ticker="X", accession="a", filing_date="d",
                                     business="Business."),
                     primary_accession="a", cache_key="a", filing_date="d",
                     tenq_added_risks=text or "")
    assert all(lbl != "10-Q Part II Item 1A" for lbl, _ in b.segments())


def test_bundle_segment_is_labelled_and_enters_the_haystack():
    b = FilingBundle(tenk=FilingText(ticker="X", accession="a", filing_date="d",
                                     business="Business."),
                     primary_accession="a", cache_key="a", filing_date="d",
                     tenq_added_risks=NEW_A)
    assert ("10-Q Part II Item 1A", NEW_A) in b.segments()
    assert NEW_A in b.haystack()


def test_prompt_section_is_omitted_when_empty_and_present_when_not():
    from shortlist.research.assess import _build_user_prompt
    tenk = FilingText(ticker="X", accession="a", filing_date="d", business="Business.")
    base = FilingBundle(tenk=tenk, primary_accession="a", cache_key="a", filing_date="d")
    with_update = FilingBundle(tenk=tenk, primary_accession="a", cache_key="a",
                               filing_date="d", tenq_added_risks=NEW_A)
    empty_prompt = _build_user_prompt(base, {})
    assert "10-Q" not in empty_prompt.split("Return at most")[0]
    filled = _build_user_prompt(with_update, {})
    assert NEW_A in filled
    # byte-identical: the ONLY difference is the new section
    assert filled.replace(
        f"=== LATEST 10-Q — PART II ITEM 1A (RISK FACTORS NEW SINCE THE 10-K) ==="
        f"\n{NEW_A}\n\n", "") == empty_prompt


def test_verified_quote_is_attributed_to_the_10q_not_the_10k():
    from shortlist.research.assess import _verify_grounding
    from shortlist.research.models import Finding, QualitativeAssessment
    b = FilingBundle(tenk=FilingText(ticker="X", accession="a", filing_date="d",
                                     business="We make widgets."),
                     primary_accession="a", cache_key="a", filing_date="d",
                     tenq_added_risks=NEW_A)
    a = QualitativeAssessment(ticker="X", as_of="2026-08-14", filing_accession="a",
                              filing_date="d", model="m")
    a.added_risks = [Finding(claim="New compute constraint", evidence=NEW_A)]
    _verify_grounding(a, b)
    assert a.added_risks[0].verified is True
    assert a.added_risks[0].source == "10-Q Part II Item 1A"

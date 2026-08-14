"""A 10-Q whose MD&A could not be extracted must SAY so, not silently omit the section.

Measured 2026-08-14 over 228 filings: 5 (2.19% — BLK, C, MTRN, MHO, MED) return 0 chars
for Part I Item 2 because edgartools misses the item heading. Those briefs carried no
quarterly MD&A and nothing told the model, so it could not distinguish "nothing changed
this quarter" from "we could not read it".

The recovery alternative is KILLED on evidence — see
docs/audits/2026-08-14-tenq-mda-recovery-kill.md.
"""
from shortlist.research.assess import _build_user_prompt
from shortlist.research.models import FilingBundle, FilingText

TENK = FilingText(ticker="X", accession="a", filing_date="d", business="We make widgets.")


def _bundle(**kw):
    return FilingBundle(tenk=TENK, primary_accession="a", cache_key="a",
                        filing_date="d", **kw)


def test_a_located_10q_with_unextractable_mda_is_declared_a_data_gap():
    p = _build_user_prompt(_bundle(tenq_accession="0000019617-26-000123"), {})
    assert "MD&A UNAVAILABLE" in p
    assert "data gap" in p


def test_no_10q_at_all_says_nothing_new():
    """Absent a 10-Q there is nothing to report as broken — the brief is simply
    annual-only, which is the pre-existing behaviour and must stay byte-identical."""
    p = _build_user_prompt(_bundle(), {})
    assert "UNAVAILABLE" not in p


def test_a_working_mda_is_byte_identical_to_before():
    p = _build_user_prompt(_bundle(tenq_mda="Revenue rose.",
                                   tenq_accession="0000019617-26-000123"), {})
    assert "UNAVAILABLE" not in p
    assert "=== LATEST 10-Q — MD&A (current quarter) ===\nRevenue rose." in p


def test_the_notice_is_prompt_only_and_never_enters_the_haystack():
    """It is a computed status line, not filing text. If it reached segments() a model
    could quote it back and _verify_grounding would mark it verified against a filing."""
    b = _bundle(tenq_accession="0000019617-26-000123")
    assert all(label != "10-Q MD&A" for label, _ in b.segments())
    assert "UNAVAILABLE" not in b.haystack()

"""How a controls finding reaches the model: as a rendered prompt section AND as a
grounding segment, with the derived verdict kept out of the haystack.

The two must move together. A segment the model never saw could still verify a
quote — which would make `verified=True` mean the opposite of what it says.
"""
from __future__ import annotations

from shortlist.research.assess import _build_user_prompt
from shortlist.research.models import ControlsFinding, FilingBundle, FilingText

_QUOTE = ("Based on that evaluation, management concluded that our internal control "
          "over financial reporting was not effective as of December 31, 2025.")


def _finding():
    return ControlsFinding(form="10-K", accession="acc", basis="icfr",
                           as_of="2025-12-31", label="10-K controls conclusion",
                           quote=_QUOTE)


def _bundle(controls=None):
    tenk = FilingText("XYZ", "acc", "2026-02-20", business="biz", mda="mda",
                      risk_factors="rf")
    return FilingBundle(tenk=tenk, primary_accession="acc", cache_key="acc",
                        filing_date="2026-02-20", controls=controls)


def test_the_quote_is_a_labelled_grounding_segment():
    segs = dict(_bundle(_finding()).segments())
    assert segs["10-K controls conclusion"] == _QUOTE


def test_no_finding_leaves_the_segments_untouched():
    assert _bundle(None).segments() == _bundle(None).segments()
    assert all(label != "10-K controls conclusion"
               for label, _ in _bundle(None).segments())


def test_the_quote_is_rendered_in_the_prompt_it_can_verify_against():
    prompt = _build_user_prompt(_bundle(_finding()), {"research": {}}, card=None)
    assert "INTERNAL CONTROL CONCLUSION" in prompt
    assert _QUOTE in prompt


def test_the_derived_verdict_rides_outside_the_haystack():
    bundle = _bundle(_finding())
    prompt = _build_user_prompt(bundle, {"research": {}}, card=None)
    assert "context only" in prompt and "NOT effective as of 2025-12-31" in prompt
    # the verdict sentence must not be quotable as a filing fact
    assert "NOT effective as of 2025-12-31" not in bundle.haystack()


def test_a_clean_filer_keeps_the_prompt_byte_identical():
    """5.3% of large/small-mid names have a finding, so the other 94.7% must pay
    nothing at all for this feature."""
    assert (_build_user_prompt(_bundle(None), {"research": {}}, card=None)
            == _build_user_prompt(_bundle(), {"research": {}}, card=None))
    assert "INTERNAL CONTROL CONCLUSION" not in _build_user_prompt(
        _bundle(None), {"research": {}}, card=None)

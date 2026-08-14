"""Live-EDGAR contract test for the 10-Q Part II Item 1A reader.

Pins the edgartools shape the pure tests can only fake: that Part II Item 1A comes
out of `get_item_with_part("Part II", "Item 1A")` and NOT off a `risk_factors`
attribute (TenQ has none — verified on 10/10 names, 2026-08-14), and that the diff
against the 10-K keeps the section small. CI pins our parse shape, not SEC's or
edgartools' — and `standard_concept` drift has broken extraction once already.

Skipped without SEC_IDENTITY (the [edgar] extra).
"""
import os

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.getenv("SEC_IDENTITY"), reason="needs SEC_IDENTITY + edgar extra"),
]

# NVDA restates its entire Item 1A quarterly (43,097 raw chars measured 2026-08-14),
# so it exercises the diff rather than the trivial boilerplate path.
RESTATING = "NVDA"


def test_tenq_exposes_part_ii_item_1a_only_via_get_item_with_part():
    from edgar import Company, set_identity
    set_identity(os.environ["SEC_IDENTITY"])
    q = Company(RESTATING).get_filings(form="10-Q").latest(1).obj()
    # The attribute route that works on TenK must NOT be relied on here.
    assert not getattr(q, "risk_factors", "")
    raw = q.get_item_with_part("Part II", "Item 1A", markdown=True)
    assert raw and len(str(raw)) > 5000        # a restating filer, not a pointer


def test_the_diff_keeps_a_restating_filer_small_and_grounded():
    from pathlib import Path

    import yaml

    from shortlist.research.filings import fetch_bundle
    # Repo-relative, not cwd-relative: the test must not depend on where pytest ran.
    with (Path(__file__).resolve().parent.parent / "config.yaml").open() as fh:
        cfg = yaml.safe_load(fh)
    b = fetch_bundle(RESTATING, config=cfg)
    assert b is not None
    update = b.tenq_added_risks
    assert update, "the restating filer should yield new blocks, not an empty diff"
    # The whole point: the raw section is tens of thousands of chars; the diff is not.
    assert len(update) <= cfg["research"]["tenq_risk_update"]["max_chars"]
    # Provenance: its own labelled segment, so a verified quote is not credited to the 10-K.
    assert ("10-Q Part II Item 1A", update) in b.segments()
    assert update in b.haystack()

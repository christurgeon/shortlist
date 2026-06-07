from shortlist.research.filings import _build_filing_text
from shortlist.research.models import FilingText


class _FakeTenK:
    def __init__(self, business=None, mda=None, risk=None):
        self.business = business
        self.management_discussion = mda
        self.risk_factors = risk


def test_build_filing_text_maps_sections():
    tenk = _FakeTenK(business="We make widgets.", mda="Revenue rose.", risk="Supply risk.")
    ft = _build_filing_text("AAPL", "0000320193-25-000123", "2025-10-31", tenk)
    assert isinstance(ft, FilingText)
    assert ft.ticker == "AAPL"
    assert ft.accession == "0000320193-25-000123"
    assert ft.filing_date == "2025-10-31"
    assert ft.business == "We make widgets."
    assert ft.mda == "Revenue rose."
    assert ft.risk_factors == "Supply risk."


def test_build_filing_text_tolerates_missing_sections():
    tenk = _FakeTenK(business="Only business section.", mda=None, risk=None)
    ft = _build_filing_text("X", "a", "d", tenk)
    assert ft.business == "Only business section."
    assert ft.mda == "" and ft.risk_factors == ""
    assert ft.has_content() is True


def test_build_filing_text_all_empty_has_no_content():
    ft = _build_filing_text("X", "a", "d", _FakeTenK())
    assert ft.has_content() is False


def test_cap_sections_prefix_trims_and_is_noop_when_absent():
    from shortlist.research.filings import cap_sections
    from shortlist.research.models import FilingText
    f = FilingText(ticker="X", accession="a", filing_date="d",
                   business="b"*100, mda="m"*100, risk_factors="r"*100)
    # absent caps -> unchanged (same object semantics: identical content)
    assert cap_sections(f, None) is f
    assert cap_sections(f, {}) is f
    capped = cap_sections(f, {"business": 10, "risk_factors": 40})
    assert len(capped.business) == 10           # trimmed
    assert len(capped.mda) == 100               # no cap given -> untouched
    assert len(capped.risk_factors) == 40
    # prompt == haystack consistency: combined() reflects the trim
    assert capped.business in capped.combined()


def test_cap_sections_under_cap_unchanged():
    from shortlist.research.filings import cap_sections
    from shortlist.research.models import FilingText
    f = FilingText(ticker="X", accession="a", filing_date="d",
                   business="short", mda="short", risk_factors="short")
    capped = cap_sections(f, {"business": 1000})
    assert capped.business == "short"

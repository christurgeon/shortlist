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

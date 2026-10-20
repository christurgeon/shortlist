"""Live: the submissions-index file number on a 13D/13G/144 row marks whether the company
is the subject, checked against the filing header. Pinned by accession so the assertions
do not depend on what the latest filings happen to be."""
import os
from datetime import date, timedelta

import pytest

from shortlist.data.sources.edgar import is_filed_about_another_issuer

_OWNERSHIP_FORMS = ["SC 13D", "SC 13G", "SCHEDULE 13D", "SCHEDULE 13G", "144"]

# (ticker, accession, the company is the subject)
_PINNED = [
    ("BRK-B", "0001193125-26-417254", False),   # Berkshire 13G/A on Lennar
    ("BAC", "0000070858-26-000484", False),     # BAC 13D/A on a Nuveen fund
    ("GS", "0000886982-26-000526", False),      # GS 13G/A on Nebius
    ("XOM", "0001193125-26-232863", False),     # XOM 144 as seller of ProPetro
    ("BAC", "0002100119-26-000199", True),
    ("XOM", "0000093751-26-000461", True),
    ("JNJ", "0001959173-26-006622", True),      # 144 on JNJ
    ("STT", "0000093751-23-000424", True),      # STT is both filer and subject
]


@pytest.fixture(scope="module")
def _identity():
    from edgar import set_identity
    set_identity(os.environ["SEC_IDENTITY"])


@pytest.mark.parametrize("ticker,accession,about_company", _PINNED)
@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("RUN_LIVE_EDGAR"), reason="live SEC call; set RUN_LIVE_EDGAR=1")
def test_live_file_number_marks_the_subject(_identity, ticker, accession, about_company):
    from edgar import Company

    company = Company(ticker)
    filing = company.get_filings(form=_OWNERSHIP_FORMS).get(accession)
    assert filing is not None
    assert (not is_filed_about_another_issuer(filing.form, filing.file_number)) is about_company

    subjects = {int(s.company_information.cik) for s in filing.header.subject_companies}
    assert subjects
    assert (int(company.cik) in subjects) is about_company


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("RUN_LIVE_EDGAR"), reason="live SEC call; set RUN_LIVE_EDGAR=1")
def test_live_form4_window_shape(_identity):
    from edgar import Company

    cutoff = date.today() - timedelta(days=183)
    company = Company("JNJ")
    window = company.get_filings(form="4", filing_date=f"{cutoff.isoformat()}:",
                                 sort_by=[("filing_date", "descending")])
    dates = [f.filing_date for f in window]
    assert dates and all(d >= cutoff for d in dates)
    assert dates == sorted(dates, reverse=True)

    one = window.head(1)
    assert len(one) == 1 and len(list(one)) == 1

    today = company.get_filings(form="4", filing_date=f"{date.today().isoformat()}:")
    assert today is not None

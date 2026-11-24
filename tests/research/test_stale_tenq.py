"""`fetch_bundle` drops a 10-Q that the bundled 10-K already supersedes.

After a 10-K is filed, the newest 10-Q still covers a quarter inside that fiscal year
until the next 10-Q lands. Shown as "LATEST 10-Q — MD&A (current quarter)", it would be
older than the annual report it sits beside.
"""
from datetime import date

import pytest

from shortlist.research import eightk, filings
from shortlist.research.assess import _build_user_prompt
from shortlist.research.models import FilingText

TENK_ACC = "0000320193-26-000100"
TENQ_ACC = "0000320193-26-000080"


class _TenKFiling:
    def __init__(self, filing_date, report_date):
        self.form, self.accession_no = "10-K", TENK_ACC
        self.filing_date, self.report_date = filing_date, report_date


class _TenQObj:
    notes = None

    def get_item_with_part(self, part, item, markdown=True):
        if (part, item) == ("Part I", "Item 2"):
            return "Quarterly MD&A narrative."
        if (part, item) == ("Part II", "Item 1A"):
            return "A brand new quarterly risk about tariffs on imported components."
        return None


class _TenQFiling:
    def __init__(self, filing_date, report_date):
        self.form, self.accession_no = "10-Q", TENQ_ACC
        self.filing_date, self.report_date = filing_date, report_date
        self.parsed = 0

    def obj(self):
        self.parsed += 1
        return _TenQObj()


@pytest.fixture
def bundle_for(monkeypatch):
    """fetch_bundle with a 10-K filing row and a 10-Q filing row, fully offline."""
    import edgar

    monkeypatch.setattr(filings, "_prior_year_sections", lambda *a, **k: ("", ""))
    monkeypatch.setattr(eightk, "fetch_eightks", lambda *a, **k: [])

    def _build(tenk_row, tenq_row):
        tenk = FilingText("AAPL", TENK_ACC, str(getattr(tenk_row, "filing_date", "")),
                          business="b", mda="m", risk_factors="An old annual risk.")
        monkeypatch.setattr(filings, "_fetch_10k_parsed",
                            lambda *a, **k: (tenk, None, tenk_row, None))

        class _Filings:
            def latest(self, n=1):
                return tenq_row

        class _Company:
            def __init__(self, *a, **k):
                pass

            def get_filings(self, form=None):
                assert form == "10-Q"
                return _Filings()

        monkeypatch.setattr(edgar, "Company", _Company)
        return filings.fetch_bundle("AAPL")

    return _build


def _assert_no_tenq(bundle):
    assert bundle.tenq_mda == ""
    assert bundle.tenq_accession == ""
    assert bundle.tenq_added_risks == ""
    assert not [n for n in bundle.debt_notes if n.form == "10-Q"]
    assert bundle.cache_key == TENK_ACC
    assert not [label for label, _ in bundle.segments() if label.startswith("10-Q")]
    assert "10-Q" not in _build_user_prompt(bundle, {"research": {}}, card=None)


def test_a_10q_for_a_quarter_inside_the_10k_year_is_dropped(bundle_for, capsys):
    q = _TenQFiling("2026-07-31", "2026-06-27")
    bundle = bundle_for(_TenKFiling("2026-11-03", "2026-09-26"), q)
    _assert_no_tenq(bundle)
    assert q.parsed == 0
    err = capsys.readouterr().err
    assert "AAPL" in err and "2026-06-27" in err and "2026-09-26" in err


def test_a_10q_for_a_later_quarter_is_kept(bundle_for):
    bundle = bundle_for(_TenKFiling("2026-02-20", "2025-12-31"),
                        _TenQFiling("2026-10-28", "2026-10-02"))
    assert bundle.tenq_mda == "Quarterly MD&A narrative."
    assert bundle.tenq_accession == TENQ_ACC
    assert bundle.cache_key == f"{TENK_ACC}+{TENQ_ACC}"


def test_a_10q_with_the_10k_period_is_dropped(bundle_for):
    _assert_no_tenq(bundle_for(_TenKFiling("2026-11-03", "2026-09-26"),
                               _TenQFiling("2026-11-03", "2026-09-26")))


def test_periods_beat_filing_dates(bundle_for):
    """A late 10-Q filed after the 10-K still covers a superseded quarter."""
    _assert_no_tenq(bundle_for(_TenKFiling("2026-11-03", "2026-09-26"),
                               _TenQFiling("2026-11-20", "2026-06-27")))


def test_a_10q_filed_by_the_10k_period_end_is_dropped_without_its_own_period(bundle_for):
    _assert_no_tenq(bundle_for(_TenKFiling("2026-11-03", "2026-09-26"),
                               _TenQFiling("2026-07-31", "")))


@pytest.mark.parametrize("tenk_period,tenq_period", [("", "2026-06-27"), (None, None)])
def test_a_missing_10k_period_keeps_the_10q(bundle_for, tenk_period, tenq_period):
    bundle = bundle_for(_TenKFiling("2026-11-03", tenk_period),
                        _TenQFiling("2026-07-31", tenq_period))
    assert bundle.tenq_accession == TENQ_ACC


def test_a_late_10k_keeps_a_newer_quarter_filed_before_it(bundle_for):
    """A delinquent filer's FY 10-K can follow its next Q1 10-Q."""
    bundle = bundle_for(_TenKFiling("2024-08-10", "2023-12-31"),
                        _TenQFiling("2024-05-15", ""))
    assert bundle.tenq_accession == TENQ_ACC


def test_a_missing_period_and_a_same_day_filing_keeps_the_10q(bundle_for):
    bundle = bundle_for(_TenKFiling("2026-11-03", ""), _TenQFiling("2026-11-03", ""))
    assert bundle.tenq_accession == TENQ_ACC


def test_no_dates_at_all_keeps_the_10q(bundle_for):
    bundle = bundle_for(_TenKFiling("", ""), _TenQFiling("", ""))
    assert bundle.tenq_accession == TENQ_ACC


def test_no_10k_filing_row_keeps_the_10q(bundle_for):
    bundle = bundle_for(None, _TenQFiling("2026-07-31", "2026-06-27"))
    assert bundle.tenq_accession == TENQ_ACC


def test_date_typed_filing_dates_compare_on_the_fallback(bundle_for, capsys):
    """edgartools gives `filing_date` as a datetime.date, `report_date` as a str."""
    _assert_no_tenq(bundle_for(_TenKFiling(date(2026, 11, 3), "2026-09-26"),
                               _TenQFiling(date(2026, 7, 31), "")))
    err = capsys.readouterr().err
    assert "2026-07-31" in err and "2026-09-26" in err and "no period of report" in err


def test_a_10q_amendment_is_not_bundled_and_not_reported_as_stale(bundle_for, capsys):
    q = _TenQFiling("2026-12-01", "2026-12-27")
    q.form = "10-Q/A"
    _assert_no_tenq(bundle_for(_TenKFiling("2026-11-03", "2026-09-26"), q))
    assert q.parsed == 0
    assert "omitted" not in capsys.readouterr().err

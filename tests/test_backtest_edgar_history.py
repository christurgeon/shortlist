from datetime import date
from types import SimpleNamespace

from shortlist.backtest.edgar_history import fetch_activist_window, group_by_day


class _Hdr:
    def __init__(self, cik, subject, activist):
        ci = SimpleNamespace(cik=cik, name=subject)
        self.subject_companies = [SimpleNamespace(company_information=ci)]
        fci = SimpleNamespace(name=activist)
        self.filers = [SimpleNamespace(company_information=fci)]


def _filing(form, fdate, acc, cik=886158, subject="Target Corp", activist="Fund LP",
            boom=False):
    f = SimpleNamespace(form=form, filing_date=fdate, accession_no=acc)
    if boom:
        def _raise():
            raise RuntimeError("header 500")
        # property-like access via attribute lookup; use a class to make .header raise
        class _F:
            form_ = form
        f = _F()
        f.form = form
        f.filing_date = fdate
        f.accession_no = acc
        f.__class__.header = property(lambda self: (_ for _ in ()).throw(RuntimeError("header 500")))
        return f
    f.header = _Hdr(cik, subject, activist)
    return f


def _fake_get_filings(rows):
    calls = []

    def fake(form, filing_date):
        calls.append((form, filing_date))
        return [r for r in rows if r.form == form]
    fake.calls = calls
    return fake


def test_walker_dedups_filters_amendments_and_threads_dates():
    rows = [
        _filing("SCHEDULE 13D", date(2023, 10, 10), "a-1"),
        _filing("SCHEDULE 13D", date(2023, 10, 10), "a-1"),          # edgartools double row
        _filing("SCHEDULE 13D/A", date(2023, 10, 10), "a-2"),        # amendment -> excluded
        _filing("SC 13D", date(2023, 10, 11), "a-3", cik=777, subject="Other Inc",
                activist="Icahn"),
    ]
    fake = _fake_get_filings(rows)
    recs = fetch_activist_window(date(2023, 10, 10), date(2023, 10, 12),
                                 "t@example.com", throttle_s=0.0, _get_filings=fake)
    assert [r["accession"] for r in recs] == ["a-1", "a-3"]
    assert recs[0]["filing_date"] == date(2023, 10, 10)
    assert recs[0]["cik"] == "0000886158"                            # 10-digit zero-padded
    assert recs[1]["activist"] == "Icahn"
    # both form strings queried with the ranged filing_date
    assert ("SCHEDULE 13D", "2023-10-10:2023-10-12") in fake.calls
    assert ("SC 13D", "2023-10-10:2023-10-12") in fake.calls


def test_walker_header_failure_keeps_record_with_none_cik():
    rows = [_filing("SCHEDULE 13D", date(2023, 10, 10), "a-9", boom=True)]
    recs = fetch_activist_window(date(2023, 10, 10), date(2023, 10, 10),
                                 "t@example.com", throttle_s=0.0,
                                 _get_filings=_fake_get_filings(rows))
    assert len(recs) == 1 and recs[0]["cik"] is None and recs[0]["accession"] == "a-9"


def test_walker_index_failure_returns_none_and_cap_warns():
    import pytest

    def boom(form, filing_date):
        raise RuntimeError("EDGAR down")
    with pytest.warns(UserWarning, match="edgar_history"):
        assert fetch_activist_window(date(2023, 1, 1), date(2023, 1, 2),
                                     "t@example.com", throttle_s=0.0, _get_filings=boom) is None
    rows = [_filing("SCHEDULE 13D", date(2023, 1, 1), f"a-{i}") for i in range(5)]
    with pytest.warns(UserWarning, match="max_records"):
        recs = fetch_activist_window(date(2023, 1, 1), date(2023, 1, 2), "t@example.com",
                                     throttle_s=0.0, max_records=2,
                                     _get_filings=_fake_get_filings(rows))
    assert len(recs) == 2


def test_group_by_day():
    recs = [{"filing_date": date(2023, 1, 2), "accession": "a"},
            {"filing_date": date(2023, 1, 3), "accession": "b"},
            {"filing_date": date(2023, 1, 2), "accession": "c"}]
    g = group_by_day(recs)
    assert [r["accession"] for r in g[date(2023, 1, 2)]] == ["a", "c"]
    assert list(g) == [date(2023, 1, 2), date(2023, 1, 3)]           # sorted keys

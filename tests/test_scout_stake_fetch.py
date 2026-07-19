from datetime import date
from pathlib import Path
from types import SimpleNamespace as NS

from shortlist.scout.edgar_index import fetch_amendment_records
from shortlist.scout.stake import fetch_prior_stake, stake_pct_from_filing

FIX = Path(__file__).parent / "fixtures" / "stake"


class _Filing:
    def __init__(self, form, acc, subj_cik="123", filer_cik="900", fdate="2026-07-10",
                 xml=None, text="", html=None):
        self.form, self.accession_no, self.filing_date = form, acc, fdate
        ci_s = NS(cik=subj_cik, name="Target Co")
        ci_f = NS(cik=filer_cik, name="Fund LP")
        self.header = NS(subject_companies=[NS(company_information=ci_s)],
                         filers=[NS(company_information=ci_f)])
        self._xml, self._text, self._html = xml, text, html

    def xml(self):
        return self._xml

    def text(self):
        return self._text

    def html(self):
        return self._html


def test_fetch_amendment_records_filters_to_amendments(monkeypatch):
    rows = [_Filing("SCHEDULE 13D/A", "a1"), _Filing("SCHEDULE 13D", "a2"),
            _Filing("SCHEDULE 13D/A", "a1")]          # doubled row -> dedup
    monkeypatch.setattr("shortlist.scout.edgar_index._get_13d_index_rows",
                        lambda session, identity: rows)
    recs = fetch_amendment_records(date(2026, 7, 10), 10, "id", lambda cik: "TGT")
    assert [r["accession"] for r in recs] == ["a1"]
    r = recs[0]
    assert r["ticker"] == "TGT" and r["filer_cik"] == "0000000900" \
        and r["cik"] == "0000000123" and r["form"] == "SCHEDULE 13D/A"


def test_fetch_amendment_records_never_raises(monkeypatch):
    def boom(session, identity):
        raise RuntimeError("sec down")
    monkeypatch.setattr("shortlist.scout.edgar_index._get_13d_index_rows", boom)
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert fetch_amendment_records(date(2026, 7, 10), 10, "id", lambda c: "T") == []


def test_stake_pct_from_filing_xml_then_text():
    f = _Filing("SCHEDULE 13D/A", "a1", xml="<percentOfClass>7.2%</percentOfClass>")
    assert stake_pct_from_filing(f) == 7.2
    f2 = _Filing("SC 13D/A", "a2", xml=None,
                 text="13 PERCENT OF CLASS REPRESENTED BY AMOUNT IN ROW (11)\n 5.5%")
    assert stake_pct_from_filing(f2) == 5.5
    f3 = _Filing("SC 13D/A", "a3", xml=None, text="no cover page here")
    assert stake_pct_from_filing(f3) is None


def test_stake_pct_from_filing_recovers_sibling_div_via_html_tier():
    # Legacy cover-page template family (live example: FLOTEK INDUSTRIES,
    # SC 13D/A, 0001013594-22-000096): the row-13 label survives .text()
    # rendering but the paired "3.1%" value -- a sibling <div> inside the same
    # <td> in the source HTML -- does not. xml() is absent (pre-2024
    # modernization), text() is a faithful stand-in for that value-stripped
    # rendering, and html() is the real (trimmed) raw document. Only the
    # ("xml", "html", "text") accessor order recovers the value.
    raw_html = (FIX / "legacy_amendment_sibling_div.txt").read_text()
    stripped_text = ("13      PERCENT OF CLASS REPRESENTED BY AMOUNT IN ROW (11)\n"
                     "14      TYPE OF REPORTING PERSON (See Instructions)\n"
                     "(1) Includes shares issuable upon conversion of the note.")
    f = _Filing("SC 13D/A", "a4", xml=None, text=stripped_text, html=raw_html)
    assert stake_pct_from_filing(f) == 3.1


def test_fetch_prior_stake_picks_latest_before(monkeypatch):
    older = _Filing("SCHEDULE 13D", "p1", fdate="2026-01-05",
                    text="PERCENT OF CLASS ...\n 4.0%")
    newer = _Filing("SCHEDULE 13D/A", "p2", fdate="2026-03-01",
                    text="PERCENT OF CLASS ...\n 6.0%")
    future = _Filing("SCHEDULE 13D/A", "p3", fdate="2026-08-01",
                     text="PERCENT OF CLASS ...\n 9.0%")

    def fake_company(cik, identity):
        return [older, newer, future]
    pct = fetch_prior_stake("123", "0000000900", date(2026, 7, 1), "id",
                            _get_company=fake_company)
    assert pct == 6.0                                 # latest strictly before `before`

from datetime import date
from shortlist.edgar.index import (
    fetch_activist_records,
    fetch_form4_submissions,
    fetch_recent_records,
    _is_real_ticker,
)


def test_fetch_recent_walks_back_to_last_published_index():
    # Wed 2026-06-03's index is unpublished (empty) at after-close run time; the
    # signal should fall back to the prior published session (Tue 2026-06-02).
    published = {date(2026, 6, 2): [{"ticker": "ABC", "insider": "Jane", "code": "P", "value": 1}]}
    seen = []

    def fake_fetch(d, cap, ident):
        seen.append(d)
        return published.get(d, [])

    recs, used = fetch_recent_records(date(2026, 6, 3), 400, "id", _fetch=fake_fetch)
    assert used == date(2026, 6, 2)
    assert recs and recs[0]["ticker"] == "ABC"
    assert seen[0] == date(2026, 6, 3)  # tried today first


def test_fetch_recent_skips_weekend_when_walking_back():
    # Mon 2026-06-08 unpublished -> must skip Sun/Sat back to Fri 2026-06-05.
    published = {date(2026, 6, 5): [{"ticker": "ABC", "insider": "Jane", "code": "P", "value": 1}]}
    seen = []

    def fake_fetch(d, cap, ident):
        seen.append(d)
        return published.get(d, [])

    recs, used = fetch_recent_records(date(2026, 6, 8), 400, "id", _fetch=fake_fetch)
    assert used == date(2026, 6, 5)
    assert date(2026, 6, 6) not in seen and date(2026, 6, 7) not in seen  # weekend skipped


def test_fetch_recent_exhaustion_returns_original_session():
    # All sessions empty -> return ([], original session) so the caller's "used != session"
    # fallback-suffix logic stays correct (no false "index empty, used ..." note).
    recs, used = fetch_recent_records(date(2026, 6, 3), 400, "id",
                                     lookback=2, _fetch=lambda d, c, i: [])
    assert recs == [] and used == date(2026, 6, 3)


def test_is_real_ticker_rejects_placeholders_and_keeps_real_symbols():
    # Placeholders edgartools can leak: None/"None", whitespace, em-dash, CIK-as-ticker.
    for junk in (None, "", "  ", "None", "NONE", "n/a", "—", "0001234567", "12345"):
        assert _is_real_ticker(junk) == ""
    # Real symbols (incl. digits and a dotted class) survive, normalized to upper.
    assert _is_real_ticker(" brk.b ") == "BRK.B"
    assert _is_real_ticker("axia3") == "AXIA3"
    assert _is_real_ticker("AAPL") == "AAPL"


def _broken_edgar_module(monkeypatch):
    """Install a fake `edgar` module whose set_identity raises — deterministic outer-except
    trigger whether or not edgartools is installed."""
    import sys
    import types

    fake = types.ModuleType("edgar")

    def _boom(*a, **k):
        raise RuntimeError("SEC outage https://sec.gov/x?apikey=SECRET")

    fake.set_identity = _boom
    fake.get_filings = _boom
    monkeypatch.setitem(sys.modules, "edgar", fake)


def test_fetch_daily_records_outage_degrades_loudly(monkeypatch):
    import pytest
    from shortlist.edgar.index import fetch_daily_records
    _broken_edgar_module(monkeypatch)
    with pytest.warns(UserWarning, match="index fetch failed") as w:
        assert fetch_daily_records(date(2026, 7, 1), 5, "x@y.z") == []   # still never-raises
    assert "SECRET" not in str(w[0].message)          # redact_secrets applied


def test_fetch_activist_records_outage_degrades_loudly(monkeypatch):
    import pytest
    from shortlist.edgar.index import fetch_activist_records
    _broken_edgar_module(monkeypatch)
    with pytest.warns(UserWarning, match="index fetch failed") as w:
        assert fetch_activist_records(date(2026, 7, 1), 5, "x@y.z",
                                      lambda cik: None) == []            # still never-raises
    assert "SECRET" not in str(w[0].message)          # redact_secrets applied


class _FakeCompanyInfo:
    def __init__(self, cik, name):
        self.cik, self.name = cik, name


class _FakeSubject:
    def __init__(self, cik, name):
        self.company_information = _FakeCompanyInfo(cik, name)


class _FakeHeader:
    def __init__(self, cik, name):
        self.subject_companies = [_FakeSubject(cik, name)]
        self.filers = [_FakeSubject(999, "Activist LP")]


class _FakeActivistFiling:
    """`.header` is a NETWORK access in edgartools — the throttle must precede it."""

    def __init__(self, accession_no, cik, form="SCHEDULE 13D"):
        self.accession_no = accession_no
        self.form = form
        self._cik = cik

    @property
    def header(self):
        return _FakeHeader(self._cik, "Target Co")


class _FakeFiling:
    def __init__(self, accession_no, submission_text):
        self.accession_no = accession_no
        self._text = submission_text

    def full_text_submission(self):
        return self._text


def _install_fake_edgar_module(monkeypatch, filings_by_day):
    """Install a fake `edgar` module whose get_filings returns each row TWICE (the
    documented edgartools quirk _dedup_by_accession guards against)."""
    import sys
    import types

    fake = types.ModuleType("edgar")
    fake.set_identity = lambda *a, **k: None
    fake.get_filings = lambda form, filing_date: (
        filings_by_day.get(filing_date, []) * 2)
    monkeypatch.setitem(sys.modules, "edgar", fake)


def test_fetch_form4_submissions_dedups_and_walks_back(monkeypatch):
    published = {"2026-06-02": [_FakeFiling("0001-26-000001", "<ownershipDocument>A</ownershipDocument>")]}
    _install_fake_edgar_module(monkeypatch, published)

    docs, used, considered = fetch_form4_submissions(date(2026, 6, 3), 400, "id@x.z")
    assert used == date(2026, 6, 2)
    assert docs == ["<ownershipDocument>A</ownershipDocument>"]   # deduped, not doubled


def test_fetch_form4_submissions_outage_degrades_loudly(monkeypatch):
    import pytest
    _broken_edgar_module(monkeypatch)
    with pytest.warns(UserWarning, match="form4 submission fetch failed") as w:
        # I-1: used=None is the hard-failure sentinel, distinct from a normal walk-back
        # exhaustion (used == session) -- the caller uses it to tell a real SEC outage
        # apart from a quiet day.
        assert fetch_form4_submissions(date(2026, 7, 1), 5, "x@y.z") == ([], None, 0)
    assert "SECRET" not in str(w[0].message)          # redact_secrets applied


def test_fetch_form4_submissions_throttles_every_filing(monkeypatch):
    """The 2500-filing sweep is the process's heaviest SEC consumer; every per-filing
    request must pass the shared throttle or it 429s the rest of the run (audit §4)."""
    published = {"2026-06-02": [
        _FakeFiling("0001-26-00000%d" % i, "<ownershipDocument>x</ownershipDocument>")
        for i in range(1, 4)
    ]}
    _install_fake_edgar_module(monkeypatch, published)
    calls = []
    docs, used, considered = fetch_form4_submissions(
        date(2026, 6, 2), 400, "id@x.z", _throttle=lambda c=None: calls.append(c))
    assert len(docs) == 3
    assert len(calls) == 3           # one throttle acquisition per filing fetched
    # labelled, so RunManifest.sec_requests can attribute the run's SEC draw
    assert set(calls) == {"edgar_form4"}


def test_fetch_activist_records_throttles_every_header_fetch(monkeypatch):
    """13D discovery fetches a header per filing to read the SUBJECT company — same budget."""
    published = {"2026-06-02": [_FakeActivistFiling("0009-26-00000%d" % i, cik=320193 + i)
                                for i in range(1, 3)]}
    _install_fake_edgar_module(monkeypatch, published)
    calls = []
    fetch_activist_records(date(2026, 6, 2), 300, "id@x.z",
                           lambda cik: "AAPL", _throttle=lambda c=None: calls.append(c))
    assert len(calls) == 2
    assert set(calls) == {"edgar_activist_13d"}


def test_fetch_form4_submissions_skips_one_bad_filing(monkeypatch):
    class _Boom(_FakeFiling):
        def full_text_submission(self):
            raise RuntimeError("bad doc")

    published = {"2026-06-02": [
        _Boom("0001-26-000001", ""),
        _FakeFiling("0001-26-000002", "<ownershipDocument>B</ownershipDocument>"),
    ]}
    _install_fake_edgar_module(monkeypatch, published)

    docs, used, considered = fetch_form4_submissions(date(2026, 6, 2), 400, "id@x.z")
    # `considered` counts filings the cap admitted, BEFORE per-filing errors -- that is what
    # lets the caller tell "the cap bound" from "some filings failed" (final review I-6).
    assert considered > len(docs)
    assert used == date(2026, 6, 2)
    assert docs == ["<ownershipDocument>B</ownershipDocument>"]

from datetime import date
from shortlist.scout.edgar_index import (
    cluster_buys_from_records,
    fetch_recent_records,
    _is_real_ticker,
)


def test_cluster_detection_groups_buys_by_issuer():
    # Two distinct insiders buying the same issuer same day = a cluster.
    records = [
        {"ticker": "ABC", "insider": "Jane", "code": "P", "value": 250_000},
        {"ticker": "ABC", "insider": "John", "code": "P", "value": 120_000},
        {"ticker": "XYZ", "insider": "Sue",  "code": "P", "value": 90_000},   # lone buy
        {"ticker": "ABC", "insider": "Jane", "code": "S", "value": 999_999},  # sale ignored
    ]
    ems = cluster_buys_from_records(records, min_buyers=2)
    syms = {e.ticker for e in ems}
    assert syms == {"ABC"}            # only ABC has >=2 distinct buyers
    e = next(iter(ems))
    assert e.is_discovery is True
    assert "2 insiders" in e.evidence and "370" in e.evidence  # $370k total


def test_placeholder_tickers_do_not_form_a_phantom_cluster():
    # Tickerless filers (unresolved issuer -> "NONE") must not bucket into a fake cluster,
    # even with >=2 distinct buyers. Regression for the live scout's phantom "NONE" candidate.
    records = [
        {"ticker": "NONE", "insider": "Ellen Harvey", "code": "P", "value": 20_000},
        {"ticker": "NONE", "insider": "Robert Harper", "code": "P", "value": 15_000},
        {"ticker": "",     "insider": "Anon",          "code": "P", "value": 5_000},
    ]
    assert cluster_buys_from_records(records, min_buyers=2) == []


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
    from shortlist.scout.edgar_index import fetch_daily_records
    _broken_edgar_module(monkeypatch)
    with pytest.warns(UserWarning, match="edgar index fetch failed") as w:
        assert fetch_daily_records(date(2026, 7, 1), 5, "x@y.z") == []   # still never-raises
    assert "SECRET" not in str(w[0].message)          # redact_secrets applied


def test_fetch_activist_records_outage_degrades_loudly(monkeypatch):
    import pytest
    from shortlist.scout.edgar_index import fetch_activist_records
    _broken_edgar_module(monkeypatch)
    with pytest.warns(UserWarning, match="edgar index fetch failed") as w:
        assert fetch_activist_records(date(2026, 7, 1), 5, "x@y.z",
                                      lambda cik: None) == []            # still never-raises
    assert "SECRET" not in str(w[0].message)          # redact_secrets applied

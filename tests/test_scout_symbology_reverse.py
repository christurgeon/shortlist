import warnings
from datetime import date

from shortlist.scout.symbology import Symbology


def _sym(snaps, revmaps):
    s = Symbology.__new__(Symbology)
    s._live = {"BBBY": 1130713}          # live reverse would be WRONG (Overstock) — must be unused
    s._snapshots = snaps
    s._snap_cache = {}
    s._rev_cache = revmaps                # {ts: {ticker: cik}}
    s._cache_dir = "/unused"; s._client = None; s._overrides = {}
    s.disagreements = []
    return s


def test_reverse_uses_archive_not_live():
    s = _sym([("20221003133031", date(2022, 10, 3))],
             {"20221003133031": {"BBBY": 886158, "AAPL": 320193}})
    # as-of snapshot maps BBBY -> the REAL delisted CIK 886158, NOT live's Overstock 1130713
    assert s.resolve_cik("BBBY", date(2022, 11, 15)) == 886158


def test_reverse_unresolvable_returns_none_and_counts_abstention():
    s = _sym([("20221003133031", date(2022, 10, 3))],
             {"20221003133031": {"AAPL": 320193}})
    assert s.resolve_cik("OTCJUNKF", date(2022, 11, 15)) is None
    resolved, rate = s.resolve_ciks(["AAPL", "OTCJUNKF", "FOREIGNY"], date(2022, 11, 15))
    assert resolved == {"AAPL": 320193}
    assert abs(rate - (2 / 3)) < 1e-9    # 2 of 3 unresolvable (the FINRA OTC gap)


def test_reverse_non_str_ticker_returns_none_never_raises():
    # M-1: a truthy non-str ticker (an int) must not AttributeError — honors the never-raises
    # contract, symmetric with the forward path. Not in the map -> None (no crash).
    s = _sym([("20221003133031", date(2022, 10, 3))],
             {"20221003133031": {"AAPL": 320193}})
    assert s.resolve_cik(12345, date(2022, 11, 15)) is None
    resolved, rate = s.resolve_ciks([12345, "AAPL"], date(2022, 11, 15))
    assert resolved == {"AAPL": 320193}


def test_reverse_no_abstention_does_not_warn():
    # M-2: a fully-resolved batch (0% abstention) must NOT emit the abstention warning.
    s = _sym([("20221003133031", date(2022, 10, 3))],
             {"20221003133031": {"AAPL": 320193, "MSFT": 789019}})
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        resolved, rate = s.resolve_ciks(["AAPL", "MSFT"], date(2022, 11, 15))
    assert resolved == {"AAPL": 320193, "MSFT": 789019}
    assert rate == 0.0
    assert not any("abstention" in str(w.message) for w in rec)

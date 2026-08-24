"""Realized post-announcement moves from 8-K Item 2.02 (the anchor for the implied
earnings move — docs/audits/2026-08-24-options-surface-design.md §6.2).

Pure logic only; the EDGAR and price fetches are exercised by the live test.
"""
import datetime

from shortlist.research import earnings_moves

D = datetime.date


def _series(pairs):
    return [(D(*d), px) for d, px in pairs]


# --- realized_moves ---------------------------------------------------------------

def test_move_spans_the_announcement():
    """Close before the announcement -> first close after it."""
    closes = _series([((2026, 7, 29), 100.0), ((2026, 7, 30), 101.0),
                      ((2026, 7, 31), 93.6)])
    out = earnings_moves.realized_moves(closes, [D(2026, 7, 30)])
    assert out == [("2026-07-30", -7.3)]


def test_multiple_announcements_are_newest_first():
    closes = _series([((2026, 1, 28), 100.0), ((2026, 1, 29), 100.0),
                      ((2026, 1, 30), 105.0),
                      ((2026, 4, 29), 200.0), ((2026, 4, 30), 200.0),
                      ((2026, 5, 1), 190.0)])
    out = earnings_moves.realized_moves(
        closes, [D(2026, 1, 29), D(2026, 4, 30)])
    assert [d for d, _ in out] == ["2026-04-30", "2026-01-29"]
    assert out[0][1] == -5.0
    assert out[1][1] == 5.0


def test_announcement_with_no_following_session_is_dropped():
    """The most recent print can post-date the last close in the series."""
    closes = _series([((2026, 7, 29), 100.0), ((2026, 7, 30), 101.0)])
    assert earnings_moves.realized_moves(closes, [D(2026, 7, 30)]) == []


def test_announcement_before_the_series_is_dropped():
    closes = _series([((2026, 7, 29), 100.0), ((2026, 7, 30), 101.0)])
    assert earnings_moves.realized_moves(closes, [D(2020, 1, 1)]) == []


def test_quarters_cap_is_respected():
    closes = _series([((2026, 1, d), 100.0 + d) for d in range(1, 20)])
    anns = [D(2026, 1, d) for d in range(2, 18)]
    assert len(earnings_moves.realized_moves(closes, anns, quarters=4)) == 4


def test_zero_or_missing_prior_close_is_dropped_not_divided_by():
    closes = _series([((2026, 7, 29), 0.0), ((2026, 7, 30), 101.0)])
    assert earnings_moves.realized_moves(closes, [D(2026, 7, 29)]) == []


def test_empty_inputs_abstain():
    assert earnings_moves.realized_moves([], [D(2026, 7, 30)]) == []
    assert earnings_moves.realized_moves(_series([((2026, 7, 29), 1.0)]), []) == []


# --- item-code matching -----------------------------------------------------------

def test_item_202_is_matched_and_others_are_not():
    """2.02 is Results of Operations. 2.06 and 12.02 must not match it."""
    assert earnings_moves.is_results_8k("2.02,9.01")
    assert earnings_moves.is_results_8k("2.02")
    assert earnings_moves.is_results_8k("2.02, 7.01, 9.01")
    assert not earnings_moves.is_results_8k("2.06,9.01")
    assert not earnings_moves.is_results_8k("5.02")
    assert not earnings_moves.is_results_8k("")
    assert not earnings_moves.is_results_8k(None)


def test_item_code_matching_is_not_a_substring_search():
    """A naive `"2.02" in items` matches 12.02 and 2.021. Neither is Results of
    Operations, and a wrong announcement date silently moves every realized move."""
    assert not earnings_moves.is_results_8k("12.02")
    assert not earnings_moves.is_results_8k("2.021")


# --- the shared Yahoo day-cache ---------------------------------------------------

def _chart_payload(rows):
    stamps, closes = [], []
    for d, px in rows:
        stamps.append(int(datetime.datetime(d.year, d.month, d.day,
                                            tzinfo=datetime.timezone.utc).timestamp()))
        closes.append(px)
    return {"chart": {"result": [{"timestamp": stamps,
                                  "indicators": {"quote": [{"close": closes}]}}]}}


def test_daily_closes_reads_the_price_sources_cache_and_makes_no_request(tmp_path, monkeypatch):
    """The regression this guards: an earlier version issued its own uncached Yahoo
    call and added 40-60 SECONDS to every /deep. data/sources/yahoo.py has already
    fetched and day-cached this exact payload earlier in the same run."""
    import json as _json

    from shortlist.research import earnings_moves as em

    day = datetime.date.today().isoformat()
    cache = tmp_path / f"AAPL-{day}.json"
    cache.write_text(_json.dumps(_chart_payload([(D(2026, 7, 29), 100.0),
                                                 (D(2026, 7, 30), 110.0)])))

    def _explode(*a, **k):                      # any HTTP call is a failure here
        raise AssertionError("daily_closes must not fetch when the day-cache is warm")

    monkeypatch.setattr("httpx.get", _explode)
    out = em.daily_closes("AAPL", cache_dir=str(tmp_path))
    assert out == [(D(2026, 7, 29), 100.0), (D(2026, 7, 30), 110.0)]


def test_cache_key_matches_the_price_source_exactly():
    """Same directory and same filename format, or the two write different entries and
    each pays for its own fetch."""
    from datetime import date

    from shortlist.data.sources.yahoo import YahooSource
    from shortlist.research import earnings_moves as em

    expected = YahooSource()._cache_path("AAPL")
    assert expected.name == f"AAPL-{date.today().isoformat()}.json"
    assert str(expected.parent) == em._YAHOO_CACHE_DIR
    # ...and the request params must match, or the shared entry holds the wrong range.
    assert em._YAHOO_PARAMS == {"range": "5y", "interval": "1d"}


def test_the_dead_ticker_envelope_yields_no_closes():
    """The price source day-caches {"result": None} for a 404 (delisted/unknown), so
    this must read as 'no closes', not raise."""
    from shortlist.research import earnings_moves as em

    dead = {"chart": {"result": None, "error": {"code": "Not Found"}}}
    assert em._closes_from_payload(dead) == []
    assert em._closes_from_payload({}) == []
    assert em._closes_from_payload(None) == []

"""The listed-universe leaf: pure parsing, and failure that degrades to inert."""
import json

from shortlist.data.nasdaq_universe import (adv_shares_from_finra, fetch_universe,
                                            parse_universe)


def _payload(*rows):
    return {"data": {"table": {"rows": list(rows)}}}


def _row(symbol, cap="1,000,000,000", price="$12.34"):
    return {"symbol": symbol, "name": f"{symbol} Inc", "marketCap": cap, "lastsale": price}


# --- parsing -------------------------------------------------------------------------

def test_parses_currency_and_thousands_separators():
    u = parse_universe(_payload(_row("AAPL", "4,556,011,112,400", "$312.18")))
    assert u["AAPL"] == (4_556_011_112_400.0, 312.18)


def test_symbols_are_uppercased_and_stripped():
    assert "BRKB" in parse_universe(_payload(_row(" brkb ")))


def test_one_unparseable_row_does_not_lose_the_payload():
    """Skip the bad row individually. A single malformed entry in a 4,000-row response
    must not blank the whole universe and silently turn the floor off."""
    u = parse_universe(_payload(_row("GOOD"), "not-a-dict", {"no_symbol": 1},
                                _row("ALSOGOOD")))
    assert set(u) == {"GOOD", "ALSOGOOD"}


def test_missing_or_sentinel_values_become_none_not_zero():
    """0.0 would read as a real measurement and drop the name; None abstains."""
    u = parse_universe(_payload(_row("AAA", cap="N/A", price="--"),
                                _row("BBB", cap="", price="$0.00")))
    assert u["AAA"] == (None, None)
    assert u["BBB"] == (None, None)


def test_a_malformed_payload_yields_an_empty_map_rather_than_raising():
    for bad in (None, {}, {"data": None}, {"data": {"table": {}}}, "nonsense", []):
        assert parse_universe(bad) == {}


# --- fetching: every failure path degrades to inert ------------------------------------

def test_fetch_degrades_to_empty_when_every_request_fails(tmp_path):
    def boom(url, timeout):
        raise RuntimeError("WAF")
    assert fetch_universe(cache_dir=str(tmp_path), _http_json=boom) == {}


def test_a_partial_failure_keeps_what_succeeded(tmp_path):
    """Two exchanges are better than none — the floor abstains on whatever is missing."""
    calls = []

    def flaky(url, timeout):
        calls.append(url)
        if "NYSE" in url:
            raise RuntimeError("500")
        return _payload(_row(f"X{len(calls)}"))

    u = fetch_universe(cache_dir=str(tmp_path), _http_json=flaky)
    assert len(calls) == 3 and len(u) == 2      # NASDAQ + AMEX survived


def test_second_call_is_served_from_the_day_cache(tmp_path):
    calls = []

    def once(url, timeout):
        calls.append(url)
        return _payload(_row("AAA"))

    a = fetch_universe(cache_dir=str(tmp_path), _http_json=once)
    b = fetch_universe(cache_dir=str(tmp_path), _http_json=once)
    assert a == b == {"AAA": (1_000_000_000.0, 12.34)}
    assert len(calls) == 3, "cached run must issue no further requests"


def test_an_empty_result_is_not_cached(tmp_path):
    """Caching a WAF block as 'the universe is empty' would disable the floor for a whole
    day — the same class of bug as caching a soft failure in the HTTP cache."""
    def boom(url, timeout):
        raise RuntimeError("WAF")
    fetch_universe(cache_dir=str(tmp_path), _http_json=boom)
    assert not list(tmp_path.glob("*.json"))


def test_cache_roundtrips_tuples_not_lists(tmp_path):
    def ok(url, timeout):
        return _payload(_row("AAA"))
    fetch_universe(cache_dir=str(tmp_path), _http_json=ok)
    raw = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert isinstance(raw["AAA"], list)                       # JSON has no tuples
    again = fetch_universe(cache_dir=str(tmp_path), _http_json=ok)
    assert isinstance(again["AAA"], tuple)                    # callers unpack a pair


# --- the FINRA volume join --------------------------------------------------------------

def test_adv_from_finra_rows():
    rows = [{"symbolCode": "aaa", "averageDailyVolumeQuantity": "347222"},
            {"symbolCode": "BBB", "averageDailyVolumeQuantity": 1_709_546}]
    assert adv_shares_from_finra(rows) == {"AAA": 347222.0, "BBB": 1709546.0}


def test_zero_and_unparseable_volumes_are_omitted_so_the_floor_abstains():
    """FINRA legitimately carries 0-volume rows for non-trading issues. Recording them as
    0.0 would make every one of them fail the dollar-volume floor."""
    rows = [{"symbolCode": "ZERO", "averageDailyVolumeQuantity": 0},
            {"symbolCode": "JUNK", "averageDailyVolumeQuantity": "n/a"},
            {"symbolCode": "NONE"}, {"no": "symbol"}, "not-a-dict"]
    assert adv_shares_from_finra(rows) == {}


def test_adv_handles_empty_input():
    assert adv_shares_from_finra(None) == {} and adv_shares_from_finra([]) == {}

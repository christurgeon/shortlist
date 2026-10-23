import asyncio
import tempfile
from datetime import date, timedelta

import httpx
import pytest
from shortlist.data.sources import LobbyingSource


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _no_429_floor(monkeypatch):
    from shortlist.data.sources import lobbying
    monkeypatch.setattr(lobbying, "_MIN_429_WAIT_S", 0.0)


def _source_with(handler, name_map=None, **cfg):
    cache_dir = tempfile.mkdtemp(prefix="lobbytest-")
    src = LobbyingSource(config={"lobbying": {
        "match_min_confidence": 0.85, "trailing_months": 24, "max_pages_per_year": 2,
        "max_retries": 0,  # keep mock tests fast; retry/backoff isn't under test here
        "cache_dir": cache_dir, "base_url": "https://lda.gov/api/v1", **cfg}})
    src._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    src._name_index = name_map if name_map is not None else {"LMT": "Lockheed Martin Corporation"}
    return src


# Dates relative to "today" so window bucketing is deterministic across run dates.
_TTM = (date.today() - timedelta(days=100)).isoformat()
_PRIOR = (date.today() - timedelta(days=500)).isoformat()
_TTM_YEAR = int(_TTM[:4])
_PRIOR_YEAR = int(_PRIOR[:4])


def test_unknown_ticker_abstains():
    src = _source_with(lambda req: httpx.Response(200, json={}), name_map={})
    res = _run(src.fetch("LMT"))
    assert res.partial.lobbying is None
    _run(src.aclose())


def _year_handler(req):
    yr = int(req.url.params.get("filing_year"))
    if yr == _TTM_YEAR:
        return httpx.Response(200, json={"count": 2, "next": None, "results": [
            {"client": {"name": "LOCKHEED MARTIN CORPORATION"},
             "registrant": {"name": "ETHERTON AND ASSOCIATES, INC."},
             "income": "30000.00", "expenses": None, "dt_posted": _TTM + "T00:00:00-04:00"},
            {"client": {"name": "ACME UNRELATED LLC"},   # below match -> dropped
             "registrant": {"name": "SOME FIRM"},
             "income": "99.00", "expenses": None, "dt_posted": _TTM + "T00:00:00-04:00"},
        ]})
    if yr == _PRIOR_YEAR:
        return httpx.Response(200, json={"count": 1, "next": None, "results": [
            {"client": {"name": "Lockheed Martin Corp"},
             "registrant": {"name": "IN HOUSE"},
             "income": None, "expenses": "20000.00", "dt_posted": _PRIOR + "T00:00:00-04:00"},
        ]})
    return httpx.Response(200, json={"count": 0, "next": None, "results": []})


def test_aggregates_and_buckets_by_window():
    src = _source_with(_year_handler)
    lb = _run(src.fetch("LMT")).partial.lobbying
    assert lb is not None
    assert lb.ttm_spend == 30000.0          # matched income, in TTM window
    assert lb.prior_ttm_spend == 20000.0    # matched expenses (in-house), prior window
    assert lb.filing_count_ttm == 1
    assert lb.match_confidence >= 0.9
    assert lb.registrant_count == 1         # TTM-scoped: only Etherton (the TTM filing)
    assert lb.latest_filing == _TTM
    _run(src.aclose())


def test_filing_before_window_is_excluded():
    # A filing posted >24m ago is returned by its edge-year query but must land in
    # NEITHER spend bucket (falls through both the if/elif window guards).
    old = (date.today() - timedelta(days=800)).isoformat()
    old_year = int(old[:4])

    def handler(req):
        if int(req.url.params.get("filing_year")) == old_year:
            return httpx.Response(200, json={"count": 1, "next": None, "results": [
                {"client": {"name": "LOCKHEED MARTIN CORPORATION"},
                 "registrant": {"name": "OLD FIRM"}, "income": "777.00",
                 "expenses": None, "dt_posted": old + "T00:00:00-04:00"}]})
        return httpx.Response(200, json={"count": 0, "next": None, "results": []})
    src = _source_with(handler)
    lb = _run(src.fetch("LMT")).partial.lobbying
    assert lb.ttm_spend == 0.0          # 800d-old filing excluded from TTM
    assert lb.prior_ttm_spend == 0.0    # ...and from prior-TTM (before window_start)
    _run(src.aclose())


def test_retries_429_then_succeeds():
    state = {"hits": 0}

    def handler(req):
        state["hits"] += 1
        if state["hits"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})
        return _year_handler(req)
    src = _source_with(handler, max_retries=1)
    lb = _run(src.fetch("LMT")).partial.lobbying
    assert lb is not None and lb.ttm_spend == 30000.0   # recovered after the retry
    assert state["hits"] >= 2
    _run(src.aclose())


def test_income_else_expenses_spend():
    from shortlist.data.sources import LobbyingSource as L
    assert L._spend({"income": "5", "expenses": None}) == 5.0
    assert L._spend({"income": None, "expenses": "7"}) == 7.0
    assert L._spend({"income": None, "expenses": None}) is None
    assert L._spend({"income": "", "expenses": "9"}) == 9.0


def test_never_raises_on_http_error():
    src = _source_with(lambda req: httpx.Response(500, text="boom"))
    res = _run(src.fetch("LMT"))
    assert res.partial.lobbying is None
    assert any("lobbying" in e for e in res.errors)
    _run(src.aclose())


def test_truncation_flag_when_capped():
    def handler(req):
        # always has a next page -> paging stops at max_pages_per_year=2
        return httpx.Response(200, json={"count": 500, "next": "http://x?page=99",
            "results": [{"client": {"name": "LOCKHEED MARTIN CORPORATION"},
                         "registrant": {"name": "F"}, "income": "1000.00",
                         "expenses": None, "dt_posted": _TTM + "T00:00:00-04:00"}]})
    src = _source_with(handler)
    lb = _run(src.fetch("LMT")).partial.lobbying
    assert lb.truncated is True
    assert lb.total_filings >= 500
    _run(src.aclose())


def test_same_day_rerun_uses_cache():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return _year_handler(req)
    src = _source_with(handler)
    first = _run(src.fetch("LMT")).partial.lobbying
    after_first = calls["n"]
    second = _run(src.fetch("LMT")).partial.lobbying
    assert calls["n"] == after_first       # warm re-run: zero upstream calls
    assert second.ttm_spend == first.ttm_spend == 30000.0
    _run(src.aclose())


@pytest.mark.parametrize("bad", [{"matched": True, "lb": {"no_such_field": 1}},
                                 {"matched": True}])
def test_corrupt_versioned_cache_never_raises_and_refetches(bad):
    from shortlist.data.diskcache import write_json_cache
    end = date.today().isoformat()
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return _year_handler(req)
    src = _source_with(handler)
    write_json_cache(src._cache_path("LMT", end), {"v": src._CACHE_V, **bad})
    res = _run(src.fetch("LMT"))          # must not raise
    assert not res.errors
    assert res.partial.lobbying.ttm_spend == 30000.0
    assert calls["n"] > 0                 # live path ran: corrupt cache was a miss
    assert src._read_cache("LMT", end)["lb"]["ttm_spend"] == 30000.0
    _run(src.aclose())


def test_queries_and_matches_on_the_title_without_sec_qualifiers():
    seen = []

    def handler(req):
        seen.append(req.url.params.get("client_name"))
        return httpx.Response(200, json={"count": 1, "next": None, "results": [
            {"client": {"name": "NORTHROP GRUMMAN CORP"},
             "registrant": {"name": "IN HOUSE"},
             "income": None, "expenses": "1000.00", "dt_posted": _TTM + "T00:00:00-04:00"},
        ]})
    src = _source_with(handler, name_map={"NOC": "NORTHROP GRUMMAN CORP /DE/"})
    res = _run(src.fetch("NOC"))
    assert seen and set(seen) == {"NORTHROP GRUMMAN CORP"}
    assert res.partial.lobbying.match_confidence == 1.0
    assert res.raw["resolved_name"] == "NORTHROP GRUMMAN CORP /DE/"
    _run(src.aclose())


def test_deadline_exceeded_is_an_error_not_a_raise_and_writes_no_cache():
    async def slow(req):
        await asyncio.sleep(5)
        return _year_handler(req)
    src = _source_with(slow, deadline_s=0.05)
    res = _run(src.fetch("LMT"))
    assert res.errors == ["lobbying: deadline exceeded"]
    assert res.partial is not None and res.partial.lobbying is None
    assert src._read_cache("LMT", date.today().isoformat()) is None
    _run(src.aclose())


def test_default_deadline_bounds_the_fetch():
    src = LobbyingSource(config={"lobbying": {"cache_dir": tempfile.mkdtemp(prefix="lobbytest-")}})
    assert src._deadline_s > 0
    _run(src.aclose())


def test_429_is_retried_past_max_retries_until_it_clears():
    state = {"hits": 0}

    def handler(req):
        state["hits"] += 1
        if state["hits"] <= 4:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})
        return _year_handler(req)
    src = _source_with(handler, max_retries=0)
    res = _run(src.fetch("LMT"))
    assert not res.errors and res.partial.lobbying.ttm_spend == 30000.0
    _run(src.aclose())


def test_a_429_that_never_clears_ends_at_the_deadline():
    src = _source_with(lambda req: httpx.Response(429, headers={"Retry-After": "0"}, json={}),
                       deadline_s=0.2)
    res = _run(src.fetch("LMT"))
    assert res.errors == ["lobbying: deadline exceeded"]
    _run(src.aclose())


def test_concurrent_tickers_never_overlap_their_lda_requests():
    state = {"live": 0, "peak": 0}

    async def handler(req):
        state["live"] += 1
        state["peak"] = max(state["peak"], state["live"])
        await asyncio.sleep(0.01)
        state["live"] -= 1
        return _year_handler(req)
    src = _source_with(handler, name_map={"LMT": "Lockheed Martin Corporation",
                                          "LMTB": "Lockheed Martin Corporation"})

    async def both():
        return await asyncio.gather(src.fetch("LMT"), src.fetch("LMTB"))
    a, b = _run(both())
    assert a.partial.lobbying and b.partial.lobbying
    assert state["peak"] == 1
    _run(src.aclose())


def test_an_ambiguous_name_abstains_without_a_query():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return _year_handler(req)
    src = _source_with(handler, name_map={"IBCP": "INDEPENDENT BANK CORP /MI/"})
    src._ambiguous = frozenset({"IBCP"})
    res = _run(src.fetch("IBCP"))
    assert calls["n"] == 0 and res.partial.lobbying is None and not res.errors
    assert res.raw == {"resolved_name": "INDEPENDENT BANK CORP /MI/", "ambiguous_name": True}
    _run(src.aclose())


_TICKERS_RAW = {
    "0": {"cik_str": 1, "ticker": "INDB", "title": "INDEPENDENT BANK CORP"},
    "1": {"cik_str": 2, "ticker": "IBCP", "title": "INDEPENDENT BANK CORP /MI/"},
    "2": {"cik_str": 3, "ticker": "BRK-A", "title": "BERKSHIRE HATHAWAY INC"},
    "3": {"cik_str": 3, "ticker": "BRK-B", "title": "BERKSHIRE HATHAWAY INC"},
    "4": {"cik_str": 4, "ticker": "NOC", "title": "NORTHROP GRUMMAN CORP /DE/"},
    "5": {"cik_str": 5, "ticker": "TGT", "title": "TARGET CORP"},
    "6": {"cik_str": 6, "ticker": "CBDY", "title": "Target Group Inc."},
}


def test_only_a_collision_the_strip_created_is_ambiguous():
    from shortlist.data.sources._common import _ambiguous_tickers
    assert _ambiguous_tickers(_TICKERS_RAW) == frozenset({"INDB", "IBCP"})


def test_the_loaded_index_marks_ambiguous_names(monkeypatch):
    async def fake_raw(client, cache_dir, month):
        return _TICKERS_RAW
    monkeypatch.setattr("shortlist.backtest.xbrl.fetch_company_tickers_raw", fake_raw)
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return _year_handler(req)
    src = _source_with(handler)
    src._name_index = None
    res = _run(src.fetch("IBCP"))
    assert calls["n"] == 0 and res.raw.get("ambiguous_name") is True
    _run(src.aclose())


def test_a_long_429_run_never_overflows_the_backoff(monkeypatch):
    from shortlist.data.sources import lobbying

    async def no_sleep(s):
        return None
    monkeypatch.setattr(lobbying.asyncio, "sleep", no_sleep)
    state = {"hits": 0}

    def handler(req):
        state["hits"] += 1
        if state["hits"] <= 1100:
            return httpx.Response(429, json={})
        return _year_handler(req)
    src = _source_with(handler, deadline_s=60)
    res = _run(src.fetch("LMT"))
    assert not res.errors and res.partial.lobbying is not None
    _run(src.aclose())


def _client_row(client, spend="1000.00", uid=None):
    row = {"client": {"name": client}, "registrant": {"name": "IN HOUSE"},
           "income": None, "expenses": spend, "dt_posted": _TTM + "T00:00:00-04:00"}
    if uid:
        row["filing_uuid"] = uid
    return row


def _by_client(rows_for):
    """A handler serving ``rows_for[client_name]`` in the TTM year, and logging queries."""
    seen = []

    def handler(req):
        q = req.url.params.get("client_name")
        seen.append(q)
        rows = rows_for.get(q, []) if int(req.url.params.get("filing_year")) == _TTM_YEAR else []
        return httpx.Response(200, json={"count": len(rows), "next": None, "results": rows})
    return handler, seen


def test_an_alias_is_searched_and_matches_its_own_filings():
    handler, seen = _by_client({"Altria Client Services": [
        _client_row("ALTRIA CLIENT SERVICES LLC", "5000.00"),
        _client_row("ALTRIA CLIENT SERVICES FOUNDATION", "99.00")]})   # not the alias
    src = _source_with(handler, name_map={"MO": "ALTRIA GROUP, INC."},
                       aliases={"MO": ["Altria Client Services"]})
    res = _run(src.fetch("MO"))
    lb = res.partial.lobbying
    assert set(seen) == {"ALTRIA GROUP, INC.", "Altria Client Services"}
    assert lb.ttm_spend == 5000.0 and lb.match_confidence == 1.0
    assert lb.matched_client == "ALTRIA CLIENT SERVICES LLC"
    _run(src.aclose())


def test_an_alias_matches_exactly_not_within_the_fuzzy_band():
    # 0.86 against "Amazon.com Services" by ratio: inside a 0.85 floor, but another entity.
    handler, _ = _by_client({"Amazon.com Services": [_client_row("AMAZON COMMUNITY SERVICES")]})
    src = _source_with(handler, name_map={"AMZN": "AMAZON COM INC"},
                       aliases={"AMZN": ["Amazon.com Services"]})
    res = _run(src.fetch("AMZN"))
    assert res.partial.lobbying is None and res.raw["matched"] is False
    _run(src.aclose())


def test_an_alias_never_admits_a_joint_venture():
    handler, _ = _by_client({"Chevron U.S.A.": [
        _client_row("CHEVRON U.S.A. INC.", "700.00"),
        _client_row("CHEVRON PHILLIPS CHEMICAL COMPANY LP", "9999.00")]})
    src = _source_with(handler, name_map={"CVX": "CHEVRON CORP"},
                       aliases={"CVX": ["Chevron U.S.A."]})
    assert _run(src.fetch("CVX")).partial.lobbying.ttm_spend == 700.0
    _run(src.aclose())


def test_a_filing_found_by_two_names_counts_once():
    dup = _client_row("EXXONMOBIL CORPORATION", "300.00", uid="f-1")
    handler, seen = _by_client({"Exxon Mobil Corporation": [dup], "ExxonMobil": [dup],
                                "ExxonMobil Holdings Corp": [_client_row("EXXONMOBIL", "1.00")]})
    src = _source_with(handler, name_map={"XOM": "ExxonMobil Holdings Corp"},
                       aliases={"XOM": ["Exxon Mobil Corporation", "ExxonMobil"]})
    lb = _run(src.fetch("XOM")).partial.lobbying
    # "ExxonMobil" normalizes like the title but is spelled differently, so it is still searched.
    assert set(seen) == {"ExxonMobil Holdings Corp", "Exxon Mobil Corporation", "ExxonMobil"}
    assert lb.ttm_spend == 301.0 and lb.filing_count_ttm == 2
    _run(src.aclose())


def test_share_classes_share_aliases_through_the_sec_title():
    handler, seen = _by_client({"Google Client Services": [_client_row("GOOGLE CLIENT SERVICES LLC")]})
    src = _source_with(handler, name_map={"GOOGL": "Alphabet Inc.", "GOOG": "Alphabet Inc."},
                       aliases={"googl": "Google Client Services"})
    assert _run(src.fetch("GOOG")).partial.lobbying is not None
    assert "Google Client Services" in seen
    _run(src.aclose())


def test_an_ambiguous_name_abstains_even_with_aliases():
    handler, seen = _by_client({})
    src = _source_with(handler, name_map={"IBCP": "INDEPENDENT BANK CORP /MI/"},
                       aliases={"IBCP": ["Independent Bank"]})
    src._ambiguous = frozenset({"IBCP"})
    assert _run(src.fetch("IBCP")).partial.lobbying is None and seen == []
    _run(src.aclose())


def test_a_day_cache_fetched_for_other_names_is_a_miss():
    handler, seen = _by_client({"Altria Client Services": [_client_row("ALTRIA CLIENT SERVICES LLC")]})
    src = _source_with(handler, name_map={"MO": "ALTRIA GROUP, INC."})
    assert _run(src.fetch("MO")).partial.lobbying is None        # cached unmatched, title only
    src._aliases = {"MO": ("Altria Client Services",)}
    seen.clear()
    assert _run(src.fetch("MO")).partial.lobbying is not None    # alias added: refetched
    assert seen
    seen.clear()
    assert _run(src.fetch("MO")).partial.lobbying is not None and seen == []
    _run(src.aclose())


@pytest.mark.parametrize("raw, expected", [
    ({"mo": ["Altria Client Services", " ", 7]}, {"MO": ("Altria Client Services",)}),
    ({"XOM": "ExxonMobil"}, {"XOM": ("ExxonMobil",)}),
    ({True: ["On Semiconductor"], "AMZN": 5, "CVX": []}, {}),
    (None, {}),
    (["GOOGL"], {}),
])
def test_alias_config_is_parsed_defensively(raw, expected):
    from shortlist.data.sources._common import _parse_aliases
    assert _parse_aliases(raw) == expected

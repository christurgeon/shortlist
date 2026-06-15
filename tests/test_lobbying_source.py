import asyncio
import tempfile
from datetime import date, timedelta

import httpx
from shortlist.data.sources import LobbyingSource


def _run(coro):
    return asyncio.run(coro)


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
    assert lb.registrant_count == 2         # Etherton (ttm) + In House (prior)
    assert lb.latest_filing == _TTM
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

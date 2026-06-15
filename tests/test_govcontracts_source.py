import asyncio
import tempfile
import httpx
from shortlist.data.sources import GovContractsSource


def _run(coro):
    return asyncio.run(coro)


def _source_with(handler, name_map=None, **cfg):
    # Unique cache dir per source so the per-ticker disk cache can't cross-
    # contaminate other tests (same ticker + same day would otherwise collide).
    cache_dir = tempfile.mkdtemp(prefix="gctest-")
    src = GovContractsSource(config={"gov_contracts": {
        "match_min_confidence": 0.8, "trailing_months": 24, "max_pages": 2,
        "cache_dir": cache_dir, **cfg}})
    src._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    src._name_index = name_map if name_map is not None else {"LMT": "Lockheed Martin Corporation"}
    return src


def test_unknown_ticker_abstains():
    src = _source_with(lambda req: httpx.Response(200, json={}), name_map={})
    res = _run(src.fetch("LMT"))
    assert res.partial.gov_contracts is None
    _run(src.aclose())


def test_aggregates_window_scoped_transactions():
    def handler(req):
        if req.url.path.endswith("spending_by_transaction_count/"):
            return httpx.Response(200, json={"results": {"contracts": 2}})
        return httpx.Response(200, json={"results": [
            {"Recipient Name": "LOCKHEED MARTIN CORPORATION",
             "Action Date": "2025-12-01", "Transaction Amount": 9e9},
            {"Recipient Name": "ZETA ASSOCIATES INC",  # below match -> dropped
             "Action Date": "2025-12-01", "Transaction Amount": 5e8},
            {"Recipient Name": "LOCKHEED MARTIN CORP",
             "Action Date": "2024-06-01", "Transaction Amount": 8e9},  # prior window
        ], "page_metadata": {"hasNext": False}})
    src = _source_with(handler)
    res = _run(src.fetch("LMT"))
    gc = res.partial.gov_contracts
    assert gc is not None
    assert gc.ttm_obligated == 9e9        # only the matched, in-window txn
    assert gc.prior_ttm_obligated == 8e9  # matched, prior window
    assert gc.award_count_ttm == 1
    assert gc.match_confidence >= 0.9
    assert gc.recipient_count == 2        # two distinct matched LMT recipient names
    assert gc.latest_action == "2025-12-01"
    _run(src.aclose())


def test_negative_deobligation_nets_into_sum():
    def handler(req):
        if req.url.path.endswith("count/"):
            return httpx.Response(200, json={"results": {"contracts": 2}})
        return httpx.Response(200, json={"results": [
            {"Recipient Name": "LOCKHEED MARTIN CORPORATION",
             "Action Date": "2025-12-01", "Transaction Amount": 9e9},
            {"Recipient Name": "LOCKHEED MARTIN CORPORATION",
             "Action Date": "2025-12-02", "Transaction Amount": -1e9},  # de-obligation
        ], "page_metadata": {"hasNext": False}})
    src = _source_with(handler)
    gc = _run(src.fetch("LMT")).partial.gov_contracts
    assert gc.ttm_obligated == 8e9        # 9e9 + (-1e9): net of de-obligation
    assert gc.award_count_ttm == 2
    _run(src.aclose())


def test_primary_recipient_is_largest_action_by_dollars():
    # Two distinct recipients that BOTH clear the match guard; the primary audit
    # name should be the recipient of the largest-dollar action, not page order.
    def handler(req):
        if req.url.path.endswith("count/"):
            return httpx.Response(200, json={"results": {"contracts": 2}})
        return httpx.Response(200, json={"results": [
            {"Recipient Name": "LOCKHEED MARTIN CORP",            # tiny, first in page
             "Action Date": "2025-12-01", "Transaction Amount": 1e6},
            {"Recipient Name": "LOCKHEED MARTIN CORPORATION",     # huge
             "Action Date": "2025-12-01", "Transaction Amount": 9e9},
        ], "page_metadata": {"hasNext": False}})
    src = _source_with(handler)
    gc = _run(src.fetch("LMT")).partial.gov_contracts
    assert gc.matched_recipient == "LOCKHEED MARTIN CORPORATION"  # primary = the $9B action
    assert gc.recipient_count == 2
    _run(src.aclose())


def test_date_bucketing_boundary():
    # Action exactly at the 365-day cutoff goes to TTM (adate >= cutoff); one day
    # before goes to prior. Use a far-future "today" via the live cutoff: instead,
    # assert the >= boundary with dates straddling a known cutoff by mocking around
    # the source's own cutoff computation through a wide window.
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=365)).isoformat()
    before = (date.today() - timedelta(days=366)).isoformat()

    def handler(req):
        if req.url.path.endswith("count/"):
            return httpx.Response(200, json={"results": {"contracts": 2}})
        return httpx.Response(200, json={"results": [
            {"Recipient Name": "LOCKHEED MARTIN CORPORATION",
             "Action Date": cutoff, "Transaction Amount": 4e9},   # exactly cutoff -> TTM
            {"Recipient Name": "LOCKHEED MARTIN CORPORATION",
             "Action Date": before, "Transaction Amount": 3e9},   # day before -> prior
        ], "page_metadata": {"hasNext": False}})
    src = _source_with(handler)
    gc = _run(src.fetch("LMT")).partial.gov_contracts
    assert gc.ttm_obligated == 4e9
    assert gc.prior_ttm_obligated == 3e9
    _run(src.aclose())


def test_never_raises_on_http_error():
    def handler(req):
        return httpx.Response(500, text="boom")
    src = _source_with(handler)
    res = _run(src.fetch("LMT"))
    assert res.partial.gov_contracts is None
    assert any("gov_contracts" in e for e in res.errors)
    _run(src.aclose())


def test_truncation_flag_set_when_capped():
    def handler(req):
        if req.url.path.endswith("count/"):
            return httpx.Response(200, json={"results": {"contracts": 300}})
        # always hasNext -> paging stops at max_pages=2
        return httpx.Response(200, json={"results": [
            {"Recipient Name": "LOCKHEED MARTIN CORPORATION",
             "Action Date": "2025-12-01", "Transaction Amount": 1e6}],
            "page_metadata": {"hasNext": True}})
    src = _source_with(handler)
    res = _run(src.fetch("LMT"))
    assert res.partial.gov_contracts.truncated is True
    assert res.partial.gov_contracts.total_txns == 300
    _run(src.aclose())


def test_same_day_rerun_uses_cache():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if req.url.path.endswith("count/"):
            return httpx.Response(200, json={"results": {"contracts": 1}})
        return httpx.Response(200, json={"results": [
            {"Recipient Name": "LOCKHEED MARTIN CORPORATION",
             "Action Date": "2025-12-01", "Transaction Amount": 7e9}],
            "page_metadata": {"hasNext": False}})
    src = _source_with(handler)
    first = _run(src.fetch("LMT")).partial.gov_contracts
    after_first = calls["n"]
    second = _run(src.fetch("LMT")).partial.gov_contracts
    assert calls["n"] == after_first       # zero new upstream calls on warm re-run
    assert second.ttm_obligated == first.ttm_obligated == 7e9
    _run(src.aclose())

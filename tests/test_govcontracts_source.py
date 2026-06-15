import asyncio
import httpx
from shortlist.data.sources import GovContractsSource


def _run(coro):
    return asyncio.run(coro)


def _source_with(handler, name_map=None, **cfg):
    src = GovContractsSource(config={"gov_contracts": {
        "match_min_confidence": 0.8, "trailing_months": 24, "max_pages": 2, **cfg}})
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

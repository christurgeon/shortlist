"""A source marked `feeds_axes = False` must fill only auxiliary snapshot sections: the
prompt's data-gaps line never names it, and coverage may not call its no-data outcome a gap,
so a key section filled by it would hide a real gap. Each such source is driven through a
matched fetch and its partial is checked."""
import asyncio
import json
import tempfile
from datetime import date, timedelta

import httpx
import pytest

from shortlist.data import apewisdom
from shortlist.data.models import _AUX_DEFAULTS, TickerSnapshot
from shortlist.data.sources import (
    _REGISTRY,
    FinraSource,
    GovContractsSource,
    LobbyingSource,
    WsbSource,
    aux_only_sources,
    axisless_sources,
)

_SECTIONS = [f for f in TickerSnapshot.__dataclass_fields__
             if f not in ("ticker", "as_of", "raw", "provenance", "errors")]
_RECENT = (date.today() - timedelta(days=30)).isoformat()


def _wsb(monkeypatch):
    monkeypatch.setattr(apewisdom, "fetch_wsb_mentions", lambda *a, **k: ({
        apewisdom.norm_symbol("GME"): apewisdom.WsbMention(
            ticker="GME", mentions=300, mentions_24h_ago=100, upvotes=900, rank=1,
            rank_24h_ago=5, as_of=_RECENT, mention_delta_pct=2.0, rising=True)}, None))
    return WsbSource(), "GME"


def _gov_contracts(monkeypatch):
    def handler(req):
        if req.url.path.endswith("count/"):
            return httpx.Response(200, json={"results": {"contracts": 1}})
        return httpx.Response(200, json={"results": [
            {"Recipient Name": "ACME CORP", "Action Date": _RECENT,
             "Transaction Amount": 1e9}], "page_metadata": {"hasNext": False}})
    src = GovContractsSource(config={"gov_contracts": {"cache_dir": tempfile.mkdtemp()}})
    src._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    src._name_index = {"ACME": "ACME CORP"}
    return src, "ACME"


def _lobbying(monkeypatch):
    def handler(req):
        return httpx.Response(200, json={"count": 1, "next": None, "results": [
            {"client": {"name": "ACME CORP"}, "registrant": {"name": "IN HOUSE"},
             "income": None, "expenses": "1000.00", "dt_posted": _RECENT + "T00:00:00-04:00"}]})
    src = LobbyingSource(config={"lobbying": {"cache_dir": tempfile.mkdtemp()}})
    src._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    src._name_index = {"ACME": "ACME CORP"}
    return src, "ACME"


def _finra(monkeypatch):
    src = FinraSource(cache_dir=tempfile.mkdtemp())

    async def parts():
        return {"availablePartitions": [{"partitions": [_RECENT]}]}

    async def page(settlement, offset):
        return [] if offset else [{"symbolCode": "ACME", "settlementDate": _RECENT,
                                   "currentShortPositionQuantity": "1000",
                                   "daysToCoverQuantity": "2.5"}]
    monkeypatch.setattr(src, "_fetch_partitions", parts)
    monkeypatch.setattr(src, "_fetch_page", page)
    return src, "ACME"


_MATCHED = {"wsb": _wsb, "gov_contracts": _gov_contracts, "lobbying": _lobbying,
            "finra": _finra}


def test_every_axisless_source_has_a_matched_fetch_below():
    assert axisless_sources(list(_REGISTRY)) == set(_MATCHED)


def test_a_no_data_is_normal_source_feeds_no_axis():
    assert aux_only_sources(list(_REGISTRY)) <= axisless_sources(list(_REGISTRY))


@pytest.mark.parametrize("name", sorted(_MATCHED))
def test_a_matched_fetch_fills_only_auxiliary_sections(name, monkeypatch):
    src, ticker = _MATCHED[name](monkeypatch)

    async def run():
        try:
            return await src.fetch(ticker)
        finally:
            await src.aclose()
    res = asyncio.run(run())
    filled = {s for s in _SECTIONS if getattr(res.partial, s) is not None}
    assert not res.errors and filled, (res.errors, json.dumps(res.raw))
    assert filled <= set(_AUX_DEFAULTS)


def test_aux_only_sources_ignores_unknown_names():
    assert aux_only_sources(["nope", "mock", "finra"]) == frozenset()

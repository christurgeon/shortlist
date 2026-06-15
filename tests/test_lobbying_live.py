"""Live Senate LDA check — skipped offline. Mirrors test_edgar_leverage_live."""
import asyncio
import os
import pytest
from shortlist.data.sources import LobbyingSource

pytestmark = pytest.mark.skipif(
    os.environ.get("SHORTLIST_LIVE") != "1",
    reason="set SHORTLIST_LIVE=1 to run network-bound LDA tests")


def _fetch(ticker):
    async def go():
        src = LobbyingSource(config={"lobbying": {
            "match_min_confidence": 0.85, "trailing_months": 24, "max_pages_per_year": 4}})
        try:
            return await src.fetch(ticker)
        finally:
            await src.aclose()
    return asyncio.run(go())


def test_lmt_has_material_lobbying():
    lb = _fetch("LMT").partial.lobbying
    assert lb is not None
    assert lb.ttm_spend and lb.ttm_spend > 1e5   # LMT lobbies millions/yr
    assert lb.match_confidence >= 0.9
    assert "LOCKHEED" in lb.matched_client.upper()

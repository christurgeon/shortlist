"""Live USAspending check — skipped offline. Mirrors test_edgar_leverage_live."""
import asyncio
import os
import pytest
from shortlist.data.sources import GovContractsSource

pytestmark = pytest.mark.skipif(
    os.environ.get("SHORTLIST_LIVE") != "1",
    reason="set SHORTLIST_LIVE=1 to run network-bound USAspending tests")


def _fetch(ticker):
    async def go():
        src = GovContractsSource(config={"gov_contracts": {
            "match_min_confidence": 0.8, "trailing_months": 24, "max_pages": 5}})
        try:
            return await src.fetch(ticker)
        finally:
            await src.aclose()
    return asyncio.run(go())


def test_lmt_has_material_contracts():
    gc = _fetch("LMT").partial.gov_contracts
    assert gc is not None
    assert gc.ttm_obligated and gc.ttm_obligated > 1e9   # LMT books >$1B/yr
    assert gc.match_confidence >= 0.9
    assert "LOCKHEED" in gc.matched_recipient.upper()


def test_ko_not_a_contractor():
    # Coca-Cola: no material federal procurement -> abstain or ~0.
    gc = _fetch("KO").partial.gov_contracts
    assert gc is None or not gc.ttm_obligated

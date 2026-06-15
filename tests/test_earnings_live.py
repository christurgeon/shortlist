"""Live Finnhub earnings check — skipped offline / without a key."""
import asyncio
import os

import pytest

from shortlist.env import load_env

pytestmark = pytest.mark.skipif(
    os.environ.get("SHORTLIST_LIVE") != "1",
    reason="set SHORTLIST_LIVE=1 to run network-bound Finnhub earnings tests")


def test_aapl_has_earnings_history_and_next_date():
    load_env()
    if not os.environ.get("FINNHUB_API_KEY"):
        pytest.skip("FINNHUB_API_KEY not configured")
    from shortlist.data.sources import FinnhubSource

    async def go():
        src = FinnhubSource()
        try:
            return await src.fetch("AAPL")
        finally:
            await src.aclose()
    e = asyncio.run(go()).partial.earnings
    assert e is not None
    assert e.quarters and e.quarters >= 1          # AAPL has surprise history
    assert e.beats is not None
    # Units guard: surprisePercent is already percent (~single digits), not a fraction
    # x100 — catches a 100x regression that only live data can surface.
    if e.last_surprise_pct is not None:
        assert abs(e.last_surprise_pct) < 100
    # next_date may be None right after a print; when present it's today-or-future.
    if e.next_date:
        assert e.next_date >= e.as_of

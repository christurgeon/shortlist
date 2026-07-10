"""Live Finnhub company-news check — skipped offline / without a key."""
import asyncio
import os

import pytest

from shortlist.env import load_env

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
    os.environ.get("SHORTLIST_LIVE") != "1",
    reason="set SHORTLIST_LIVE=1 to run network-bound Finnhub news tests"),
]


def test_aapl_has_recent_news():
    load_env()  # pick up FINNHUB_API_KEY from .env
    if not os.environ.get("FINNHUB_API_KEY"):
        pytest.skip("FINNHUB_API_KEY not configured")
    from shortlist.data.sources import FinnhubSource

    async def go():
        src = FinnhubSource()
        try:
            return await src.fetch("AAPL")
        finally:
            await src.aclose()
    snap = asyncio.run(go()).partial
    assert snap.news is not None
    assert snap.news.count_window and snap.news.count_window > 0  # AAPL always has news

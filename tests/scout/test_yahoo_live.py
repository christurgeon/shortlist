"""Network-gated smoke test for the Yahoo predefined-screener endpoint.

Skipped unless RUN_LIVE_TESTS=1. Asserts the unofficial Yahoo Finance endpoint
still accepts a browser User-Agent and returns non-empty quotes — the behaviour
YahooScreenerSignal depends on. Run manually before upgrading httpx or when
Yahoo changes their API surface.

    RUN_LIVE_TESTS=1 uv run pytest tests/scout/test_yahoo_live.py -v
"""
import os
from datetime import date

import pytest

from shortlist.scout.signals import YahooScreenerSignal


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TESTS") != "1",
    reason="live network test; set RUN_LIVE_TESTS=1 to run",
)
def test_yahoo_screener_endpoint_still_returns_quotes():
    sig = YahooScreenerSignal(screens=["day_gainers"])
    ems = sig.scan(date.today())
    ran, detail = sig.available()
    assert ran is True, f"yahoo screener broke: {detail}"
    assert ems, "expected non-empty gainers list from Yahoo predefined screener"

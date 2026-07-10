"""Network-gated smoke test for the Yahoo predefined-screener endpoint.

Skipped unless RUN_LIVE_TESTS=1. Asserts the unofficial Yahoo Finance endpoint
still accepts the full browser header set and returns non-empty quotes — the
behaviour YahooScreenerSignal depends on. Run manually before upgrading httpx or
when Yahoo changes their API surface.

POLITENESS: this hits an unofficial endpoint we must not get banned from. Run it
ONCE, manually, targeting ONLY this file — never the whole suite, never in CI, never
in a loop. On a cold WAF 429 it SKIPS (not fails) so nobody re-runs to chase green.

    RUN_LIVE_TESTS=1 uv run pytest -m live tests/scout/test_yahoo_live.py -v
"""
import os
from datetime import date

import pytest

from shortlist.scout.signals import YahooScreenerSignal

pytestmark = pytest.mark.live


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TESTS") != "1" or os.environ.get("CI"),
    reason="live network test; set RUN_LIVE_TESTS=1 and run only locally (never in CI)",
)
def test_yahoo_screener_endpoint_still_returns_quotes():
    sig = YahooScreenerSignal(screens=["day_gainers"])
    ems = sig.scan(date.today())
    ran, detail = sig.available()
    if not ran and getattr(sig, "waf_blocked", False):
        # Cold WAF block: endpoint reachable, IP just not warm. Do NOT re-run to chase
        # green — that is the hammering we are avoiding. Skip and try again another time.
        pytest.skip(f"cold WAF block (endpoint reachable, not a regression): {detail}")
    assert ran is True, f"yahoo screener broke: {detail}"
    assert ems, "expected non-empty gainers list from Yahoo predefined screener"

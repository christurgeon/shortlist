"""Live, network-gated smoke test for the activist-13D discovery path. Skipped in CI
(deselected by the `live` marker; also skipped without SEC_IDENTITY). Early warning if SEC
/ edgartools changes the SCHEDULE 13D header shape or company_tickers.json moves."""
import os
from datetime import date, timedelta

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.getenv("SEC_IDENTITY"),
                       reason="needs SEC_IDENTITY + edgar extra"),
]


def test_schedule_13d_resolves_and_shapes_live():
    from shortlist.edgar.cik_tickers import load_cik_to_ticker, resolve_ticker
    from shortlist.edgar.index import fetch_recent_activist_records

    ident = os.environ["SEC_IDENTITY"]
    idx = load_cik_to_ticker(ident)
    assert idx, "company_tickers.json should load into a non-empty index"

    # Walk back from yesterday to the last published index (an after-close run pattern).
    recs, used = fetch_recent_activist_records(
        date.today() - timedelta(days=1), 300, ident, lambda c: resolve_ticker(c, idx))
    assert isinstance(used, date)
    # The set may be empty on a quiet day — assert SHAPE only when present.
    for r in recs:
        assert r["form"].strip().upper() in {"SCHEDULE 13D", "SC 13D"}
        assert r["ticker"] and r["cik"]
        assert "subject_name" in r and "activist" in r


def test_emissions_build_from_live_records():
    from shortlist.edgar.cik_tickers import load_cik_to_ticker, resolve_ticker
    from shortlist.edgar.index import (activist_stakes_from_records,
                                             fetch_recent_activist_records)

    ident = os.environ["SEC_IDENTITY"]
    idx = load_cik_to_ticker(ident)
    recs, _ = fetch_recent_activist_records(
        date.today() - timedelta(days=1), 300, ident, lambda c: resolve_ticker(c, idx))
    ems = activist_stakes_from_records(recs)
    for e in ems:
        assert e.signal == "edgar:activist_13d"
        assert e.is_discovery is True
        assert 0.0 <= e.strength <= 1.0
        assert e.ticker == e.ticker.upper()

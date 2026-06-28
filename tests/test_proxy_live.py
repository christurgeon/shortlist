"""Live-EDGAR contract test for the DEF 14A proxy reader. Pins the edgartools
ProxyStatement API shapes the pure tests can only fake. Skipped without
SEC_IDENTITY (the [edgar] extra)."""
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("SEC_IDENTITY"), reason="needs SEC_IDENTITY + edgar extra")


def test_fetch_proxy_aapl_populates_structured_fields():
    from shortlist.research.proxy import fetch_proxy

    facts = fetch_proxy("AAPL")
    assert facts is not None and facts.usable()
    assert facts.has_xbrl is True
    # mega-cap proxy must surface clean comp + a multi-year pay-vs-performance table
    assert facts.peo_total_comp and facts.peo_total_comp > 0
    assert len(facts.pvp) >= 2
    assert facts.pvp[0]["fy"] is not None
    # newest-first ordering (descending fiscal year)
    fys = [r["fy"] for r in facts.pvp if r["fy"] is not None]
    assert fys == sorted(fys, reverse=True)
    # 5%+ holders present, all sentinel-cleaned (no 0.5 "<1%" artifact, all plausible)
    assert facts.top_holders
    assert all(0 < h["pct"] <= 100 and h["pct"] != 0.5 for h in facts.top_holders)


def test_fetch_proxy_point_in_time_excludes_future_filings():
    """as_of must never select a filing accepted after it (look-ahead guard)."""
    from shortlist.research.proxy import fetch_proxy

    recent = fetch_proxy("AAPL")
    assert recent is not None and recent.filing_date
    # a date strictly before the latest filing must yield an older (or no) proxy
    old = fetch_proxy("AAPL", as_of="2020-01-01")
    if old is not None:
        assert old.filing_date <= "2020-01-01"
        assert old.filing_date < recent.filing_date


def test_fetch_proxy_renders_a_context_line():
    from shortlist.research.proxy import fetch_proxy, context_line

    facts = fetch_proxy("AAPL")
    line = context_line(facts, {"enabled": True, "max_holders": 3, "control_pct": 30.0})
    assert line is not None and line.startswith("Proxy (DEF 14A")
    assert "reconcile against the business" in line

"""Live contract tests for the options surface and the realized-move anchor.

Pins the two external shapes the pure tests can only fake: CBOE's delayed-quote JSON
(keyless) and edgartools' 8-K item codes. Design + measured evidence:
docs/audits/2026-08-24-options-surface-design.md.
"""
import datetime
import os

import pytest

pytestmark = [pytest.mark.live]

CFG = {"enabled": True, "delta_tolerance": 0.10, "max_atm_spread_pct": 40,
       "earnings_date_uncertainty_days": 8, "max_earnings_expiry_gap_days": 14,
       "earnings_date_firm_within_days": 7, "earnings_lookback_quarters": 6,
       "max_stale_days": 5}


def test_cboe_chain_still_has_the_fields_the_surface_reads():
    """The whole design rests on per-contract `iv` + `delta` and an underlying `iv30`.
    If CBOE drops any of them the line silently thins, so pin them here."""
    from shortlist.research.options import fetch_surface

    surface = fetch_surface("AAPL", CFG)
    assert surface is not None, "CBOE chain unreachable or rate-limited"
    assert surface.spot and surface.spot > 0
    assert surface.iv30 and surface.iv30 > 0
    assert surface.expiries, "no live expiries parsed"
    # A mega-cap carries weeklies, so the near ladder must be dense.
    assert len(surface.expiries) >= 8
    assert surface.skew_pts is not None, "25-delta selection failed on a mega-cap"


def test_quote_time_is_a_date_not_the_file_timestamp():
    """CBOE's file-level `timestamp` moves when it regenerates the JSON and is NOT a
    freshness signal; `last_trade_time` is. Pin that it stays parseable."""
    from shortlist.research.options import fetch_surface

    surface = fetch_surface("AAPL", CFG)
    assert surface is not None
    stale = surface.stale_days()
    assert stale is not None and 0 <= stale <= 10, f"implausible quote age: {stale}"


def test_a_mega_cap_renders_a_line():
    from shortlist.data.bridge import snapshot_to_metrics
    from shortlist.data.collector import collect
    from shortlist.research.options import context_line, fetch_surface

    surface = fetch_surface("AAPL", CFG)
    assert surface is not None
    metrics = snapshot_to_metrics(collect(["AAPL"], ["yahoo"], config={})[0])
    line = context_line(surface, metrics, CFG)
    assert line and "Options market (CBOE delayed quotes" in line
    assert "not filing facts" in line
    # The measured correction: never assert the variance risk premium.
    assert "risk premium" not in line.lower()


@pytest.mark.skipif(not os.getenv("SEC_IDENTITY"),
                    reason="needs SEC_IDENTITY + edgar extra")
def test_8k_item_202_dates_are_wednesdays_for_nvda():
    """NVDA reports after the close on a Wednesday, every quarter. If edgartools'
    `items` shape drifts, or the item match starts catching 12.02, this breaks — and a
    wrong announcement date silently shifts every realized move computed from it."""
    from shortlist.research.earnings_moves import announcement_dates

    dates = announcement_dates("NVDA", 6)
    assert len(dates) >= 4
    assert all(d.weekday() == 2 for d in dates), [str(d) for d in dates]
    assert dates == sorted(dates, reverse=True)


@pytest.mark.skipif(not os.getenv("SEC_IDENTITY"),
                    reason="needs SEC_IDENTITY + edgar extra")
def test_realized_moves_are_plausible_and_bracketed():
    from shortlist.research.earnings_moves import fetch_moves

    moves = fetch_moves("AAPL", CFG)
    assert 4 <= len(moves) <= 6
    for iso, pct in moves:
        datetime.date.fromisoformat(iso)          # parseable announcement date
        assert -60.0 < pct < 60.0, f"implausible earnings move {pct}% on {iso}"


def test_point_in_time_guard_refuses_a_past_as_of():
    """CBOE serves only the CURRENT surface, so a past as_of must abstain rather than
    splice today's prices into a historical snapshot."""
    from shortlist.research.earnings_moves import fetch_moves
    from shortlist.research.options import fetch_surface

    assert fetch_surface("AAPL", CFG, as_of="2026-01-02") is None
    assert fetch_moves("AAPL", CFG, as_of="2026-01-02") == []

"""The /deep options-surface context line.

Design + measured evidence: docs/audits/2026-08-24-options-surface-design.md.
Prompt-only, never scored, never flagged, never in the grounding haystack. Every
guard below exists because a measured payload broke without it, and the docstrings
name which one.
"""
import datetime

from shortlist.research import options

TODAY = datetime.date(2026, 8, 24)
CFG = {
    "enabled": True,
    "delta_tolerance": 0.10,
    "max_atm_spread_pct": 40,
    "earnings_date_uncertainty_days": 8,
    "max_earnings_expiry_gap_days": 14,
    "earnings_date_firm_within_days": 7,
    "max_stale_days": 5,
}


class _M:
    """Minimal StockMetrics stand-in — the line reads only these."""

    def __init__(self, realized_vol=None, days_to_next=None):
        self.realized_vol = realized_vol
        self.earnings_days_to_next = days_to_next


def _contract(root, expiry, right, strike, *, delta, iv, bid=1.0, ask=1.1):
    """One OSI-symbolled contract in CBOE's delayed-quote shape."""
    sym = f"{root}{expiry:%y%m%d}{right}{int(strike * 1000):08d}"
    return {"option": sym, "bid": bid, "ask": ask, "iv": iv, "delta": delta,
            "open_interest": 10, "volume": 1}


def _payload(contracts, *, spot=100.0, iv30=25.0, root="XYZ",
             quote_time="2026-08-21T15:59:59"):
    return {"timestamp": "2026-08-24 00:38:41",
            "data": {"symbol": root, "current_price": spot, "iv30": iv30,
                     "last_trade_time": quote_time, "options": contracts}}


def _full_chain(root="XYZ", spot=100.0, **kw):
    """A well-quoted chain: two expiries, each with a clean 25-delta pair and ATM."""
    out = []
    for expiry in (datetime.date(2026, 9, 25), datetime.date(2026, 11, 6)):
        out += [
            _contract(root, expiry, "P", 90, delta=-0.25, iv=0.30, bid=1.0, ask=1.1),
            _contract(root, expiry, "C", 110, delta=0.25, iv=0.28, bid=1.0, ask=1.1),
            _contract(root, expiry, "P", 100, delta=-0.50, iv=0.29, bid=3.0, ask=3.2),
            _contract(root, expiry, "C", 100, delta=0.50, iv=0.29, bid=3.0, ask=3.2),
        ]
    return _payload(out, spot=spot, root=root, **kw)


# --- build_surface: parsing and the measured traps --------------------------------

def test_expired_contracts_are_filtered():
    """CBOE retains expired contracts — 21 of 80 large-cap files carried them, up to
    260 on one name. Before the filter, HDSN's already-past 2026-08-21 expiry produced
    a nonsense 82% implied move (design §4.3)."""
    past = datetime.date(2026, 8, 21)
    live = datetime.date(2026, 9, 25)
    payload = _payload([
        _contract("XYZ", past, "C", 100, delta=0.50, iv=2.0),
        _contract("XYZ", past, "P", 100, delta=-0.50, iv=2.0),
        _contract("XYZ", live, "C", 100, delta=0.50, iv=0.29),
        _contract("XYZ", live, "P", 100, delta=-0.50, iv=0.29),
    ])
    surface = options.build_surface(payload, TODAY, CFG)
    assert surface is not None
    assert [e["expiry"] for e in surface.expiries] == ["2026-09-25"]


def test_one_sided_and_zero_iv_quotes_are_rejected():
    """AAPL carried iv 2.0684 (207%) at delta 0.9998 — a stale one-sided quote
    inverting to a meaningless implied vol (design §4.4)."""
    expiry = datetime.date(2026, 9, 25)
    payload = _payload([
        _contract("XYZ", expiry, "C", 100, delta=0.50, iv=0.29, bid=0, ask=3.2),
        _contract("XYZ", expiry, "P", 100, delta=-0.50, iv=0.29, bid=3.0, ask=3.2),
        _contract("XYZ", expiry, "C", 105, delta=0.45, iv=0, bid=1.0, ask=1.1),
    ])
    surface = options.build_surface(payload, TODAY, CFG)
    assert surface is not None
    assert surface.expiries[0]["straddle_pct"] is None   # no tradeable ATM call


def test_skew_rejects_a_failed_delta_selection_the_RES_case():
    """RES produced a 77-vol-point skew from a put at delta -0.888 against a call at
    0.869: with two or three usable contracts per side, 'nearest to 0.25 delta' lands
    wherever it can. Rejecting on the ACHIEVED delta is the guard (design §6.4)."""
    expiry = datetime.date(2026, 9, 25)
    payload = _payload([
        _contract("XYZ", expiry, "P", 60, delta=-0.888, iv=1.1384),
        _contract("XYZ", expiry, "C", 140, delta=0.869, iv=0.3682),
    ])
    surface = options.build_surface(payload, TODAY, CFG)
    assert surface is not None
    assert surface.skew_pts is None, "a 0.89-delta contract must not stand in for 25-delta"


def test_skew_computed_when_the_delta_selection_is_clean():
    surface = options.build_surface(_full_chain(), TODAY, CFG)
    assert surface is not None
    assert surface.skew_pts == 2.0        # (0.30 - 0.28) * 100
    assert surface.skew_expiry == "2026-09-25"


def test_wide_atm_spread_suppresses_the_straddle_but_not_the_iv():
    """The spread guard gates the implied move alone — the one item priced off a
    premium mid. Skew compares volatilities and survives a wide spread (design §6.4)."""
    expiry = datetime.date(2026, 9, 25)
    payload = _payload([
        _contract("XYZ", expiry, "P", 90, delta=-0.25, iv=0.30),
        _contract("XYZ", expiry, "C", 110, delta=0.25, iv=0.28),
        _contract("XYZ", expiry, "P", 100, delta=-0.50, iv=0.29, bid=1.0, ask=5.0),
        _contract("XYZ", expiry, "C", 100, delta=0.50, iv=0.29, bid=1.0, ask=5.0),
    ])
    surface = options.build_surface(payload, TODAY, CFG)
    assert surface is not None
    assert surface.skew_pts == 2.0                       # volatilities still usable
    assert surface.expiries[0]["straddle_pct"] is None   # premium mid is not


def test_build_surface_survives_malformed_payloads():
    """Never-raises: the line abstains, it does not break a brief."""
    for bad in ({}, {"data": None}, {"data": {"options": None}},
                {"data": {"current_price": None, "options": []}},
                _payload([{"option": "!!garbage!!", "bid": 1, "ask": 2, "iv": 0.3}])):
        options.build_surface(bad, TODAY, CFG)   # must not raise


# --- earnings-expiry selection: the silent-wrong failure --------------------------

def test_expiry_inside_the_revision_window_is_rejected():
    """Finnhub revised a future earnings date 14 times across 42 tickers in ~2 months,
    median 7d and max 8d. An expiry only 3 days after the predicted date can end up
    BEFORE the print once the date is revised later — pricing no event while the line
    says it prices one (design §6.2)."""
    expiries = [{"expiry": "2026-09-04", "dte": 11, "straddle_pct": 6.0,
                 "atm_spread_pct": 10.0}]
    assert options.select_earnings_expiry(expiries, days_to_earnings=8, cfg=CFG) is None


def test_expiry_past_the_gap_ceiling_is_rejected():
    """Too far out and the straddle prices the event PLUS weeks of ordinary drift."""
    expiries = [{"expiry": "2026-11-06", "dte": 74, "straddle_pct": 9.0,
                 "atm_spread_pct": 10.0}]
    assert options.select_earnings_expiry(expiries, days_to_earnings=30, cfg=CFG) is None


def test_expiry_inside_the_window_is_selected():
    expiries = [
        {"expiry": "2026-09-04", "dte": 11, "straddle_pct": 6.0, "atm_spread_pct": 10.0},
        {"expiry": "2026-09-25", "dte": 32, "straddle_pct": 8.0, "atm_spread_pct": 10.0},
        {"expiry": "2026-11-06", "dte": 74, "straddle_pct": 9.0, "atm_spread_pct": 10.0},
    ]
    picked = options.select_earnings_expiry(expiries, days_to_earnings=20, cfg=CFG)
    assert picked is not None and picked["expiry"] == "2026-09-25"


def test_a_late_revision_cannot_leave_the_pick_before_the_print():
    """The property the guard exists for: whatever expiry is chosen for a date that is
    then revised LATER by up to the measured maximum, the expiry still follows the
    print."""
    expiries = [{"expiry": f"2026-09-{d:02d}", "dte": d - 24 + 31,
                 "straddle_pct": 7.0, "atm_spread_pct": 10.0} for d in (4, 11, 18, 25)]
    predicted = 20
    picked = options.select_earnings_expiry(expiries, days_to_earnings=predicted, cfg=CFG)
    assert picked is not None
    worst_case_print = predicted + CFG["earnings_date_uncertainty_days"]
    assert picked["dte"] >= worst_case_print


def test_selection_abstains_without_an_earnings_date():
    assert options.select_earnings_expiry([], days_to_earnings=None, cfg=CFG) is None


# --- context_line -----------------------------------------------------------------

def test_abstains_when_disabled_or_config_absent():
    surface = options.build_surface(_full_chain(), TODAY, CFG)
    assert options.context_line(surface, _M(0.25), None) is None
    assert options.context_line(surface, _M(0.25), {"enabled": False}) is None


def test_abstains_without_a_surface():
    assert options.context_line(None, _M(0.25), CFG) is None


def test_abstains_on_stale_quotes():
    """A holiday gap must not be rendered as if it were current."""
    payload = _full_chain(quote_time="2026-08-01T15:59:59")
    surface = options.build_surface(payload, TODAY, CFG)
    assert options.context_line(surface, _M(0.25), CFG) is None


def test_renders_the_iv_ratio_against_the_measured_reference():
    surface = options.build_surface(_full_chain(), TODAY, CFG)
    line = options.context_line(surface, _M(0.25), CFG, today=TODAY)
    assert line is not None
    assert "1.00" in line                      # iv30 25.0% / realized 25.0%
    assert "0.93" in line                      # the committed large-cap median
    assert "n=80" in line


def test_does_not_assert_the_variance_risk_premium():
    """Measured false on this cross-section: 60 of 80 names price implied UNDER
    realized, so the line must not tell the model implied always runs larger."""
    surface = options.build_surface(_full_chain(), TODAY, CFG)
    line = options.context_line(surface, _M(0.25), CFG, today=TODAY)
    assert "run larger" not in line
    assert "risk premium" not in line.lower()


def test_line_declares_itself_as_market_prices_not_filing_text():
    surface = options.build_surface(_full_chain(), TODAY, CFG)
    line = options.context_line(surface, _M(0.25), CFG, today=TODAY)
    assert "not filing facts" in line.lower()
    assert "not a forecast" in line.lower()


def test_items_abstain_independently():
    """A name with tradeable IV but an untradeable straddle renders the IV comparison
    and drops the implied move — 'excluded, never zeroed'."""
    expiry = datetime.date(2026, 9, 25)
    payload = _payload([
        _contract("XYZ", expiry, "P", 100, delta=-0.50, iv=0.29, bid=1.0, ask=5.0),
        _contract("XYZ", expiry, "C", 100, delta=0.50, iv=0.29, bid=1.0, ask=5.0),
    ])
    surface = options.build_surface(payload, TODAY, CFG)
    line = options.context_line(surface, _M(0.25, days_to_next=20), CFG, today=TODAY)
    assert line is not None
    assert "implied volatility" in line.lower()
    assert "straddle" not in line.lower()


def test_renders_realized_moves_beside_the_implied_one():
    """The anchor that makes an implied move interpretable at all (design §6.2)."""
    surface = options.build_surface(_full_chain(), TODAY, CFG)
    moves = [("2026-07-30", -7.4), ("2026-04-30", 3.2), ("2026-01-29", 0.5)]
    line = options.context_line(surface, _M(0.25, days_to_next=60), CFG,
                                earnings_moves=moves, today=TODAY)
    assert line is not None
    assert "-7.4" in line and "+3.2" in line
    assert "8-K Item 2.02" in line


def test_earnings_date_uncertainty_is_disclosed():
    surface = options.build_surface(_full_chain(), TODAY, CFG)
    line = options.context_line(surface, _M(0.25, days_to_next=60), CFG, today=TODAY)
    assert line is not None and "revised" in line.lower()


# --- the buffer is proximity-aware -------------------------------------------------

def test_no_uncertainty_buffer_inside_the_firm_window():
    """Measured: 0 of 14 observed calendar revisions happened with fewer than 12 days
    to go (range 12-36d) — the date firms up as the print approaches. Applying the full
    8-day buffer to a print 2 days out would skip the weekly expiry that actually
    straddles it, which is the case that matters most."""
    expiries = [{"expiry": "2026-08-28", "dte": 4, "straddle_pct": 5.0,
                 "atm_spread_pct": 8.0},
                {"expiry": "2026-09-04", "dte": 11, "straddle_pct": 6.0,
                 "atm_spread_pct": 8.0}]
    picked = options.select_earnings_expiry(expiries, days_to_earnings=2, cfg=CFG)
    assert picked is not None and picked["dte"] == 4


def test_buffer_still_applies_outside_the_firm_window():
    """A print 30 days out is squarely in the range where revisions were observed."""
    expiries = [{"expiry": "2026-09-25", "dte": 32, "straddle_pct": 6.0,
                 "atm_spread_pct": 8.0}]
    assert options.select_earnings_expiry(expiries, days_to_earnings=30, cfg=CFG) is None


def test_firm_window_boundary_is_inclusive_of_the_buffer():
    """At exactly the firm-window threshold the buffer still applies — the measured
    minimum lead time is 12 days and the default threshold sits below it."""
    firm = CFG["earnings_date_firm_within_days"]
    expiries = [{"expiry": "2026-09-04", "dte": firm + 1, "straddle_pct": 6.0,
                 "atm_spread_pct": 8.0}]
    assert options.select_earnings_expiry(expiries, days_to_earnings=firm,
                                          cfg=CFG) is None


def test_the_clause_discloses_the_gap_between_print_and_expiry():
    """The straddle spans the print PLUS the gap, so the gap must be visible."""
    surface = options.build_surface(_full_chain(), TODAY, CFG)
    line = options.context_line(surface, _M(0.25, days_to_next=24), CFG, today=TODAY)
    assert line is not None
    assert "straddle" in line.lower()
    assert "after the print" in line.lower()


def test_contracts_without_a_delta_are_not_candidates():
    """A missing delta must drop the contract, not stand in as a far-off near-miss."""
    expiry = datetime.date(2026, 9, 25)
    payload = _payload([
        {"option": f"XYZ{expiry:%y%m%d}P00090000", "bid": 1.0, "ask": 1.1, "iv": 0.30},
        {"option": f"XYZ{expiry:%y%m%d}C00110000", "bid": 1.0, "ask": 1.1, "iv": 0.28},
    ])
    surface = options.build_surface(payload, TODAY, CFG)
    assert surface is not None and surface.skew_pts is None

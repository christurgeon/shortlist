"""The investability floor: can a retail-scale book act on this name at all?

Distinct from `quality_floor`, which asks whether the BUSINESS is structurally broken.
This asks whether the SECURITY is reachable — a sound little company trading $20k/day is
a fine business and an unactionable idea, and the funnel should not spend a deep-screen
slot on it.

Measured motivation (docs/audits/2026-08-07-investability-floor.md): across the three
enabled originators the 25th-percentile pick is a **$15M** market cap, and a third of all
picks sit below $50M — shell territory that no account size can trade.
"""
from datetime import date

from shortlist.scout.investable import (Liquidity, assess, liquidity_from_universe,
                                        verdicts_from_liquidity)

FLOORS = {"min_market_cap": 100_000_000.0, "min_dollar_adv": 500_000.0}


def _liq(cap, adv_shares, price):
    return Liquidity(market_cap=cap, adv_shares=adv_shares, last_sale=price)


# --- the two floors ------------------------------------------------------------------

def test_a_liquid_midcap_is_kept():
    assert assess(_liq(3e9, 900_000, 40.0), **FLOORS).keep


def test_a_shell_below_the_cap_floor_is_dropped():
    v = assess(_liq(15e6, 900_000, 2.0), **FLOORS)
    assert not v.keep and "market cap" in v.reason


def test_an_illiquid_name_above_the_cap_floor_is_still_dropped():
    """The whole point of the ADV leg: cap is a poor proxy for tradeability. A $400M
    company trading $30k/day clears the cap floor and is still unactionable."""
    v = assess(_liq(400e6, 15_000, 2.0), **FLOORS)   # $30k/day
    assert not v.keep and "volume" in v.reason


def test_dollar_volume_not_share_volume_decides():
    """1M shares/day of a $0.20 stock is $200k/day — thin. Share count alone would pass it,
    which is why the existing short_interest ADV floor (a SHARE count) is not reused here."""
    assert not assess(_liq(400e6, 1_000_000, 0.20), **FLOORS).keep
    assert assess(_liq(400e6, 1_000_000, 1.00), **FLOORS).keep


# --- abstention: missing data must never drop a name ----------------------------------

def test_missing_market_cap_abstains():
    """Finnhub abstains on non-USD caps (the TSM fix), so `None` is COMMON and means
    'unknown', not 'small'. Dropping on absence would silently delete foreign issuers."""
    assert assess(_liq(None, 900_000, 40.0), **FLOORS).keep


def test_missing_volume_abstains():
    assert assess(_liq(3e9, None, 40.0), **FLOORS).keep


def test_missing_price_abstains_even_with_share_volume():
    """Without a price there is no dollar volume, and a share count alone must not decide."""
    assert assess(_liq(3e9, 900_000, None), **FLOORS).keep


def test_zero_and_negative_inputs_abstain_rather_than_drop():
    """A 0.0 from a bad payload is a parse artifact, not a measurement. FINRA's own rows
    carry 0-volume entries for non-trading issues."""
    assert assess(_liq(0.0, 900_000, 40.0), **FLOORS).keep
    assert assess(_liq(3e9, 0.0, 40.0), **FLOORS).keep
    assert assess(_liq(3e9, 900_000, 0.0), **FLOORS).keep


# --- assembly from the two bulk sources ------------------------------------------------

def test_liquidity_from_universe_joins_cap_and_volume():
    liq = liquidity_from_universe(
        universe={"AAA": (5e8, 12.0), "BBB": (2e7, 1.5)},
        adv_shares={"AAA": 300_000.0, "BBB": 50_000.0})
    assert liq["AAA"].market_cap == 5e8 and liq["AAA"].adv_shares == 300_000.0
    assert liq["BBB"].last_sale == 1.5


def test_a_ticker_missing_from_the_volume_source_still_gets_a_row():
    """93% FINRA coverage means ~7% carry no ADV. They must appear with adv_shares=None so
    `assess` abstains, not vanish (vanishing would read as 'no verdict' — same outcome here,
    but it would hide the coverage gap from the notes)."""
    liq = liquidity_from_universe(universe={"AAA": (5e8, 12.0)}, adv_shares={})
    assert liq["AAA"].adv_shares is None and assess(liq["AAA"], **FLOORS).keep


def test_verdicts_contain_only_drops():
    """Mirrors verdicts_from_fundamentals: funnel.apply_* treats absent-from-map as abstain,
    so emitting keeps would be redundant surface that can drift out of sync with that rule."""
    liq = liquidity_from_universe(
        universe={"KEEP": (5e9, 40.0), "SHELL": (5e6, 0.30)},
        adv_shares={"KEEP": 900_000.0, "SHELL": 900_000.0})
    v = verdicts_from_liquidity(liq, **FLOORS)
    assert set(v) == {"SHELL"} and not v["SHELL"].keep


def test_empty_inputs_yield_no_verdicts_so_the_funnel_is_untouched():
    assert verdicts_from_liquidity({}, **FLOORS) == {}
    assert liquidity_from_universe(universe={}, adv_shares={}) == {}


def test_date_is_not_an_input():
    """Guard against someone reintroducing a staleness rule here: FINRA ADV is semi-monthly
    and up to ~4 weeks old, which is fine for a liquidity floor and is documented at the
    fetch site. This leaf stays pure and time-free."""
    import inspect
    assert "date" not in inspect.signature(assess).parameters
    assert date  # imported only to assert it is unused by the leaf

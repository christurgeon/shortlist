from __future__ import annotations

import copy
import csv
import dataclasses
from pathlib import Path

import pytest
import yaml

from shortlist import scoring
from shortlist.models import ScoreCard, StockMetrics
from shortlist.providers.mock import MockProvider
from shortlist.scoring import (
    _avg, _block_on, _norm, check_flags, growth_score, insider_score, moat_score,
    momentum_score, piotroski_score, quality_score, risk_score, score, sue_score,
    value_fcf_yield_score, value_pe_vs_history_score, value_plus_evebit_score, value_score,
)
from shortlist.screen import _write_csv

# A deliberately clean config: every [0, 1] band makes 0.5 normalize to exactly
# 50, so we can reason about scores by hand. Independent of the shipped
# config.yaml (which the shipped-config tests exercise instead).
CONFIG = {
    "thresholds": {
        "roe": [0.0, 1.0],
        "net_margin": [0.0, 1.0],
        "interest_coverage": [0.0, 10.0],
        "debt_to_equity": [2.0, 0.0],  # inverted: less leverage scores higher
        "gross_margin": [0.0, 1.0],
        "gross_margin_stability": [0.0, 1.0],
        "roic": [0.0, 1.0],
        "revenue_cagr": [0.0, 1.0],
        "fcf_cagr": [0.0, 1.0],
        "eps_cagr": [0.0, 1.0],
        "revenue_growth_persistence": [0.0, 1.0],
        "price_vs_200dma": [0.0, 1.0],
        "rel_strength_6m": [0.0, 1.0],
        "eps_revision": [0.0, 1.0],
        "upside_to_target": [0.0, 1.0],
        "fcf_yield": [0.0, 1.0],
        "pe_vs_history": [0.0, 1.0],
        "peg": [2.0, 0.0],  # inverted; 0 → 100, 2 → 0. metrics_all_50 leaves peg=None so excluded.
        "insider_sentiment": [-1.0, 1.0],
        "insider_net_ratio": [-0.001, 0.001],
    },
    "weights": {"quality": 0.20, "moat": 0.20, "growth": 0.15,
                "value": 0.22, "momentum": 0.08, "insider": 0.15},
    "gates": {
        "min_market_cap": 2.0e9,
        "max_debt_to_equity": 5.0,
        "min_insider_sentiment": -0.60,
    },
}

SHIPPED = yaml.safe_load((Path(__file__).resolve().parents[1] / "config.yaml").read_text())


def _shipped() -> dict:
    return copy.deepcopy(SHIPPED)


def _with(**overrides) -> dict:
    """CONFIG with top-level blocks replaced (e.g. flags=..., ranking=...)."""
    return {**CONFIG, **overrides}


def metrics_all_50() -> StockMetrics:
    """Metrics crafted so every sub-score (and the composite) is exactly 50."""
    return StockMetrics(
        ticker="T", market_cap=10e9,
        # quality
        roe=0.5, net_margin=0.5, interest_coverage=5.0, debt_to_equity=1.0,
        # moat
        gross_margin=0.5, gross_margin_stability=0.5, roic=0.5,
        # growth
        revenue_cagr=0.5, fcf_cagr=0.5, eps_cagr=0.5, revenue_growth_persistence=0.5,
        # momentum
        price_vs_200dma=0.5, rel_strength_6m=0.5, eps_revision=0.5,
        # value: target/price - 1 = 0.5; pe_median/pe - 1 = 0.5
        price=100.0, target_median=150.0, pe_ttm=10.0, pe_median_5y=15.0,
        fcf_yield=0.5, fcf_positive=True,
        # insider: sentiment midpoint, zero net flow
        insider_sentiment=0.0, insider_net_6m=0.0,
    )


def _m(**kw) -> StockMetrics:
    return dataclasses.replace(metrics_all_50(), **kw)


# --- _norm / _avg ---------------------------------------------------------

def test_norm_endpoints_midpoint_and_clamp():
    assert _norm(0.0, 0.0, 1.0) == 0.0
    assert _norm(1.0, 0.0, 1.0) == 100.0
    assert _norm(0.5, 0.0, 1.0) == 50.0
    assert _norm(-1.0, 0.0, 1.0) == 0.0    # clamped low
    assert _norm(2.0, 0.0, 1.0) == 100.0   # clamped high


def test_norm_none_passthrough_and_degenerate_band():
    assert _norm(None, 0.0, 1.0) is None
    assert _norm(5.0, 2.0, 2.0) == 50.0    # hi == lo -> neutral 50


def test_nan_is_a_missing_leg_not_a_top_score():
    # min/max clamp NaN to the top of the band; it must abstain like None instead.
    nan = float("nan")
    assert _norm(nan, 0.0, 1.0) is None
    assert _norm(nan, 2.0, 0.0) is None
    m = _m(roe=nan, fcf_yield=nan, insider_sentiment=nan)
    absent = _m(roe=None, fcf_yield=None, insider_sentiment=None)
    got, want = score(m, CONFIG), score(absent, CONFIG)
    assert (got.quality, got.value, got.insider, got.composite, got.confidence) == \
        (want.quality, want.value, want.insider, want.composite, want.confidence)
    assert quality_score(m, CONFIG["thresholds"]) == 50.0


def test_norm_inverted_band_rewards_lower_values():
    # debt_to_equity band [2.0, 0.0]: 0 is best, 2 is worst.
    assert _norm(0.0, 2.0, 0.0) == 100.0
    assert _norm(2.0, 2.0, 0.0) == 0.0
    assert _norm(1.0, 2.0, 0.0) == 50.0
    assert _norm(3.0, 2.0, 0.0) == 0.0     # beyond worst, clamped


def test_avg_skips_none_and_handles_empty():
    assert _avg([10.0, None, 20.0]) == 15.0
    assert _avg([None, None]) is None
    assert _avg([]) is None


# --- sub-scores -----------------------------------------------------------

def test_all_midpoint_metrics_score_50_everywhere():
    m = metrics_all_50()
    t = CONFIG["thresholds"]
    assert quality_score(m, t) == 50.0
    assert moat_score(m, t) == 50.0
    assert growth_score(m, t) == 50.0
    assert momentum_score(m, t) == 50.0
    assert value_score(m, t) == 50.0
    assert insider_score(m, t) == 50.0

    card = score(m, CONFIG)
    assert card.composite == 50.0
    assert card.opportunity == 50.0
    assert card.passed


def test_moat_prefers_roic_5y_avg_over_spot_roic():
    # spot roic at 0.5 (=50), but a 5y average of 1.0 (=100) should win.
    m = _m(roic=0.5, roic_5y_avg=1.0)
    assert moat_score(m, CONFIG["thresholds"]) == pytest.approx((50 + 50 + 100) / 3)


def test_insider_net_flow_scaled_by_market_cap():
    # +$5M net buying at a $10B cap: ratio 0.0005 normalizes to 75 on [-0.001, 0.001];
    # sentiment midpoint stays 50 -> 62.5.
    m = _m(insider_net_6m=5e6, market_cap=10e9, insider_sentiment=0.0)
    assert insider_score(m, CONFIG["thresholds"]) == pytest.approx((50 + 75) / 2)


def test_growth_score_averages_present_legs_and_skips_none():
    # Only revenue_cagr (=> 50) and persistence (=> 100) present; fcf/eps None.
    m = StockMetrics(ticker="T", revenue_cagr=0.5, revenue_growth_persistence=1.0)
    assert growth_score(m, CONFIG["thresholds"]) == pytest.approx((50 + 100) / 2)
    assert growth_score(StockMetrics(ticker="T"), CONFIG["thresholds"]) is None


# --- opt-in legs ----------------------------------------------------------
# Each opt-in leg needs BOTH its config block and its threshold band. Rows: (band, the
# block that switches it on, the axis it joins, metrics carrying a maxed-out signal,
# that axis with the leg on, metrics with the signal missing).

_SUE_ON = dict(earnings_quarters=4, earnings_last_surprise_pct=10.0,
               earnings_surprise_dispersion=5.0, earnings_days_since_last_report=0)

OPT_IN_LEGS = {
    "dilution": ({"share_count_cagr": [0.04, -0.04]}, {"quality": {"dilution": {"enabled": True}}},
                 "quality", {"share_count_cagr": -0.04}, (50 * 4 + 100) / 5,
                 {"share_count_cagr": None}),
    "earnings_quality": ({"asset_growth": [0.30, -0.10], "accruals": [0.15, -0.15]},
                         {"quality": {"earnings_quality": {"enabled": True}}},
                         "quality", {"asset_growth": -0.10, "accruals": -0.15},
                         round((50 * 4 + 100 + 100) / 6, 1),
                         {"asset_growth": None, "accruals": None}),
    "shareholder_yield": ({"shareholder_yield": [-0.02, 0.10]},
                          {"value": {"shareholder_yield": {"enabled": True}}},
                          # peg is None in metrics_all_50, so value has 3 legs at 50.
                          "value", {"shareholder_yield": 0.10}, (50 * 3 + 100) / 4,
                          {"shareholder_yield": None}),
    "sue": ({"sue": [-2.0, 2.0]}, {"momentum": {"sue": {"enabled": True}}},
            # a fresh +10% beat with sigma 5 -> SUE +2.0 -> top of the band
            "momentum", _SUE_ON, (50 * 3 + 100) / 4,
            {**_SUE_ON, "earnings_last_surprise_pct": None}),
    "residual_momentum": ({"residual_momentum": [0.0, 1.0]},
                          {"momentum": {"residual": {"enabled": True}}},
                          "momentum", {"residual_momentum": 1.0}, (50 * 3 + 100) / 4,
                          {"residual_momentum": None}),
}


def _leg_config(name: str, *, band: bool = True, block: bool = True) -> dict:
    bands, blk = OPT_IN_LEGS[name][:2]
    c = {**CONFIG, "thresholds": {**CONFIG["thresholds"], **(bands if band else {})}}
    return {**c, **blk} if block else c


@pytest.mark.parametrize("name", OPT_IN_LEGS)
def test_opt_in_leg_is_ignored_without_its_block(name):
    # The signal and its band are present, but no block: byte-identical to baseline.
    _, _, axis, on, _, _ = OPT_IN_LEGS[name]
    card = score(_m(**on), _leg_config(name, block=False))
    assert getattr(card, axis) == 50.0
    assert card.composite == score(metrics_all_50(), CONFIG).composite


@pytest.mark.parametrize("name", OPT_IN_LEGS)
def test_opt_in_leg_is_ignored_without_its_band(name):
    _, _, axis, on, _, _ = OPT_IN_LEGS[name]
    assert getattr(score(_m(**on), _leg_config(name, band=False)), axis) == 50.0


@pytest.mark.parametrize("name", OPT_IN_LEGS)
def test_opt_in_leg_is_really_wired(name):
    # Guard against a silently-unwired no-op leg: the axis and the composite must move.
    _, _, axis, on, expected, _ = OPT_IN_LEGS[name]
    card = score(_m(**on), _leg_config(name))
    assert getattr(card, axis) == pytest.approx(expected)
    assert card.composite != score(_m(**on), CONFIG).composite


@pytest.mark.parametrize("name", OPT_IN_LEGS)
def test_opt_in_leg_redistributes_when_its_signal_is_missing(name):
    _, _, axis, _, _, missing = OPT_IN_LEGS[name]
    assert getattr(score(_m(**missing), _leg_config(name)), axis) == 50.0


@pytest.mark.parametrize("name,low,high,low_expected", [
    # +4%/yr issuance sits at the bottom of the inverted band; a -4%/yr buyback at the top.
    ("dilution", {"share_count_cagr": 0.04}, {"share_count_cagr": -0.04}, (50 * 4 + 0) / 5),
    # ballooning assets + high accruals vs shrinking assets + cash-backed earnings
    ("earnings_quality", {"asset_growth": 0.30, "accruals": 0.15},
     {"asset_growth": -0.10, "accruals": -0.15}, round((50 * 4 + 0 + 0) / 6, 1)),
    # a net debt ISSUER carries a negative yield (bottom of the band)
    ("shareholder_yield", {"shareholder_yield": -0.02}, {"shareholder_yield": 0.10},
     (50 * 3 + 0) / 4),
    ("sue", {**_SUE_ON, "earnings_last_surprise_pct": -10.0}, _SUE_ON, (50 * 3 + 0) / 4),
    ("residual_momentum", {"residual_momentum": 0.0}, {"residual_momentum": 1.0},
     (50 * 3 + 0) / 4),
])
def test_opt_in_leg_points_the_registered_way(name, low, high, low_expected):
    c, axis = _leg_config(name), OPT_IN_LEGS[name][2]
    lo, hi = getattr(score(_m(**low), c), axis), getattr(score(_m(**high), c), axis)
    assert lo == pytest.approx(low_expected)
    assert hi > lo


@pytest.mark.parametrize("name,axis,legs,signal", [
    ("earnings_quality", "quality", ["asset_growth", "accruals"],
     {"asset_growth": 0.30, "accruals": 0.15}),
    ("shareholder_yield", "value", ["shareholder_yield"], {"shareholder_yield": 0.10}),
])
def test_opt_in_leg_is_masked_for_financials(name, axis, legs, signal):
    # Production sector mask: the leg abstains even with its block on and is recorded
    # inapplicable (the backtest axis stays unmasked).
    c = _leg_config(name)
    c["sectors"] = {"buckets": [{"name": "financials", "sic_ranges": [[6020, 6099]]}],
                    "masked_legs": legs}
    card = score(_m(sic="6020", **signal), c)
    assert card.sic_bucket == "financials"
    assert getattr(card, axis) == 50.0
    assert set(legs) <= {a["field"] for a in card.abstentions if a["reason"] == "inapplicable"}


def test_dilution_signal_is_surfaced_but_unscored_without_its_block():
    m = _m(share_count_cagr=0.10, eps_cagr_ps=0.0)
    card = score(m, _leg_config("dilution", block=False))
    assert card.growth == 50.0           # still uses net-income-proxy eps_cagr
    assert "dilution" not in card.flags
    assert card.share_count_cagr == 0.10


def test_per_share_eps_cagr_used_when_dilution_block_on():
    c = _leg_config("dilution")
    # Net-income proxy says 50% growth; the per-share series says 0% (all the "growth"
    # was share issuance). With the block on, growth uses per-share.
    m = _m(eps_cagr=0.5, eps_cagr_ps=0.0, share_count_cagr=0.0)
    assert score(m, c).growth == pytest.approx((50 + 50 + 0 + 50) / 4)
    assert score(m, CONFIG).growth == 50.0
    # Block on but no per-share series -> fall back to the net-income proxy.
    assert score(_m(eps_cagr=0.5, eps_cagr_ps=None), c).growth == 50.0


def test_dilution_flag_is_advisory_and_config_gated():
    flag_cfg = _with(flags={"dilution": {"min_share_cagr": 0.03}})
    card = score(_m(share_count_cagr=0.05), flag_cfg)
    assert "dilution" in card.flags
    assert card.passed                      # advisory only: never disqualifies
    assert "dilution" not in score(_m(share_count_cagr=0.01), flag_cfg).flags
    assert "dilution" not in score(_m(share_count_cagr=0.05), CONFIG).flags


def test_earnings_quality_per_leg_toggle():
    # asset_growth: false scores accruals alone; no per-leg keys scores both.
    from shortlist.scoring import _quality_legs
    m = _m(asset_growth=0.30, accruals=0.15)
    accruals_only = _leg_config("earnings_quality")
    accruals_only["quality"] = {"earnings_quality": {"enabled": True, "asset_growth": False}}
    names = {leg.name for leg in _quality_legs(m, accruals_only)}
    assert "accruals" in names and "asset_growth" not in names
    names_both = {leg.name for leg in _quality_legs(m, _leg_config("earnings_quality"))}
    assert {"accruals", "asset_growth"} <= names_both


def test_sue_leg_decays_with_staleness():
    on = _leg_config("sue")
    fresh = _m(**{**_SUE_ON, "earnings_days_since_last_report": 0})    # SUE 2.0 -> 100
    half = _m(**{**_SUE_ON, "earnings_days_since_last_report": 30})    # SUE 1.0 -> 75
    stale = _m(**{**_SUE_ON, "earnings_days_since_last_report": 60})   # SUE 0.0 -> 50
    assert score(fresh, on).momentum > score(half, on).momentum > score(stale, on).momentum
    assert score(stale, on).momentum == 50.0


@pytest.mark.parametrize("kw", [
    {"earnings_surprise_dispersion": 0.0},                               # four equal surprises
    {"earnings_quarters": 1, "earnings_surprise_dispersion": None},      # one quarter
    {"earnings_surprise_dispersion": 0.5},                               # below the sigma floor
])
def test_sue_sigma_guard_abstains_no_inf_nan(kw):
    assert score(_m(**{**_SUE_ON, **kw}), _leg_config("sue")).momentum == 50.0
    assert sue_score(_m(**{**_SUE_ON, **kw}), {"sue": [-2.0, 2.0]}) is None


def test_sue_backtest_axis():
    t = {"sue": [-2.0, 2.0]}
    assert sue_score(_m(**_SUE_ON), t) == 100.0
    assert sue_score(_m(**{**_SUE_ON, "earnings_last_surprise_pct": -10.0}), t) == 0.0
    assert sue_score(_m(**_SUE_ON), {}) is None


# --- shipped config.yaml -----------------------------------------------------

def test_shipped_config_price_axis_band_directions():
    # The price-axis SIGNAL DIRECTION lives in the shipped bands, so a flip of the REAL
    # band (e.g. max_daily_return to [0.02, 0.15]) must be caught here.
    t = SHIPPED["thresholds"]
    assert t["max_daily_return"][0] > t["max_daily_return"][1], \
        "max_daily_return band must be INVERTED [high, low] (a lottery spike must score LOW)"
    assert t["pct_to_52w_high"][0] < t["pct_to_52w_high"][1]
    assert t["vol_scaled_momentum"][0] < t["vol_scaled_momentum"][1]


def test_shipped_config_disables_earnings_quality_legs():
    # Killed on evidence: docs/audits/2026-07-12-accruals-leg-disable.md. The shipped
    # config must add neither leg, byte-identical to dropping the block entirely.
    from shortlist.scoring import _quality_legs
    cfg = _shipped()
    m = _m(asset_growth=0.30, accruals=0.15)
    names = {leg.name for leg in _quality_legs(m, cfg)}
    assert "accruals" not in names and "asset_growth" not in names
    no_block = {**cfg, "quality": {k: v for k, v in (cfg.get("quality") or {}).items()
                                   if k != "earnings_quality"}}
    assert score(m, cfg).quality == score(m, no_block).quality
    assert score(m, cfg).composite == score(m, no_block).composite


def test_shipped_config_enables_residual_momentum_leg():
    cfg = _shipped()
    assert _block_on(cfg, "momentum", "residual") is True
    assert "residual_momentum" in cfg["thresholds"]
    m = _m(residual_momentum=0.0)
    with_signal = score(m, cfg).momentum
    without = score(dataclasses.replace(m, residual_momentum=None), cfg).momentum
    assert with_signal is not None and without is not None
    assert with_signal != without


def test_shipped_config_displays_risk_without_weighting_it():
    # docs/audits/2026-09-25-risk-tilt-disable.md
    cfg = _shipped()
    w = cfg["weights"]
    assert w["risk"] == 0.0
    assert "opportunity" not in w
    assert (w["value"], w["momentum"]) == (0.22, 0.08)
    t = cfg["thresholds"]
    assert t["realized_vol"] == [0.45, 0.15]
    assert t["max_drawdown"] == [-0.50, -0.10]
    safe = _m(realized_vol=0.15, max_drawdown=-0.10)
    risky = _m(realized_vol=0.45, max_drawdown=-0.50)
    assert score(safe, cfg).risk == 100.0
    assert score(risky, cfg).risk == 0.0
    assert score(safe, cfg).composite == score(risky, cfg).composite


def test_shipped_config_has_ranking_thin_below():
    assert SHIPPED["ranking"]["thin_below"] == 0.40


def test_score_runs_against_shipped_config_and_mock_data():
    card = score(MockProvider().fetch("GOOGL"), _shipped())
    assert card.ticker == "GOOGL"
    assert 0.0 <= card.composite <= 100.0
    assert card.quality is not None and card.moat is not None
    assert card.opportunity == max(x for x in (card.momentum, card.value) if x is not None)


# --- upside_to_target opt-out ---------------------------------------------

def test_upside_to_target_default_is_on_and_byte_identical():
    """The ONLY opt-OUT leg in the scorer. An absent key, an absent `value:` block and a
    `value:` block about something else must all keep it ON — otherwise shipping the knob
    would itself have changed every live value score."""
    m = metrics_all_50()
    baseline = score(m, CONFIG).value
    assert baseline == 50.0
    for cfg in (_with(value={}),
                _with(value={"shareholder_yield": {"enabled": True}}),
                _with(value={"upside_to_target": {}}),
                _with(value={"upside_to_target": {"enabled": True}})):
        assert score(m, cfg).value == baseline


def test_upside_to_target_can_be_switched_off_and_the_weight_redistributes():
    off = _with(value={"upside_to_target": {"enabled": False}})
    # A name the target leg RATES HIGHLY and the other legs do not: off, only fcf_yield /
    # pe_vs_history survive (peg is None in this fixture), so value must fall.
    m = _m(target_median=1000.0)
    assert score(m, CONFIG).value > score(m, off).value
    assert score(m, off).value == 50.0
    assert score(m, off).composite != score(m, CONFIG).composite
    on_card, off_card = score(metrics_all_50(), CONFIG), score(metrics_all_50(), off)
    for axis in ("quality", "moat", "growth", "momentum", "insider"):
        assert getattr(off_card, axis) == getattr(on_card, axis)


def test_upside_to_target_accepts_the_bare_boolean_spelling():
    """`{enabled: false}` and a bare `false` must BOTH switch the leg off: silently ignoring
    the bare form would make a "leg-off" measurement byte-identical to baseline. A valueless
    key (YAML null) is treated as ABSENT, i.e. the shipped default."""
    m = _m(target_median=1000.0)
    on = score(m, CONFIG).value
    for off_cfg in (_with(value={"upside_to_target": False}),
                    _with(value={"upside_to_target": {"enabled": False}})):
        assert score(m, off_cfg).value < on
    for on_cfg in (_with(value={"upside_to_target": True}),
                   _with(value={"upside_to_target": None})):
        assert score(m, on_cfg).value == on


# --- composite ------------------------------------------------------------

def test_value_and_momentum_weighted_independently():
    # momentum maxed (all 1.0 -> 100), value floored (upside/fcf/pe all 0).
    m = _m(price_vs_200dma=1.0, rel_strength_6m=1.0, eps_revision=1.0,
           target_median=100.0, fcf_yield=0.0, pe_median_5y=10.0)
    card = score(m, CONFIG)
    assert card.momentum == 100.0
    assert card.value == 0.0
    # opportunity (display only) still reports the stronger axis.
    assert card.opportunity == 100.0
    # num = 50*0.20 + 50*0.20 + 50*0.15 + 100*0.08 + 0*0.22 + 50*0.15 = 43.0; den = 1.00
    assert card.composite == 43.0


def test_missing_components_are_excluded_and_weights_renormalized():
    # Only quality is computable; everything else is None.
    m = StockMetrics(ticker="T", market_cap=10e9,
                     roe=0.5, net_margin=0.5, interest_coverage=5.0, debt_to_equity=1.0)
    card = score(m, CONFIG)
    assert card.quality == 50.0
    for axis in ("moat", "growth", "momentum", "value", "opportunity", "insider"):
        assert getattr(card, axis) is None
    # Composite == quality alone: the missing weight is dropped, not scored as zero.
    assert card.composite == 50.0


def test_composite_is_zero_when_nothing_is_scorable():
    assert score(StockMetrics(ticker="EMPTY"), CONFIG).composite == 0.0


@pytest.mark.parametrize("extra", [
    {"financial_series": [{"fiscal_year": 2025, "revenue": 1.0e9, "net_income": 2.0e8}]},
    "reverse_dcf",
])
def test_research_only_inputs_do_not_affect_scoring(extra):
    m = StockMetrics(ticker="X", revenue_cagr=0.1, net_margin=0.2, roic=0.2,
                     gross_margin=0.4, fcf_yield=0.05, market_cap=2000e6,
                     financial_series=[{"free_cash_flow": 100e6}])
    base = score(m, CONFIG)
    if extra == "reverse_dcf":
        after = score(m, _with(research={"reverse_dcf": {
            "enabled": True, "discount_rate": 0.99, "base_years": 1}}))
    else:
        after = score(dataclasses.replace(m, **extra), CONFIG)

    def fields(c):
        return (c.composite, c.quality, c.moat, c.growth, c.momentum,
                c.value, c.insider, c.risk, c.gates, c.flags)
    assert fields(base) == fields(after)


# --- gates ----------------------------------------------------------------

@pytest.mark.parametrize("field,value,expected_gate", [
    ("fcf_positive", False, "negative_fcf"),
    ("market_cap", 1.0e9, "below_min_mktcap"),
    ("debt_to_equity", 6.0, "over_leveraged"),
    ("insider_sentiment", -0.7, "heavy_insider_selling"),
])
def test_each_gate_trips_independently(field, value, expected_gate):
    card = score(_m(**{field: value}), CONFIG)
    assert expected_gate in card.gates
    assert not card.passed


def test_clean_metrics_trip_no_gates():
    card = score(metrics_all_50(), CONFIG)
    assert card.gates == []
    assert card.passed


def test_gate_untouched_by_10b5_1_when_conviction_off():
    # With conviction OFF, a detected planned sale must change neither the gate nor the
    # composite.
    m_flag = StockMetrics(ticker="X", insider_sentiment=-0.9, market_cap=5e9,
                          insider_planned_sell_value=9e6)
    m_plain = StockMetrics(ticker="X", insider_sentiment=-0.9, market_cap=5e9)
    a, b = score(m_flag, SHIPPED), score(m_plain, SHIPPED)
    assert "heavy_insider_selling" in a.gates
    assert a.composite == b.composite
    assert a.passed == b.passed and a.gates == b.gates


# --- PEG --------------------------------------------------------------------

_PEG_T = {"peg": [3.0, 0.5], "upside_to_target": [0.0, 1.0],
          "fcf_yield": [0.0, 1.0], "pe_vs_history": [0.0, 1.0]}


def test_peg_contributes_to_value_score():
    # Lower PEG = better value, so the band is inverted. Only peg is set among the value
    # legs (pe_vs_history also needs pe_median_5y), so peg drives the score alone.
    assert value_score(StockMetrics(ticker="T", peg=0.5, pe_ttm=20.0), _PEG_T) == 100.0
    assert value_score(StockMetrics(ticker="T", peg=3.0, pe_ttm=20.0), _PEG_T) == 0.0
    assert value_score(StockMetrics(ticker="T", peg=1.75, pe_ttm=20.0), _PEG_T) == \
        pytest.approx(50.0)


@pytest.mark.parametrize("peg", [-5.9, -0.9, 0.0])
def test_non_positive_peg_abstains_instead_of_scoring_max(peg):
    # A PEG <= 0 would clamp to 100 on the inverted band, so it must abstain.
    assert value_score(StockMetrics(ticker="T", peg=peg, pe_ttm=20.0), _PEG_T) is None
    assert value_score(StockMetrics(ticker="T", peg=peg, pe_ttm=20.0, fcf_yield=0.5),
                       _PEG_T) == pytest.approx(50.0)


@pytest.mark.parametrize("pe_ttm", [-15.0, 0.0, None])
def test_positive_peg_without_positive_pe_abstains(pe_ttm):
    # A loss-maker with shrinking losses: negative P/E over negative growth reads cheap.
    assert value_score(StockMetrics(ticker="T", peg=0.8, pe_ttm=pe_ttm), _PEG_T) is None


def test_non_positive_peg_abstains_on_the_production_path():
    base = score(metrics_all_50(), CONFIG)
    neg = score(_m(peg=-5.9), CONFIG)
    assert (neg.value, neg.composite) == (base.value, base.composite)


# --- flags: soft, non-disqualifying advisories ------------------------------

FLAGS_CFG = {"crowded_short": {
    "min_short_pct_outstanding": 0.10, "min_days_to_cover": 5.0,
    "require_rising": True, "max_staleness_days": 35,
}}


def _crowded_metrics(**kw):
    base = dict(short_pct_outstanding=0.15, days_to_cover=6.0,
                short_interest_rising=True, short_data_age_days=10)
    base.update(kw)
    return StockMetrics(ticker="X", **base)


def test_check_flags_trips_on_full_conjunction():
    assert check_flags(_crowded_metrics(), FLAGS_CFG) == ["crowded_short"]


@pytest.mark.parametrize("kw", [
    {"short_pct_outstanding": 0.05}, {"days_to_cover": 3.0},
    {"short_interest_rising": False}, {"short_data_age_days": 40},   # stale
])
def test_check_flags_each_clause_suppresses(kw):
    assert check_flags(_crowded_metrics(**kw), FLAGS_CFG) == []


def test_check_flags_none_inputs_are_noop():
    assert check_flags(StockMetrics(ticker="X"), FLAGS_CFG) == []
    assert check_flags(_crowded_metrics(), {}) == []


def test_score_carries_flags_and_passed_unaffected():
    card = score(_crowded_metrics(market_cap=5.0e9), _with(flags=FLAGS_CFG))
    assert "crowded_short" in card.flags
    assert card.passed is True


def test_flag_blocks_honor_enabled_false():
    # `enabled: false` under any soft-flag block behaves exactly like the block being
    # absent, while the same block without the key still fires.
    cases = [
        ("crowded_short", _crowded_metrics(), FLAGS_CFG["crowded_short"]),
        ("insider_cluster_buy", StockMetrics(ticker="X", insider_distinct_buyers=3),
         {"min_distinct": 3}),
        ("planned_sale", StockMetrics(ticker="X", insider_planned_sell_value=100.0),
         {"min_value": 1}),
        ("dilution", StockMetrics(ticker="X", share_count_cagr=0.05),
         {"min_share_cagr": 0.03}),
        ("cash_burn", StockMetrics(ticker="X", fcf_positive=False),
         {"enabled": True}),
        ("social_hype", StockMetrics(ticker="X", social_mentions=100),
         {"min_mentions": 50, "max_staleness_days": 2}),
        ("news_spike", StockMetrics(ticker="X", news_count_7d=20),
         {"min_count_7d": 10, "max_staleness_days": 3}),
        ("filing_text_change", StockMetrics(ticker="X", filing_text_similarity=0.5),
         {"max_similarity": 0.7}),
    ]
    for name, m, block in cases:
        assert name in check_flags(m, {name: dict(block)}), f"{name} should fire"
        assert name not in check_flags(m, {name: {**block, "enabled": False}}), \
            f"{name} must honor enabled: false"
        assert name not in check_flags(m, {}), f"{name} absent-block baseline"


@pytest.mark.parametrize("name,field,at,below", [
    ("insider_cluster_buy", "insider_distinct_buyers", 3, 2),
    ("planned_sale", "insider_planned_sell_value", 100.0, 0.0),
])
def test_insider_flags_fire_at_their_threshold(name, field, at, below):
    f = {"insider_cluster_buy": {"min_distinct": 3}, "planned_sale": {"min_value": 1}}
    assert name in check_flags(StockMetrics(ticker="X", **{field: at}), f)
    assert name not in check_flags(StockMetrics(ticker="X", **{field: below}), f)
    assert check_flags(StockMetrics(ticker="X", **{field: at}), {}) == []


_HYPE_CFG = {"social_hype": {"min_mentions": 50, "min_mention_delta_pct": 0.5,
                             "require_rising": True, "max_staleness_days": 2}}
_HYPE = dict(social_mentions=300, social_mentions_rising=True,
             social_mention_delta_pct=2.0, social_data_age_days=0)


@pytest.mark.parametrize("kw,fires", [
    ({}, True),
    ({"social_mentions_rising": False}, False),
    ({"social_data_age_days": 10}, False),          # stale
    ({"social_mentions": 10}, False),               # below the mention floor
    ({"social_mention_delta_pct": 0.1}, False),     # below the velocity floor
    ({"social_mention_delta_pct": None}, True),     # a brand-new spike has no baseline
])
def test_social_hype_flag(kw, fires):
    m = StockMetrics(ticker="GME", **{**_HYPE, **kw})
    assert ("social_hype" in check_flags(m, _HYPE_CFG)) is fires
    assert check_flags(m, {}) == []


def test_risk_off_regime_honors_enabled_false():
    from shortlist.data.macro import MacroContext
    risk_off = MacroContext("2026-06-01", 4.45, -0.2, 5.4, 27.0, 3.64, "risk-off", True)
    m = StockMetrics(ticker="LEV", market_cap=10e9, net_debt_to_ebitda=4.5)
    block = {"max_net_debt_ebitda": 3.0, "max_debt_to_equity": 1.5, "cyclical_buckets": []}
    assert "risk_off_regime" in score(m, _with(flags={"risk_off_regime": dict(block)}),
                                      macro=risk_off).flags
    off = _with(flags={"risk_off_regime": {**block, "enabled": False}})
    assert "risk_off_regime" not in score(m, off, macro=risk_off).flags


# --- value_trap ---------------------------------------------------------------

VT_CFG = {"value_trap": {"min_value_score": 60, "max_quality_score": 40,
                         "max_growth_score": 40}}
VT_PIO = {**VT_CFG["value_trap"],
          "piotroski": {"suppress_at": 0.83, "confirm_at": 0.40, "min_legs": 4}}


def _value_trap_metrics(**kw):
    # value high (~80 on the [0,1] CONFIG bands), quality low (~20). growth stays 50, so
    # the flag must fire via the quality clause.
    return _m(**{**dict(price=100.0, target_median=180.0, fcf_yield=0.8,
                        pe_ttm=10.0, pe_median_5y=18.0, peg=0.4, roe=0.2, net_margin=0.2,
                        interest_coverage=2.0, debt_to_equity=1.6), **kw})


_STRONG = dict(roe=0.9, net_margin=0.9, interest_coverage=9.0, debt_to_equity=0.2)


def _pio_cfg(sectors=None):
    return _with(flags={"value_trap": VT_PIO},
                 thresholds={**CONFIG["thresholds"], "piotroski_f": [0.34, 1.00]},
                 sectors=sectors or {"buckets": [], "masked_legs": ["piotroski_f"]})


def test_value_trap_fires_when_cheap_and_weak():
    card = score(_value_trap_metrics(), _with(flags=VT_CFG))
    assert card.value >= 60 and card.quality < 40
    assert "value_trap" in card.flags
    assert card.passed is True            # advisory only — never disqualifies


def test_value_trap_silent_when_fundamentals_strong():
    card = score(_value_trap_metrics(**_STRONG), _with(flags=VT_CFG))
    assert card.value >= 60 and card.quality >= 40
    assert "value_trap" not in card.flags


def test_value_trap_silent_when_value_missing():
    m = StockMetrics(ticker="T", market_cap=10e9, roe=0.2, net_margin=0.2)
    card = score(m, _with(flags=VT_CFG))
    assert card.value is None
    assert "value_trap" not in card.flags


@pytest.mark.parametrize("cfg", [
    CONFIG, _with(flags={"value_trap": {**VT_CFG["value_trap"], "enabled": False}})])
def test_value_trap_off_without_an_enabled_block(cfg):
    assert "value_trap" not in score(_value_trap_metrics(), cfg).flags


def test_piotroski_score_maps_fraction_through_band():
    m = StockMetrics(ticker="T", piotroski_f=5, piotroski_f_legs=6)
    s = piotroski_score(m, {"piotroski_f": [0.34, 1.00]})
    assert s == pytest.approx((0.8333333 - 0.34) / (1.00 - 0.34) * 100, abs=0.5)
    assert piotroski_score(StockMetrics(ticker="T", piotroski_f=2, piotroski_f_legs=3),
                           {"piotroski_f": [0.34, 1.00]}) is None    # below min legs


def test_value_trap_suppressed_when_fundamentals_improving():
    # cheap + weak quality would fire; a high F-fraction suppresses it.
    card = score(_value_trap_metrics(piotroski_f=6, piotroski_f_legs=6), _pio_cfg())
    assert card.value >= 60 and card.quality < 40
    assert "value_trap" not in card.flags


def test_value_trap_confirmed_when_fundamentals_deteriorating():
    # cheap + STRONG quality (base would NOT fire), but a low F-fraction confirms.
    card = score(_value_trap_metrics(**_STRONG, piotroski_f=1, piotroski_f_legs=6),
                 _pio_cfg())
    assert card.value >= 60 and card.quality >= 40
    assert "value_trap" in card.flags


def test_value_trap_masked_bucket_does_not_use_piotroski():
    # financials -> piotroski_f masked -> the base logic alone, so suppression is skipped.
    sectors = {"buckets": [{"name": "financials", "sic_ranges": [[6020, 6099]]}],
               "masked_legs": ["piotroski_f"]}
    card = score(_value_trap_metrics(sic="6020", piotroski_f=6, piotroski_f_legs=6),
                 _pio_cfg(sectors))
    assert "value_trap" in card.flags


def test_value_trap_backcompat_no_piotroski_block():
    fires = score(_value_trap_metrics(piotroski_f=6, piotroski_f_legs=6), _with(flags=VT_CFG))
    assert "value_trap" in fires.flags


# --- risk sub-score -----------------------------------------------------------

_RISK_T = {"realized_vol": [0.45, 0.15], "max_drawdown": [-0.50, -0.10]}


def _risk_config():
    c = copy.deepcopy(CONFIG)
    c["thresholds"].update(_RISK_T)
    c["weights"] = {"quality": 0.18, "moat": 0.18, "growth": 0.135,
                    "value": 0.22, "momentum": 0.08, "insider": 0.135, "risk": 0.10}
    return c


def test_risk_score_direction_and_clamp():
    assert risk_score(_m(realized_vol=0.15, max_drawdown=-0.10), _RISK_T) == 100.0
    assert risk_score(_m(realized_vol=0.45, max_drawdown=-0.50), _RISK_T) == 0.0
    assert risk_score(_m(realized_vol=0.30, max_drawdown=-0.30), _RISK_T) == 50.0
    # vol capped at 100, drawdown floored at 0
    assert risk_score(_m(realized_vol=0.05, max_drawdown=-0.80), _RISK_T) == 50.0
    assert risk_score(_m(realized_vol=None, max_drawdown=None), _RISK_T) is None


def test_composite_shifts_when_risk_present():
    card = score(_m(realized_vol=0.15, max_drawdown=-0.10), _risk_config())
    assert card.risk == 100.0
    # composite = (50*0.93 + 100*0.10) / 1.03 = 56.5/1.03 = 54.9
    assert card.composite == 54.9


def test_risk_absent_leaves_composite_and_confidence_untouched():
    rc = _risk_config()
    m = _m(realized_vol=None, max_drawdown=None)
    a, b = score(m, CONFIG), score(m, rc)
    assert a.composite == b.composite == 50.0
    assert (a.confidence, a.scored, a.passed) == (b.confidence, b.scored, b.passed)
    assert all(x.get("field") != "risk" for x in b.abstentions)


def test_no_keyerror_on_config_without_risk():
    assert score(_m(realized_vol=0.20, max_drawdown=-0.15), CONFIG).risk is None


# --- thin, confidence and CSV columns -------------------------------------------

_MOM_ONLY = StockMetrics(ticker="MOM", price_vs_200dma=0.2, rel_strength_6m=0.2,
                         eps_revision=0.05)


def test_thin_flag_set_below_threshold():
    card = score(_MOM_ONLY, _with(ranking={"thin_below": 0.5}))
    assert 0.0 < card.confidence < 0.5
    assert card.thin is True


def test_thin_flag_false_above_threshold():
    assert score(metrics_all_50(), _with(ranking={"thin_below": 0.5})).thin is False


def test_thin_noop_when_ranking_absent():
    assert score(_MOM_ONLY, CONFIG).thin is False


def test_value_gated_financials_still_scored_worst_case():
    # Financials (SIC 6020): moat masked; quality/growth/value absent, so only momentum +
    # insider are present.
    # appl_w = quality .18 + growth .135 + value .22 + momentum .08 + insider .135
    #        = 0.75 (moat .18 masked); pres_w = .08 + .135 = 0.215
    # confidence = 0.215 / 0.75 = 0.287 -> must clear min_scored_weight 0.25.
    m = StockMetrics(ticker="BANK", sic="6020", market_cap=10e9,
                     price_vs_200dma=0.1, rel_strength_6m=0.1, eps_revision=0.02,
                     insider_sentiment=0.0, insider_net_6m=0.0)
    card = score(m, SHIPPED)
    assert card.sic_bucket == "financials"
    assert card.quality is None and card.growth is None and card.value is None
    assert card.momentum is not None and card.insider is not None
    assert card.confidence == pytest.approx(0.287, abs=0.005)
    assert card.scored is True
    assert card.passed is True


def test_insider_only_financials_not_scored():
    # Only insider present -> confidence 0.135 / 0.75 = 0.18 < 0.25 -> not scored.
    m = StockMetrics(ticker="BANK2", sic="6020", market_cap=10e9,
                     insider_sentiment=0.0, insider_net_6m=0.0)
    card = score(m, SHIPPED)
    assert card.sic_bucket == "financials"
    assert card.confidence == pytest.approx(0.18, abs=0.005)
    assert card.scored is False
    assert card.passed is False


def _csv_row(tmp_path, card):
    path = tmp_path / "out.csv"
    _write_csv([card], str(path))
    header, row = list(csv.reader(path.open()))[:2]
    return header, row


def test_csv_has_aligned_risk_column(tmp_path):
    card = score(_m(realized_vol=0.15, max_drawdown=-0.10, sic=None), _risk_config())
    header, row = _csv_row(tmp_path, card)
    assert row[header.index("risk")] == str(card.risk)


def test_csv_has_confidence_column_after_scored(tmp_path):
    card = ScoreCard(ticker="T", composite=60.0, quality=None, moat=None, growth=None,
                     momentum=None, value=None, opportunity=None, insider=None,
                     confidence=0.42, scored=True)
    header, row = _csv_row(tmp_path, card)
    assert header.index("confidence") == header.index("scored") + 1
    assert row[header.index("confidence")] == str(card.confidence)


# --- insider conviction (config-gated, no-op default) ----------------------------

def _t():
    return SHIPPED["thresholds"]


def _conv_cfg():
    c = _shipped()
    c["thresholds"]["insider_cluster"] = [1, 4]
    c["thresholds"]["insider_role_buy_ratio"] = [0.0, 0.001]
    c["insider"] = {"conviction": {
        "enabled": True, "min_cluster_buy_value": 1000, "planned_sell_discount": 0.5,
        "role_weights": {"c_suite": 1.5, "officer": 1.2, "director": 1.0, "ten_pct": 0.5,
                         "unknown": 1.0},
    }}
    return c


def test_insider_conviction_off_or_without_form4_is_identity():
    full = StockMetrics(ticker="X", insider_sentiment=0.2, insider_net_6m=1e6, market_cap=1e10,
                        insider_distinct_buyers=4, insider_role_weighted_buy_value=5e6,
                        insider_planned_sell_value=2e5)
    assert insider_score(full, _t(), SHIPPED) == insider_score(full, _t())
    bare = StockMetrics(ticker="X", insider_sentiment=0.2, insider_net_6m=1e6, market_cap=1e10)
    c = _conv_cfg()
    assert insider_score(bare, c["thresholds"], c) == insider_score(bare, _t())


def test_insider_cluster_and_csuite_raise_score():
    base = StockMetrics(ticker="X", insider_sentiment=0.0, insider_net_6m=0.0, market_cap=1e9)
    rich = dataclasses.replace(base, insider_distinct_buyers=4,
                               insider_role_weighted_buy_value=8e5)
    c = _conv_cfg()
    assert insider_score(rich, c["thresholds"], c) > insider_score(base, c["thresholds"], c)


def test_planned_sell_softens_flow():
    # The net/mktcap ratio stays INSIDE the unclamped band: -3e6/1e10 = -3e-4 and the
    # softened -1.5e6/1e10, so the discount is observable.
    undetected = StockMetrics(ticker="X", insider_sentiment=0.0, insider_net_6m=-3e6,
                              market_cap=1e10, insider_planned_sell_value=0.0)
    detected = dataclasses.replace(undetected, insider_planned_sell_value=3e6)
    c = _conv_cfg()
    assert insider_score(detected, c["thresholds"], c) > \
        insider_score(undetected, c["thresholds"], c)


@pytest.mark.parametrize("base_kw,conv_kw", [
    # heavy selling, no buys
    (dict(insider_net_6m=-5e7, market_cap=3e12),
     dict(insider_distinct_buyers=0, insider_role_weighted_buy_value=0.0)),
    # a lone small insider BUY
    (dict(insider_net_6m=2e5, market_cap=4e10),
     dict(insider_distinct_buyers=1, insider_role_weighted_buy_value=2e5)),
])
def test_conviction_is_one_directional_never_penalizes(base_kw, conv_kw):
    base = StockMetrics(ticker="X", insider_sentiment=0.0, **base_kw)
    found = dataclasses.replace(base, insider_planned_sell_value=0.0, **conv_kw)
    c = _conv_cfg()
    assert insider_score(found, c["thresholds"], c) >= insider_score(base, _t()) - 1e-9


# --- backtest-only axes -----------------------------------------------------------
# (scorer, metric, band, value at the band's 0 end, value at its 100 end). The band
# order carries the sign: an inverted band lists the high value first.

BAND_AXES = [
    ("share_count_score", "share_count_cagr", [0.04, -0.04], 0.04, -0.04),
    ("asset_growth_score", "asset_growth", [0.30, -0.10], 0.30, -0.10),
    ("accruals_score", "accruals", [0.15, -0.15], 0.15, -0.15),
    ("shareholder_yield_score", "shareholder_yield", [-0.02, 0.10], -0.02, 0.10),
    ("residual_momentum_score", "residual_momentum", [-1.0, 1.0], -1.0, 1.0),
    ("pct_to_52w_high_score", "pct_to_52w_high", [0.70, 1.00], 0.70, 1.00),
    ("max_daily_return_score", "max_daily_return", [0.15, 0.02], 0.15, 0.02),
    ("vol_scaled_momentum_score", "vol_scaled_momentum", [0.0, 2.0], 0.0, 2.0),
    ("price_vs_200dma_score", "price_vs_200dma", [-0.10, 0.30], -0.10, 0.30),
    ("rel_strength_6m_score", "rel_strength_6m", [-0.15, 0.25], -0.15, 0.25),
    ("net_debt_to_ebitda_score", "net_debt_to_ebitda", [4.0, 0.0], 4.0, 0.0),
    ("ebit_ev_yield_score", "ebit_ev_yield", [0.04, 0.12], 0.04, 0.12),
]


@pytest.mark.parametrize("scorer,field,band,zero_at,hundred_at", BAND_AXES,
                         ids=[a[0] for a in BAND_AXES])
def test_band_axis_maps_its_band_and_abstains_without_band_or_metric(
        scorer, field, band, zero_at, hundred_at):
    fn, t = getattr(scoring, scorer), {field: band}
    assert fn(StockMetrics(ticker="A", **{field: zero_at}), t) == 0.0
    assert fn(StockMetrics(ticker="A", **{field: hundred_at}), t) == 100.0
    assert fn(StockMetrics(ticker="A", **{field: hundred_at}), {}) is None
    assert fn(StockMetrics(ticker="A"), t) is None


def test_max_daily_return_score_is_monotonically_decreasing():
    # Inverted band, interpolated inside it: a bigger lottery spike scores lower.
    t = {"max_daily_return": [0.15, 0.02]}
    s_hi = scoring.max_daily_return_score(StockMetrics(ticker="A", max_daily_return=0.10), t)
    s_lo = scoring.max_daily_return_score(StockMetrics(ticker="B", max_daily_return=0.05), t)
    assert 0.0 < s_hi < s_lo < 100.0


_VALUE_T = {"ebit_ev_yield": [0.04, 0.12], "fcf_yield": [0.0, 0.08],
            "pe_vs_history": [-0.3, 0.3], "upside_to_target": [0.0, 0.4], "peg": [3.0, 0.5]}


def test_value_single_leg_axes():
    assert value_fcf_yield_score(StockMetrics(ticker="X", fcf_yield=0.08), _VALUE_T) == 100.0
    # pe_vs_history = 13/10 - 1 = 0.3 -> band top -> 100
    m = StockMetrics(ticker="X", pe_ttm=10.0, pe_median_5y=13.0)
    assert value_pe_vs_history_score(m, _VALUE_T) == 100.0


def test_value_plus_evebit_folds_leg_into_average():
    # fcf_yield 0.04 -> 50; ebit_ev_yield 0.08 -> 50
    m = StockMetrics(ticker="X", fcf_yield=0.04, ebit_ev_yield=0.08)
    assert value_plus_evebit_score(m, _VALUE_T) == pytest.approx(50.0)
    m = StockMetrics(ticker="X", fcf_yield=0.04, ebit_ev_yield=0.12)
    assert value_plus_evebit_score(m, _VALUE_T) == pytest.approx(75.0)
    # Without the leg (or its band) it equals value_score.
    m = StockMetrics(ticker="X", fcf_yield=0.04, ebit_ev_yield=0.12)
    no_band = {k: v for k, v in _VALUE_T.items() if k != "ebit_ev_yield"}
    assert value_plus_evebit_score(m, no_band) == value_score(m, no_band)
    m = StockMetrics(ticker="X", fcf_yield=0.04)
    assert value_plus_evebit_score(m, _VALUE_T) == value_score(m, _VALUE_T)

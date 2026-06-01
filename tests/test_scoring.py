from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
import yaml

from shortlist.models import StockMetrics
from shortlist.providers.mock import MockProvider
from shortlist.scoring import (
    _avg, _norm, growth_score, insider_score, moat_score, momentum_score,
    quality_score, score, value_score,
)

# A deliberately clean config: every [0, 1] band makes 0.5 normalize to exactly
# 50, so we can reason about scores by hand. Independent of the shipped
# config.yaml (which the integration test at the bottom exercises instead).
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
                "opportunity": 0.30, "insider": 0.15},
    "gates": {
        "min_market_cap": 2.0e9,
        "max_debt_to_equity": 5.0,
        "min_insider_sentiment": -0.60,
    },
}


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
    t = CONFIG["thresholds"]
    base = metrics_all_50()
    # spot roic at 0.5 (=50), but a 5y average of 1.0 (=100) should win.
    m = dataclasses.replace(base, roic=0.5, roic_5y_avg=1.0)
    # gross_margin & stability are 50; roic component now 100 -> avg 66.67.
    assert moat_score(m, t) == pytest.approx((50 + 50 + 100) / 3)


def test_insider_net_flow_scaled_by_market_cap():
    t = CONFIG["thresholds"]
    base = metrics_all_50()
    # +$5M net buying at a $10B cap: ratio 0.0005 -> top of [-0.001, 0.001] band
    # is 0.001, so 0.0005 normalizes to 75; sentiment midpoint stays 50 -> 62.5.
    m = dataclasses.replace(base, insider_net_6m=5e6, market_cap=10e9, insider_sentiment=0.0)
    assert insider_score(m, t) == pytest.approx((50 + 75) / 2)


# --- growth ---------------------------------------------------------------

def test_growth_score_averages_present_legs_and_skips_none():
    t = CONFIG["thresholds"]
    # Only revenue_cagr (=> 50) and persistence (=> 100) present; fcf/eps None.
    m = StockMetrics(ticker="T", revenue_cagr=0.5, revenue_growth_persistence=1.0)
    assert growth_score(m, t) == pytest.approx((50 + 100) / 2)


def test_growth_score_none_when_no_legs_present():
    assert growth_score(StockMetrics(ticker="T"), CONFIG["thresholds"]) is None


def test_growth_weight_redistributed_when_axis_absent():
    # Quality present, growth (and all else) absent -> composite == quality alone,
    # i.e. growth's weight is dropped from the denominator, not scored as zero.
    m = StockMetrics(ticker="T", market_cap=10e9,
                     roe=0.5, net_margin=0.5, interest_coverage=5.0, debt_to_equity=1.0)
    card = score(m, CONFIG)
    assert card.growth is None
    assert card.composite == 50.0


# --- opportunity = max(momentum, value) -----------------------------------

def test_opportunity_takes_the_stronger_axis():
    base = metrics_all_50()
    # momentum maxed (all 1.0 -> 100), value floored (upside/fcf/pe all 0).
    m = dataclasses.replace(
        base,
        price_vs_200dma=1.0, rel_strength_6m=1.0, eps_revision=1.0,
        target_median=100.0,        # upside_to_target = 0
        fcf_yield=0.0,
        pe_median_5y=10.0,          # pe_vs_history = 0
    )
    card = score(m, CONFIG)
    assert card.momentum == 100.0
    assert card.value == 0.0
    assert card.opportunity == 100.0   # max, not the average (which would be 50)


# --- composite renormalization --------------------------------------------

def test_missing_components_are_excluded_and_weights_renormalized():
    # Only quality is computable; everything else is None.
    m = StockMetrics(
        ticker="T", market_cap=10e9,
        roe=0.5, net_margin=0.5, interest_coverage=5.0, debt_to_equity=1.0,
    )
    card = score(m, CONFIG)
    assert card.quality == 50.0
    assert card.moat is None
    assert card.growth is None
    assert card.momentum is None
    assert card.value is None
    assert card.opportunity is None
    assert card.insider is None
    # Composite == quality alone (denominator collapses to quality's weight).
    assert card.composite == 50.0


def test_composite_is_zero_when_nothing_is_scorable():
    card = score(StockMetrics(ticker="EMPTY"), CONFIG)
    assert card.composite == 0.0


# --- gates ----------------------------------------------------------------

@pytest.mark.parametrize("field,value,expected_gate", [
    ("fcf_positive", False, "negative_fcf"),
    ("market_cap", 1.0e9, "below_min_mktcap"),
    ("debt_to_equity", 6.0, "over_leveraged"),
    ("insider_sentiment", -0.7, "heavy_insider_selling"),
])
def test_each_gate_trips_independently(field, value, expected_gate):
    m = dataclasses.replace(metrics_all_50(), **{field: value})
    card = score(m, CONFIG)
    assert expected_gate in card.gates
    assert not card.passed


def test_clean_metrics_trip_no_gates():
    card = score(metrics_all_50(), CONFIG)
    assert card.gates == []
    assert card.passed


def test_peg_contributes_to_value_score():
    # peg band [3.0, 0.5]: 0.5 → 100 (cheap growth), 3.0 → 0 (expensive growth).
    # Lower PEG = better value, so the band is inverted like debt_to_equity.
    t = {
        "peg": [3.0, 0.5],
        "upside_to_target": [0.0, 1.0],
        "fcf_yield": [0.0, 1.0],
        "pe_vs_history": [0.0, 1.0],
    }
    # Only peg is set; other value inputs None → peg drives the score alone.
    assert value_score(StockMetrics(ticker="T", peg=0.5), t) == 100.0
    assert value_score(StockMetrics(ticker="T", peg=3.0), t) == 0.0
    # Midpoint PEG midway through the inverted band.
    assert value_score(StockMetrics(ticker="T", peg=1.75), t) == pytest.approx(50.0)


# --- integration: real config.yaml + mock provider -----------------------

def test_score_runs_against_shipped_config_and_mock_data():
    config_path = Path(__file__).parent.parent / "config.yaml"
    config = yaml.safe_load(config_path.read_text())

    card = score(MockProvider().fetch("GOOGL"), config)
    assert card.ticker == "GOOGL"
    assert 0.0 <= card.composite <= 100.0
    # GOOGL's sample has strong quality/moat inputs; sanity-check they populate.
    assert card.quality is not None and card.moat is not None
    assert card.opportunity == max(
        x for x in (card.momentum, card.value) if x is not None
    )

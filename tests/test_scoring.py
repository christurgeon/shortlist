from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
import yaml

from shortlist.models import StockMetrics
from shortlist.providers.mock import MockProvider
from shortlist.scoring import (
    _avg, _norm, check_flags, growth_score, insider_score, moat_score,
    momentum_score, piotroski_score, quality_score, score, value_score,
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
                "value": 0.22, "momentum": 0.08, "insider": 0.15},
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


# --- value/momentum independent weighting ---------------------------------

def test_value_and_momentum_weighted_independently():
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
    # Display field still reports the stronger axis (max), even though the
    # composite no longer uses it.
    assert card.opportunity == 100.0
    # Composite weights value (0.22) far above momentum (0.08): the floored value
    # axis drags the composite well below the old max()-driven 65.
    # num = 50*0.20 + 50*0.20 + 50*0.15 + 100*0.08 + 0*0.22 + 50*0.15 = 43.0; den = 1.00
    assert card.composite == 43.0


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


# --- check_flags: soft, non-disqualifying advisories ---------------------

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


def test_check_flags_each_clause_suppresses():
    assert check_flags(_crowded_metrics(short_pct_outstanding=0.05), FLAGS_CFG) == []
    assert check_flags(_crowded_metrics(days_to_cover=3.0), FLAGS_CFG) == []
    assert check_flags(_crowded_metrics(short_interest_rising=False), FLAGS_CFG) == []
    assert check_flags(_crowded_metrics(short_data_age_days=40), FLAGS_CFG) == []   # stale


def test_check_flags_none_inputs_are_noop():
    assert check_flags(StockMetrics(ticker="X"), FLAGS_CFG) == []
    assert check_flags(_crowded_metrics(), {}) == []          # no flags config -> nothing


VT_CFG = {"value_trap": {"min_value_score": 60, "max_quality_score": 40,
                         "max_growth_score": 40}}


def _value_trap_metrics(**kw):
    # value high (~80 on the [0,1] CONFIG bands), quality low (~20). growth stays
    # 50 (not weak), so the flag must fire via the quality clause.
    base = metrics_all_50()
    m = dataclasses.replace(
        base,
        # value legs high: upside 0.8, fcf_yield 0.8, pe_vs_history 0.8, peg 0.4(->80)
        price=100.0, target_median=180.0, fcf_yield=0.8,
        pe_ttm=10.0, pe_median_5y=18.0, peg=0.4,
        # quality legs low: roe/net_margin 0.2, interest_coverage 2(->20), d/e 1.6(->20)
        roe=0.2, net_margin=0.2, interest_coverage=2.0, debt_to_equity=1.6,
    )
    return dataclasses.replace(m, **kw)


def test_value_trap_fires_when_cheap_and_weak():
    cfg = dict(CONFIG); cfg["flags"] = VT_CFG
    card = score(_value_trap_metrics(), cfg)
    assert card.value >= 60
    assert card.quality < 40
    assert "value_trap" in card.flags
    assert card.passed is True            # advisory only — never disqualifies


def test_value_trap_silent_when_fundamentals_strong():
    cfg = dict(CONFIG); cfg["flags"] = VT_CFG
    m = _value_trap_metrics(roe=0.9, net_margin=0.9, interest_coverage=9.0,
                            debt_to_equity=0.2)
    card = score(m, cfg)
    assert card.value >= 60
    assert card.quality >= 40
    assert "value_trap" not in card.flags


def test_value_trap_silent_when_value_missing():
    cfg = dict(CONFIG); cfg["flags"] = VT_CFG
    m = StockMetrics(ticker="T", market_cap=10e9, roe=0.2, net_margin=0.2)
    card = score(m, cfg)
    assert card.value is None
    assert "value_trap" not in card.flags


def test_value_trap_noop_without_config_block():
    card = score(_value_trap_metrics(), CONFIG)   # CONFIG has no flags block
    assert "value_trap" not in card.flags


def test_score_carries_flags_and_passed_unaffected():
    m = _crowded_metrics(market_cap=5.0e9)
    cfg = dict(CONFIG)
    cfg["flags"] = FLAGS_CFG
    card = score(m, cfg)
    assert "crowded_short" in card.flags
    assert card.passed is True                                # advisory only


# --- Risk sub-score (7th axis) -------------------------------------------

import copy
import dataclasses

from shortlist.scoring import risk_score

_RISK_T = {"realized_vol": [0.45, 0.15], "max_drawdown": [-0.50, -0.10]}


def _risk_config():
    c = copy.deepcopy(CONFIG)
    c["thresholds"]["realized_vol"] = [0.45, 0.15]
    c["thresholds"]["max_drawdown"] = [-0.50, -0.10]
    # six independent components + risk 0.10 (the old x0.9 risk-absent invariant
    # is retired by the value/momentum split)
    c["weights"] = {"quality": 0.18, "moat": 0.18, "growth": 0.135,
                    "value": 0.22, "momentum": 0.08, "insider": 0.135, "risk": 0.10}
    return c


def test_risk_score_direction_and_clamp():
    base = metrics_all_50()
    safe = dataclasses.replace(base, realized_vol=0.15, max_drawdown=-0.10)
    assert risk_score(safe, _RISK_T) == 100.0
    risky = dataclasses.replace(base, realized_vol=0.45, max_drawdown=-0.50)
    assert risk_score(risky, _RISK_T) == 0.0
    mid = dataclasses.replace(base, realized_vol=0.30, max_drawdown=-0.30)
    assert risk_score(mid, _RISK_T) == 50.0
    extreme = dataclasses.replace(base, realized_vol=0.05, max_drawdown=-0.80)
    assert risk_score(extreme, _RISK_T) == 50.0  # vol capped 100, dd floored 0


def test_risk_score_none_when_no_legs():
    no_risk = dataclasses.replace(metrics_all_50(), realized_vol=None, max_drawdown=None)
    assert risk_score(no_risk, _RISK_T) is None


def test_composite_shifts_when_risk_present():
    rc = _risk_config()
    safe = dataclasses.replace(metrics_all_50(), realized_vol=0.15, max_drawdown=-0.10)
    card = score(safe, rc)
    assert card.risk == 100.0
    # composite = (50*0.93 + 100*0.10) / 1.03 = 56.5/1.03 = 54.9
    assert card.composite == 54.9


def test_composite_invariant_when_risk_absent():
    rc = _risk_config()
    m = dataclasses.replace(metrics_all_50(), realized_vol=None, max_drawdown=None)
    assert score(m, rc).composite == score(m, CONFIG).composite
    assert score(m, rc).composite == 50.0


def test_confidence_invariant_unknown_bucket_and_no_risk_abstention():
    rc = _risk_config()
    m = dataclasses.replace(metrics_all_50(), realized_vol=None, max_drawdown=None)
    a, b = score(m, CONFIG), score(m, rc)
    assert a.confidence == b.confidence
    assert a.scored == b.scored
    assert a.passed == b.passed
    assert all(x.get("field") != "risk" for x in b.abstentions)


def test_no_keyerror_on_config_without_risk():
    m = dataclasses.replace(metrics_all_50(), realized_vol=0.20, max_drawdown=-0.15)
    card = score(m, CONFIG)  # CONFIG has no risk keys
    assert card.risk is None


def test_shipped_config_activates_risk():
    cfg = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text())
    w = cfg["weights"]
    assert w["risk"] == 0.10
    # The value/momentum split intentionally lifts the price/value bloc to 0.30
    # (value 0.22 + momentum 0.08) vs the old opportunity 0.27, so the weights no
    # longer sum to 1.0. That is cosmetic: the composite is a normalized weighted
    # average, so only ratios matter (spec 2026-06-02 §3.1). Pin the new schema.
    assert "opportunity" not in w
    assert (w["value"], w["momentum"]) == (0.22, 0.08)
    assert abs(sum(w.values()) - 1.03) < 1e-9
    t = cfg["thresholds"]
    assert t["realized_vol"] == [0.45, 0.15]
    assert t["max_drawdown"] == [-0.50, -0.10]
    m = dataclasses.replace(metrics_all_50(), realized_vol=0.15, max_drawdown=-0.10)
    assert score(m, cfg).risk == 100.0


def test_csv_has_aligned_risk_column(tmp_path):
    import csv
    from shortlist.screen import _write_csv
    rc = _risk_config()
    m = dataclasses.replace(metrics_all_50(), realized_vol=0.15, max_drawdown=-0.10, sic=None)
    card = score(m, rc)
    path = tmp_path / "out.csv"
    _write_csv([card], str(path))
    rows = list(csv.reader(path.open()))
    header, row = rows[0], rows[1]
    assert "risk" in header
    assert row[header.index("risk")] == str(card.risk)


def test_thin_flag_set_below_threshold():
    import copy, dataclasses
    rc = copy.deepcopy(CONFIG)
    rc["ranking"] = {"thin_below": 0.5}
    # momentum-only name -> confidence well below 0.5 -> thin
    m = StockMetrics(ticker="MOM", price_vs_200dma=0.2, rel_strength_6m=0.2,
                     eps_revision=0.05)
    card = score(m, rc)
    assert 0.0 < card.confidence < 0.5
    assert card.thin is True


def test_thin_flag_false_above_threshold():
    rc = {**CONFIG, "ranking": {"thin_below": 0.5}}
    card = score(metrics_all_50(), rc)   # fully covered -> confidence 1.0
    assert card.thin is False


def test_thin_noop_when_ranking_absent():
    # CONFIG has no `ranking` block -> thin always False, no KeyError
    m = StockMetrics(ticker="MOM", price_vs_200dma=0.2, rel_strength_6m=0.2,
                     eps_revision=0.05)
    assert score(m, CONFIG).thin is False


def test_shipped_config_has_ranking_thin_below():
    cfg = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text())
    assert cfg["ranking"]["thin_below"] == 0.40


# --- C2 regression: value-tilt must not drop gated financials below `scored` ---

def _shipped_config():
    config_path = Path(__file__).parent.parent / "config.yaml"
    return yaml.safe_load(config_path.read_text())


def test_value_gated_financials_still_scored_worst_case():
    # Financials (SIC 6020): moat masked, and quality/growth/value all
    # gated/absent, so only momentum + insider are present.
    # appl_w = quality .18 + growth .135 + value .22 + momentum .08 + insider .135
    #        = 0.75 (moat .18 masked); pres_w = .08 + .135 = 0.215
    # confidence = 0.215 / 0.75 = 0.287 -> must clear min_scored_weight 0.25.
    cfg = _shipped_config()
    m = StockMetrics(
        ticker="BANK", sic="6020", market_cap=10e9,
        price_vs_200dma=0.1, rel_strength_6m=0.1, eps_revision=0.02,   # momentum present
        insider_sentiment=0.0, insider_net_6m=0.0,                     # insider present, clean
        # quality/growth/value legs all None -> absent
    )
    card = score(m, cfg)
    assert card.sic_bucket == "financials"
    assert card.quality is None and card.growth is None and card.value is None
    assert card.momentum is not None and card.insider is not None
    assert card.confidence == pytest.approx(0.287, abs=0.005)
    assert card.scored is True
    assert card.passed is True


def test_insider_only_financials_not_scored():
    # Only insider present -> confidence 0.135 / 0.75 = 0.18 < 0.25 -> not scored.
    cfg = _shipped_config()
    m = StockMetrics(
        ticker="BANK2", sic="6020", market_cap=10e9,
        insider_sentiment=0.0, insider_net_6m=0.0,
    )
    card = score(m, cfg)
    assert card.sic_bucket == "financials"
    assert card.confidence == pytest.approx(0.18, abs=0.005)
    assert card.scored is False
    assert card.passed is False


def test_csv_has_confidence_column_after_scored(tmp_path):
    import csv
    from shortlist.screen import _write_csv
    from shortlist.models import ScoreCard
    card = ScoreCard(ticker="T", composite=60.0, quality=None, moat=None, growth=None,
                     momentum=None, value=None, opportunity=None, insider=None,
                     confidence=0.42, scored=True)
    path = tmp_path / "out.csv"
    _write_csv([card], str(path))
    rows = list(csv.reader(path.open()))
    header, row = rows[0], rows[1]
    assert "confidence" in header
    assert header.index("confidence") == header.index("scored") + 1
    assert row[header.index("confidence")] == str(card.confidence)


# --- insider_score v2: conviction leg (config-gated, no-op default) -------

_CFG = yaml.safe_load((Path(__file__).resolve().parents[1] / "config.yaml").read_text())


def _t():
    return _CFG["thresholds"]


def _conv_cfg():
    # a config WITH the conviction block + bands enabled
    import copy
    c = copy.deepcopy(_CFG)
    c["thresholds"]["insider_cluster"] = [1, 4]
    c["thresholds"]["insider_role_buy_ratio"] = [0.0, 0.001]
    c["insider"] = {"conviction": {
        "enabled": True, "min_cluster_buy_value": 1000, "planned_sell_discount": 0.5,
        "role_weights": {"c_suite": 1.5, "officer": 1.2, "director": 1.0, "ten_pct": 0.5, "unknown": 1.0},
    }}
    return c


def test_insider_v2_disabled_identity():
    m = StockMetrics(ticker="X", insider_sentiment=0.2, insider_net_6m=1e6, market_cap=1e10,
                     insider_distinct_buyers=4, insider_role_weighted_buy_value=5e6,
                     insider_planned_sell_value=2e5)
    # config WITHOUT insider.conviction -> exactly legacy two-arg behavior
    assert insider_score(m, _t(), _CFG) == insider_score(m, _t())


def test_insider_v2_enabled_no_form4_identity():
    m = StockMetrics(ticker="X", insider_sentiment=0.2, insider_net_6m=1e6, market_cap=1e10)
    c = _conv_cfg()
    assert insider_score(m, c["thresholds"], c) == insider_score(m, _t())


def test_insider_cluster_and_csuite_raise_score():
    base = StockMetrics(ticker="X", insider_sentiment=0.0, insider_net_6m=0.0, market_cap=1e9)
    rich = StockMetrics(ticker="X", insider_sentiment=0.0, insider_net_6m=0.0, market_cap=1e9,
                        insider_distinct_buyers=4, insider_role_weighted_buy_value=8e5)
    c = _conv_cfg()
    assert insider_score(rich, c["thresholds"], c) > insider_score(base, c["thresholds"], c)


def test_planned_sell_softens_flow():
    # Numbers chosen so the net/mktcap ratio lands INSIDE the unclamped band
    # [-0.0005, 0.0005]: -3e6/1e10 = -3e-4; softened -1.5e6/1e10 = -1.5e-4. Both
    # in-band, so the discount is observable (a -5e6/1e9 = -5e-3 would clamp both to 0).
    detected = StockMetrics(ticker="X", insider_sentiment=0.0, insider_net_6m=-3e6, market_cap=1e10,
                            insider_planned_sell_value=3e6)
    undetected = StockMetrics(ticker="X", insider_sentiment=0.0, insider_net_6m=-3e6, market_cap=1e10,
                              insider_planned_sell_value=0.0)
    c = _conv_cfg()
    assert insider_score(detected, c["thresholds"], c) > insider_score(undetected, c["thresholds"], c)


def test_conviction_is_one_directional_never_penalizes():
    # Conviction may only RAISE the insider score, never drag it below the base
    # (sentiment+flow) view. A found name with heavy selling / no buys, and a name
    # with a single small insider BUY, must both score >= their base — never lower.
    c = _conv_cfg()

    sells = StockMetrics(ticker="X", insider_sentiment=0.0, insider_net_6m=-5e7, market_cap=3e12,
                         insider_distinct_buyers=0, insider_role_weighted_buy_value=0.0,
                         insider_planned_sell_value=0.0)
    sells_base = insider_score(  # base = legacy/off view (no conviction block)
        StockMetrics(ticker="X", insider_sentiment=0.0, insider_net_6m=-5e7, market_cap=3e12), _t())
    assert insider_score(sells, c["thresholds"], c) >= sells_base - 1e-9

    # a lone small insider BUY must NOT drop the score below its base view
    buy = StockMetrics(ticker="Y", insider_sentiment=0.0, insider_net_6m=2e5, market_cap=4e10,
                       insider_distinct_buyers=1, insider_role_weighted_buy_value=2e5,
                       insider_planned_sell_value=0.0)
    buy_base = insider_score(
        StockMetrics(ticker="Y", insider_sentiment=0.0, insider_net_6m=2e5, market_cap=4e10), _t())
    assert insider_score(buy, c["thresholds"], c) >= buy_base - 1e-9


def test_insider_cluster_buy_flag_fires_at_threshold():
    f = {"insider_cluster_buy": {"min_distinct": 3}, "planned_sale": {"min_value": 1}}
    assert "insider_cluster_buy" in check_flags(
        StockMetrics(ticker="X", insider_distinct_buyers=3), f)
    assert "insider_cluster_buy" not in check_flags(
        StockMetrics(ticker="X", insider_distinct_buyers=2), f)


def test_planned_sale_flag_fires_on_detected_dollars():
    f = {"insider_cluster_buy": {"min_distinct": 3}, "planned_sale": {"min_value": 1}}
    assert "planned_sale" in check_flags(
        StockMetrics(ticker="X", insider_planned_sell_value=100.0), f)
    assert "planned_sale" not in check_flags(
        StockMetrics(ticker="X", insider_planned_sell_value=0.0), f)


def test_insider_flags_absent_when_unconfigured():
    assert check_flags(StockMetrics(ticker="X", insider_distinct_buyers=5), {}) == []


# --- Task 8: gate-untouched regression -----------------------------------

def test_gate_untouched_by_10b5_1_when_conviction_off():
    # A name whose MSPR trips heavy_insider_selling, WITH a detected planned sale set.
    # With conviction OFF (no insider.conviction block), the gate must still trip and
    # the composite/passed must be identical to not setting the planned-sale field.
    m_flag = StockMetrics(ticker="X", insider_sentiment=-0.9, market_cap=5e9,
                          insider_planned_sell_value=9e6)
    m_plain = StockMetrics(ticker="X", insider_sentiment=-0.9, market_cap=5e9)
    a = score(m_flag, _CFG)
    b = score(m_plain, _CFG)
    assert "heavy_insider_selling" in a.gates          # gate still trips
    assert a.composite == b.composite                   # planned-sale field changes nothing
    assert a.passed == b.passed and a.gates == b.gates


# --- piotroski_score + value_trap refinement (Task 6) ---------------------

# value_trap refinement config: existing thresholds + the piotroski sub-block.
VT_PIO_CFG = {"value_trap": {"min_value_score": 60, "max_quality_score": 40,
                             "max_growth_score": 40,
                             "piotroski": {"suppress_at": 0.83, "confirm_at": 0.40,
                                           "min_legs": 4}}}

def _cfg_with(flags):
    cfg = dict(CONFIG); cfg["flags"] = flags
    # ensure the band + mask exist for piotroski_score/_piotroski_raw_fraction
    cfg["thresholds"] = {**CONFIG["thresholds"], "piotroski_f": [0.34, 1.00]}
    cfg["sectors"] = {"buckets": [], "masked_legs": ["piotroski_f"]}
    return cfg

def test_piotroski_score_maps_fraction_through_band():
    m = StockMetrics(ticker="T", piotroski_f=5, piotroski_f_legs=6)
    # fraction 0.833 over band [0.34, 1.00] -> ~74.7
    s = piotroski_score(m, {"piotroski_f": [0.34, 1.00]})
    assert s == pytest.approx((0.8333333 - 0.34) / (1.00 - 0.34) * 100, abs=0.5)

def test_piotroski_score_none_below_min_legs():
    m = StockMetrics(ticker="T", piotroski_f=2, piotroski_f_legs=3)
    assert piotroski_score(m, {"piotroski_f": [0.34, 1.00]}) is None

def test_value_trap_suppressed_when_fundamentals_improving():
    # cheap + weak quality would normally fire value_trap; high F-fraction suppresses it.
    m = _value_trap_metrics(piotroski_f=6, piotroski_f_legs=6)   # fraction 1.0 >= 0.83
    card = score(m, _cfg_with({"value_trap": VT_PIO_CFG["value_trap"]}))
    assert card.value >= 60 and card.quality < 40
    assert "value_trap" not in card.flags

def test_value_trap_confirmed_when_fundamentals_deteriorating():
    # cheap + STRONG quality/growth (base would NOT fire) but low F-fraction confirms.
    m = _value_trap_metrics(roe=0.9, net_margin=0.9, interest_coverage=9.0,
                            debt_to_equity=0.2, piotroski_f=1, piotroski_f_legs=6)
    card = score(m, _cfg_with({"value_trap": VT_PIO_CFG["value_trap"]}))
    assert card.value >= 60 and card.quality >= 40
    assert "value_trap" in card.flags

def test_value_trap_masked_bucket_does_not_use_piotroski():
    # financials bucket -> piotroski_f masked -> fraction None -> legacy base logic only.
    m = _value_trap_metrics(sic="6020", piotroski_f=6, piotroski_f_legs=6)
    cfg = _cfg_with({"value_trap": VT_PIO_CFG["value_trap"]})
    cfg["sectors"] = {"buckets": [{"name": "financials", "sic_ranges": [[6020, 6099]]}],
                      "masked_legs": ["piotroski_f"]}
    card = score(m, cfg)
    # base (cheap + weak quality) still fires; suppression does NOT apply (masked)
    assert "value_trap" in card.flags

def test_value_trap_backcompat_no_piotroski_block():
    # VT_CFG has NO piotroski sub-block -> byte-identical legacy behavior even if
    # piotroski fields are present on the metrics.
    cfg = dict(CONFIG); cfg["flags"] = VT_CFG
    fires = score(_value_trap_metrics(piotroski_f=6, piotroski_f_legs=6), cfg)
    assert "value_trap" in fires.flags          # high-F name STILL flagged (no suppression)

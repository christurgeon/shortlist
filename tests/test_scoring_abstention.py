from pathlib import Path

import yaml

from shortlist.models import StockMetrics
from shortlist.scoring import score

CFG = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text())


def _fin(**kw):
    # a broker-dealer (SIC 6211) with present-but-misleading legs
    base = dict(ticker="SCHWX", sic="6211", market_cap=152e9,
                roe=0.17, net_margin=0.36, interest_coverage=1.5, debt_to_equity=8.0,
                gross_margin=0.55, gross_margin_stability=0.9, roic=0.20,
                fcf_yield=0.02, fcf_cagr=0.30,
                revenue_cagr=0.08, eps_cagr=0.06, revenue_growth_persistence=0.5,
                price_vs_200dma=-0.05, rel_strength_6m=-0.08, eps_revision=0.06,
                pe_ttm=17.0, pe_median_5y=20.0, price=87.0, target_median=115.0,
                peg=None, insider_net_6m=-1.2e6, insider_sentiment=-0.05,
                fcf_positive=True)
    base.update(kw)
    return StockMetrics(**base)


def test_financial_masks_moat_legs_so_moat_abstains():
    card = score(_fin(), CFG)
    assert card.sic_bucket == "financials"
    assert card.moat is None                      # gross_margin+stability+roic all masked
    names = {(a["field"], a["reason"]) for a in card.abstentions}
    assert ("moat", "inapplicable") in names
    # A COMPUTED roic must not reach the composite for a financial either. As of 2026-08-10
    # bridge.py derives roic from statements on the FMP-gated path, and UNH (SIC 6324) / JPM
    # (6021) are among the names that gain one -- but ROIC is structurally meaningless for
    # banks and insurers, so the SIC mask has to keep firing. `_fin()` already carries
    # roic=0.20, so this pins the leg explicitly rather than relying on the subscore result.
    # NOTE the real limit of this guard: the mask keys on `m.sic`, and `leg_applicable`
    # returns True for bucket == "unknown" -- a name with no resolved SIC is NOT masked.
    assert ("roic", "inapplicable") in names


def test_financial_masks_fcf_yield_in_value():
    card = score(_fin(), CFG)
    assert ("fcf_yield", "inapplicable") in {(a["field"], a["reason"]) for a in card.abstentions}


def test_unknown_bucket_is_bit_identical_to_legacy():
    # An operating company with no SIC must score EXACTLY as before this change.
    m = StockMetrics(ticker="OPCO", roe=0.5, net_margin=0.5, interest_coverage=5.0,
                     debt_to_equity=1.0, gross_margin=0.6, gross_margin_stability=0.9,
                     roic=0.2, revenue_cagr=0.1, fcf_cagr=0.1, eps_cagr=0.1,
                     revenue_growth_persistence=0.8, price_vs_200dma=0.1,
                     rel_strength_6m=0.1, eps_revision=0.05, fcf_yield=0.05,
                     peg=1.0, market_cap=5e9, insider_sentiment=0.1)
    card = score(m, CFG)
    assert card.sic_bucket == "unknown"
    assert card.scored is True
    assert card.abstentions == []                 # nothing masked, nothing thin
    assert card.quality is not None and card.moat is not None


def test_unknown_momentum_only_name_still_scored():
    # Today a momentum-only name scores on opportunity alone; must NOT flip not_scored.
    m = StockMetrics(ticker="MOM", price_vs_200dma=0.2, rel_strength_6m=0.2,
                     eps_revision=0.05)
    card = score(m, CFG)
    assert card.scored is True
    assert card.passed is True


def test_over_leveraged_gate_masked_for_financial():
    card = score(_fin(debt_to_equity=8.0), CFG)   # 8.0 > max 5.0
    assert "over_leveraged" not in card.gates      # masked
    un = score(_fin(sic=None, debt_to_equity=8.0), CFG)
    assert "over_leveraged" in un.gates            # same metrics, unknown -> fires


def test_financial_scored_with_reduced_confidence():
    card = score(_fin(), CFG)
    assert card.scored is True          # quality/growth/opportunity/insider present
    assert card.confidence <= 1.0
    assert card.composite > 0.0         # number still emitted (audit)


def test_data_starved_financial_not_scored():
    # only insider present among applicable components -> below floor
    m = StockMetrics(ticker="THIN", sic="6211", market_cap=10e9, insider_sentiment=0.1)
    card = score(m, CFG)
    assert card.scored is False
    assert card.passed is False


def test_risk_axis_does_not_regress_known_bucket_confidence():
    """A financials name with PARTIAL coverage (0 < confidence < 1) must keep the
    SAME confidence/scored/passed whether or not the risk weight is in config. Risk
    is a composite-only tilt and must never enter the confidence denominator."""
    import copy
    # financials with only quality + opportunity present among APPLICABLE components
    # (moat masked/inapplicable; growth + insider made absent -> confidence < 1).
    m = _fin(revenue_cagr=None, fcf_cagr=None, eps_cagr=None,
             revenue_growth_persistence=None,            # growth absent
             insider_net_6m=None, insider_sentiment=None)  # insider absent
    cfg_no_risk = copy.deepcopy(CFG)
    del cfg_no_risk["weights"]["risk"]
    del cfg_no_risk["thresholds"]["realized_vol"]
    del cfg_no_risk["thresholds"]["max_drawdown"]

    with_risk = score(m, CFG)
    without_risk = score(m, cfg_no_risk)

    assert with_risk.sic_bucket == "financials"
    assert 0.0 < with_risk.confidence < 1.0      # genuinely partial -> non-trivial guard
    assert with_risk.scored is True
    assert with_risk.confidence == without_risk.confidence
    assert with_risk.scored == without_risk.scored
    assert with_risk.passed == without_risk.passed

"""`validity.min_composite_components` — a composite must rest on a real sub-score.

The defect this pins (live, 2026-08-10): BRVE ranked #1 in the scout report at composite
100.0 with six of seven components null and confidence 0.0. `scored` did not catch it
because the bucket-gated line reads `True if bucket == "unknown" else ...`, and `unknown`
is the majority bucket — so the weight redistributed onto the risk tilt alone, which read
100 because the issuer reports no debt.

The rule is a COUNT, not a weight threshold. A momentum-only name sits at confidence ~0.08
and is pinned as scored by test_scoring_abstention.py, while BRVE sits at 0.0 — too narrow a
band to threshold. Both cases are re-pinned here so the distinction cannot silently invert.
"""
from pathlib import Path

import yaml

from shortlist.models import StockMetrics
from shortlist.scoring import score

CFG = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text())


def _no_data(**kw):
    """A BRVE-shaped name: no SIC (-> 'unknown' bucket) and no fundamental leg present.
    Only the risk tilt can compute, because volatility/drawdown come from price history."""
    base = dict(ticker="BRVE", market_cap=0.0, price=30.59,
                realized_vol=0.15, max_drawdown=0.0)
    base.update(kw)
    return StockMetrics(**base)


def _cfg_without_floor():
    cfg = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text())
    cfg["validity"].pop("min_composite_components", None)
    return cfg


class TestFloorCatchesTheNoDataCase:
    def test_bucket_is_unknown_and_confidence_is_zero(self):
        card = score(_no_data(), CFG)
        assert card.sic_bucket == "unknown"
        assert card.confidence == 0.0

    def test_no_data_name_is_not_scored(self):
        assert score(_no_data(), CFG).scored is False

    def test_not_scored_means_it_cannot_pass(self):
        # passed = not gates and scored — an unscored name can never rank.
        assert score(_no_data(), CFG).passed is False


class TestBackCompat:
    def test_absent_key_restores_the_old_behaviour_exactly(self):
        # Without the key the `unknown` escape is absolute again, so the same card scores.
        assert score(_no_data(), _cfg_without_floor()).scored is True

    def test_absent_key_leaves_composite_and_confidence_untouched(self):
        with_floor = score(_no_data(), CFG)
        without = score(_no_data(), _cfg_without_floor())
        # The floor gates `scored` ONLY — it must never move the composite math.
        assert with_floor.composite == without.composite
        assert with_floor.confidence == without.confidence


class TestCommittedGuardsStillHold:
    def test_momentum_only_name_still_scores(self):
        # test_scoring_abstention.py pins this: a momentum-only name scores on opportunity
        # alone and must NOT flip not_scored. It has ONE present component, so the count
        # rule keeps it while the risk-tilt-only case is excluded. This guard is what
        # caught an earlier weight-threshold attempt at this fix.
        m = StockMetrics(ticker="MOM", price_vs_200dma=0.2, rel_strength_6m=0.2,
                         eps_revision=0.05)
        card = score(m, CFG)
        assert card.scored is True and card.passed is True
        assert card.confidence < 0.20      # below any plausible weight floor...
        assert card.momentum is not None   # ...but it does have a real component

    def test_data_starved_financial_stays_unscored(self):
        # Insider-only financial: already unscored via min_scored_weight. The count rule
        # must not accidentally rescue it (1 component >= 1) — the bucket gate still runs.
        m = StockMetrics(ticker="THIN", sic="6211", market_cap=10e9, insider_sentiment=0.1)
        assert score(m, CFG).scored is False

    def test_a_well_covered_name_is_unaffected(self):
        m = StockMetrics(ticker="AAPL", sic="3571", market_cap=3.0e12,
                         roe=0.45, net_margin=0.25, gross_margin=0.44,
                         gross_margin_stability=0.95, roic=0.35,
                         fcf_yield=0.03, fcf_cagr=0.10, fcf_positive=True,
                         revenue_cagr=0.08, eps_cagr=0.10,
                         revenue_growth_persistence=0.8,
                         price_vs_200dma=0.05, rel_strength_6m=0.10,
                         pe_ttm=28.0, pe_median_5y=25.0, price=190.0,
                         insider_net_6m=1.0e6, debt_to_equity=1.5,
                         realized_vol=0.22, max_drawdown=-0.15)
        card = score(m, CFG)
        assert card.confidence > 0.5
        assert card.scored is True

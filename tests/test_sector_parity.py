"""End-to-end: SCHW golden + two-stack parity + EDGAR-absent symmetry (keyless)."""
from pathlib import Path

import yaml

from shortlist.data.bridge import snapshot_to_metrics
from shortlist.data.mockdata import SAMPLE
from shortlist.merge import merge
from shortlist.providers.mock import MockProvider
from shortlist.scoring import score

CFG = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text())


def _screener_card(ticker):
    return score(merge([MockProvider().fetch(ticker)]), CFG)


def _harness_card(ticker):
    snap = SAMPLE[ticker]["snapshot"](ticker)       # real _snap closure -> TickerSnapshot
    return score(snapshot_to_metrics(snap), CFG)


def test_schw_golden_screener():
    card = _screener_card("SCHW")
    assert card.sic_bucket == "financials"
    assert card.moat is None                        # gross_margin+stability+roic PRESENT but masked
    assert "over_leveraged" not in card.gates       # gate masked despite D/E 8.0
    assert card.composite > 0.0                     # number still emitted (audit)
    # the masked moat is reported as inapplicable, not as a coverage gap
    assert ("moat", "inapplicable") in {(a["field"], a["reason"]) for a in card.abstentions}


def test_two_stack_parity_for_schw():
    card_s = _screener_card("SCHW")
    card_h = _harness_card("SCHW")
    assert card_s.sic_bucket == card_h.sic_bucket == "financials"
    assert ("over_leveraged" in card_s.gates) == ("over_leveraged" in card_h.gates)
    assert (card_s.moat is None) == (card_h.moat is None)
    assert card_s.scored == card_h.scored


def test_financial_card_contract_is_pinned():
    """Lock SCHW's full card contract so masking can't silently regress. score() is
    pure in (metrics, config), so this also IS the genuine no-divergence proof: any
    stack that populates the same m.sic produces exactly this card."""
    from shortlist.models import StockMetrics
    m = StockMetrics(
        ticker="SCHWX", sic="6211", market_cap=152e9,
        roe=0.17, net_margin=0.36, interest_coverage=1.5, debt_to_equity=8.0,
        gross_margin=0.55, gross_margin_stability=0.9, roic=0.20,
        fcf_yield=0.02, fcf_cagr=0.30,
        revenue_cagr=0.08, eps_cagr=0.06, revenue_growth_persistence=0.5,
        price_vs_200dma=-0.05, rel_strength_6m=-0.08, eps_revision=0.06,
        pe_ttm=17.0, pe_median_5y=20.0, price=87.0, target_median=115.0,
        insider_sentiment=-0.05, fcf_positive=True,
    )
    card = score(m, CFG)
    assert card.sic_bucket == "financials"
    assert card.moat is None                       # all 3 moat legs masked -> abstains
    assert card.quality is not None                # roe + net_margin survive
    assert "over_leveraged" not in card.gates      # masked despite D/E 8.0
    masked = {a["field"] for a in card.abstentions
              if a["reason"] == "inapplicable"}
    assert {"gross_margin", "gross_margin_stability", "roic", "fcf_yield",
            "fcf_cagr", "interest_coverage", "debt_to_equity", "moat"} <= masked
    assert card.scored is True                     # 4 of 5 applicable components present


def test_edgar_absent_both_unknown():
    # Strip SIC -> unknown -> symmetric, NO masking, gate fires on D/E 8.0.
    m = merge([MockProvider().fetch("SCHW")])
    m.sic = None
    card = score(m, CFG)
    assert card.sic_bucket == "unknown"
    assert card.moat is not None                    # masked legs now contribute
    assert "over_leveraged" in card.gates           # D/E 8.0 trips when unknown

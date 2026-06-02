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


def test_edgar_absent_both_unknown():
    # Strip SIC -> unknown -> symmetric, NO masking, gate fires on D/E 8.0.
    m = merge([MockProvider().fetch("SCHW")])
    m.sic = None
    card = score(m, CFG)
    assert card.sic_bucket == "unknown"
    assert card.moat is not None                    # masked legs now contribute
    assert "over_leveraged" in card.gates           # D/E 8.0 trips when unknown

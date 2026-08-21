"""The /deep inventory context line (docs/PLAN_INVENTORY_DECOMPOSITION.md §1).

Reports the inventory LEVEL and days-inventory-outstanding trend. Prompt-only, never
scored, never flagged, and never in the grounding haystack.
"""
from dataclasses import fields

import pytest

from shortlist.research import inventory

CFG = {"enabled": True}


class _M:
    """Minimal StockMetrics stand-in — the line only reads financial_series."""

    def __init__(self, series=None):
        self.financial_series = series


def _year(inv, rev, gp, end="2025-12-31"):
    return {"period_end": end, "inventory": inv, "revenue": rev, "gross_profit": gp}


# --- abstention ------------------------------------------------------------------

def test_abstains_when_disabled_or_config_absent():
    s = [_year(135.9e6, 246.6e6, 62.1e6)]
    assert inventory.context_line(_M(s), None) is None
    assert inventory.context_line(_M(s), {"enabled": False}) is None


def test_abstains_without_a_series():
    assert inventory.context_line(_M(None), CFG) is None
    assert inventory.context_line(_M([]), CFG) is None


def test_abstains_when_the_filer_reports_no_inventory():
    """A bank or a services company files no inventory line. That is the NORMAL case,
    not a failure, and must abstain rather than render a zero."""
    assert inventory.context_line(_M([_year(None, 1e9, 4e8)]), CFG) is None


# --- days inventory outstanding ---------------------------------------------------

def test_dio_matches_hand_computed_hdsn():
    """HDSN's real figures. FY2024 205 days, FY2025 269 days — the number the §0.2
    analysis turns on, and the one signal that does not depend on which
    working-capital lines are stripped."""
    cur = _year(135.923e6, 246.6e6, 246.6e6 * 0.252, "2025-12-31")
    prior = _year(96.247e6, 237.1e6, 237.1e6 * 0.277, "2024-12-31")
    assert inventory._dio(cur) == pytest.approx(269.0, abs=0.5)
    assert inventory._dio(prior) == pytest.approx(204.9, abs=0.5)
    line = inventory.context_line(_M([cur, prior]), CFG)
    assert "days inventory outstanding 205 -> 269" in line


def test_dio_none_when_cogs_non_positive():
    """gross_profit >= revenue (financials, royalty trusts) makes DIO meaningless.
    Abstain from the leg rather than emit a negative or divide by zero."""
    assert inventory._dio(_year(1e6, 1e8, 1e8)) is None      # COGS == 0
    assert inventory._dio(_year(1e6, 1e8, 1.2e8)) is None    # COGS < 0


def test_dio_none_on_any_missing_input():
    assert inventory._dio(_year(None, 1e8, 4e7)) is None
    assert inventory._dio({"inventory": 1e6, "revenue": None, "gross_profit": 4e7}) is None
    assert inventory._dio({"inventory": 1e6, "revenue": 1e8, "gross_profit": None}) is None


def test_line_survives_a_cogs_free_filer_if_inventory_is_present():
    """No DIO leg, but the balance trend still renders — partial data degrades to a
    shorter line, never to an exception."""
    line = inventory.context_line(
        _M([_year(50e6, 1e8, 1.2e8), _year(40e6, 9e7, 1.1e8)]), CFG)
    assert line is not None
    assert "days inventory outstanding" not in line
    assert "balance $40M -> $50M" in line


# --- divergence + rendering --------------------------------------------------------

def test_reports_inventory_growth_against_revenue_growth():
    """HDSN FY2025: inventory +41% while revenue grew 4% — the divergence is the whole
    reason the line exists."""
    line = inventory.context_line(_M([
        _year(135.923e6, 246.6e6, 62.1e6, "2025-12-31"),
        _year(96.247e6, 237.1e6, 65.7e6, "2024-12-31"),
    ]), CFG)
    assert "+41%" in line and "revenue +4% over the same period" in line


def test_single_year_series_renders_a_bare_balance():
    line = inventory.context_line(_M([_year(135.923e6, 246.6e6, 62.1e6)]), CFG)
    assert "balance $136M" in line and "->" not in line.split(".")[0]


def test_earlier_years_give_a_base_rate():
    """A one-year DIO move must not read as a trend, so prior years ride along."""
    ys = [_year(135.9e6, 246.6e6, 246.6e6 * 0.252, "2025-12-31"),
          _year(96.2e6, 237.1e6, 237.1e6 * 0.277, "2024-12-31"),
          _year(154.4e6, 244.6e6, 244.6e6 * 0.35, "2023-12-31"),
          _year(147.0e6, 325.2e6, 325.2e6 * 0.501, "2022-12-31")]
    assert "earlier years:" in inventory.context_line(_M(ys), CFG)


def test_line_disclaims_what_it_is_not():
    """The line is a LEVEL, not a cash-flow bridge. §0.2: a single-line 'FCF excluding
    the inventory build' reverses sign once payables are included, so the text must not
    let a reader reconstruct one and must say other WC lines are excluded."""
    line = inventory.context_line(_M([_year(135.9e6, 246.6e6, 62.1e6)]), CFG)
    assert "NOT a filing quote" in line
    assert "not a free-cash-flow bridge" in line
    assert "working-capital lines are excluded" in line
    assert "free cash flow" not in line.lower().replace("free-cash-flow", "")


def test_module_never_computes_an_fcf_adjustment():
    """Guard against the cut design creeping back in: no public helper may return an
    'FCF excluding inventory' figure. See the module docstring."""
    assert not [n for n in dir(inventory) if "fcf" in n.lower()]


# --- the coverage denominator (the silent hazard) ----------------------------------

def test_inventory_is_excluded_from_coverage_accounting():
    """`statements` is in KEY_OBJECTS, so an un-excluded field moves the coverage
    DENOMINATOR for every snapshot ever taken (mock GEV 0.855 -> 0.825), which shifts
    accumulate.py's THIN_MARK CAPTURED/THIN split. A prior coverage move flipped 16%
    of 1,432 snapshots."""
    from shortlist.data.models import _NON_SIGNAL_FIELDS, Statements
    assert "inventory" in _NON_SIGNAL_FIELDS
    signal = [f.name for f in fields(Statements) if f.name not in _NON_SIGNAL_FIELDS]
    assert "inventory" not in signal
    assert len(signal) == 8, "Statements signal-field count changed; coverage moved"


def _card(series):
    """ScoreCard with the given financial_series (mirrors test_assess._dcf_card)."""
    from shortlist.models import ScoreCard, StockMetrics
    m = StockMetrics(ticker="HDSN", financial_series=series)
    return ScoreCard(ticker="HDSN", composite=52.0, quality=40.0, moat=None,
                     growth=None, momentum=None, value=60.0, opportunity=60.0,
                     insider=None, metrics=m, sic_bucket="unknown")


# --- prompt-only: never in the grounding haystack ---------------------------------

def test_inventory_line_excluded_from_haystack():
    """A computed number must never be verifiable as a filing quote. Mirrors
    test_reverse_dcf_line_excluded_from_haystack — the line is assembled in
    _quant_context, which never touches FilingBundle."""
    from shortlist.research.assess import _quant_context
    from shortlist.research.models import FilingBundle, FilingText

    card = _card([
        _year(135.923e6, 246.6e6, 246.6e6 * 0.252, "2025-12-31"),
        _year(96.247e6, 237.1e6, 237.1e6 * 0.277, "2024-12-31"),
    ])
    line = _quant_context(card, "", None, None, None, None, CFG)
    assert "days inventory outstanding" in line

    tenk = FilingText("HDSN", "acc", "2026-03-16", business="b", mda="m",
                      risk_factors="r")
    bundle = FilingBundle(tenk=tenk, primary_accession="acc", cache_key="acc",
                          filing_date="2026-03-16")
    assert "days inventory outstanding" not in bundle.haystack()
    assert "Inventory:" not in bundle.haystack()


def test_quant_context_back_compat_without_the_inventory_config():
    """invcfg defaults to None, so every existing call arity still works and emits no
    inventory line."""
    from shortlist.research.assess import _quant_context

    card = _card([_year(135.9e6, 246.6e6, 62.1e6)])
    assert "Inventory:" not in _quant_context(card)
    assert "Inventory:" not in _quant_context(card, "", None, None, None, None)


# --- the field is inert for scoring -----------------------------------------------

def test_inventory_never_changes_a_score_or_a_flag():
    """financial_series is scorer-inert by contract (models.py, bridge.py). The line is
    research context only: no sub-score, no composite move, no flag, no gate."""
    from pathlib import Path

    import yaml

    from shortlist.models import StockMetrics
    from shortlist.scoring import score

    cfg = yaml.safe_load(Path("config.yaml").read_text())
    base = StockMetrics(ticker="HDSN", gross_margin=0.25, net_margin=0.05,
                        revenue=2.5e8, market_cap=8e8)
    withinv = StockMetrics(ticker="HDSN", gross_margin=0.25, net_margin=0.05,
                           revenue=2.5e8, market_cap=8e8)
    withinv.financial_series = [_year(135.9e6, 246.6e6, 62.1e6)]
    a, b = score(base, cfg), score(withinv, cfg)
    assert a.composite == b.composite
    assert a.scored == b.scored and a.passed == b.passed
    assert (a.flags or []) == (b.flags or [])
    assert (a.gates or []) == (b.gates or [])

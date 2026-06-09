# tests/test_macro_flag.py
from __future__ import annotations
import copy
from pathlib import Path
import yaml
from shortlist.data.macro import MacroContext
from shortlist.models import StockMetrics
from shortlist.scoring import score

# Load the shipped config (NOT a cross-test import — tests/ is not a package, so
# `from tests.test_scoring import CONFIG` does not resolve under pytest's default
# prepend import mode). This mirrors the integration test at the bottom of
# test_scoring.py, which loads config.yaml directly.
_BASE = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text())

RISK_OFF = MacroContext("2026-06-01", 4.45, -0.2, 5.4, 27.0, 3.64, "risk-off", True)
RISK_ON  = MacroContext("2026-06-01", 4.45, 0.5, 2.7, 15.0, 3.64, "risk-on", False)

def _cfg():
    c = copy.deepcopy(_BASE)
    c.setdefault("flags", {})["risk_off_regime"] = {
        "max_net_debt_ebitda": 3.0, "max_debt_to_equity": 1.5,
        "cyclical_buckets": ["energy"]}
    return c

def test_flag_fires_on_leveraged_name_in_risk_off():
    m = StockMetrics(ticker="LEV", net_debt_to_ebitda=4.5)
    card = score(m, _cfg(), macro=RISK_OFF)
    assert "risk_off_regime" in card.flags

def test_flag_quiet_when_risk_on():
    m = StockMetrics(ticker="LEV", net_debt_to_ebitda=4.5)
    card = score(m, _cfg(), macro=RISK_ON)
    assert "risk_off_regime" not in card.flags

def test_flag_quiet_when_unlevered_and_noncyclical():
    m = StockMetrics(ticker="SAFE", net_debt_to_ebitda=0.5, debt_to_equity=0.3)
    card = score(m, _cfg(), macro=RISK_OFF)
    assert "risk_off_regime" not in card.flags

def test_flag_never_affects_composite_or_passed():
    m = StockMetrics(ticker="LEV", net_debt_to_ebitda=4.5)
    base = score(m, _cfg(), macro=None)
    off = score(m, _cfg(), macro=RISK_OFF)
    assert off.composite == base.composite and off.passed == base.passed

def test_backcompat_macro_none_byte_identical():
    # macro defaulted vs explicit None: identical ScoreCard
    m = StockMetrics(ticker="X", net_debt_to_ebitda=4.5, roe=0.2, net_margin=0.1)
    assert score(m, _cfg()) == score(m, _cfg(), macro=None)

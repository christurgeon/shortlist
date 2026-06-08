import yaml
from pathlib import Path

CFG = yaml.safe_load((Path(__file__).resolve().parents[1] / "config.yaml").read_text())


def test_leverage_block_shipped_on():
    lv = CFG["gates"]["leverage"]
    assert lv["enabled"] is True
    assert lv["max_net_debt_to_ebitda"] == 4.0
    assert lv["min_ebitda_margin"] == 0.03
    assert lv["dte_artifact_ceiling"] == 20.0
    assert lv["min_interest_coverage_for_gate"] == 2.0


def test_fcf_block_shipped_on():
    fc = CFG["gates"]["fcf"]
    assert fc["enabled"] is True
    assert fc["excuse_min_revenue_cagr"] == 0.15
    assert fc["excuse_min_persistence"] == 0.70


def test_cash_burn_flag_enabled():
    assert CFG["flags"]["cash_burn"]["enabled"] is True


def test_net_debt_axis_band_present():
    assert CFG["thresholds"]["net_debt_to_ebitda"] == [6.0, 0.0]

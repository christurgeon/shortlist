# tests/test_macro_config.py
from __future__ import annotations
from pathlib import Path
import yaml

CFG = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text())

def test_macro_block_present_and_enabled():
    m = CFG["macro"]
    assert m["enabled"] is True
    assert set(m["series"]) == {"dgs10", "t10y2y", "hy_oas", "vix", "fedfunds"}
    assert "hy_oas" in m["risk_off"] and "hy_oas" in m["risk_on"]

def test_risk_off_flag_block_present():
    ro = CFG["flags"]["risk_off_regime"]
    assert ro["max_net_debt_ebitda"] > 0
    assert isinstance(ro["cyclical_buckets"], list)

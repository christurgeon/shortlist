from pathlib import Path

import yaml

from shortlist.sectors import leg_applicable, resolve_bucket


def _cfg():
    return yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text())


def test_shipped_config_buckets_resolve():
    cfg = _cfg()
    assert "sectors" in cfg and "validity" in cfg
    assert resolve_bucket("6211", cfg) == "financials"
    assert resolve_bucket("6798", cfg) == "reit"
    assert resolve_bucket("6311", cfg) == "insurer"
    assert resolve_bucket("6231", cfg) == "unknown"   # exchange stays unknown
    assert leg_applicable("financials", "fcf_yield", cfg) is False


def test_validity_defaults():
    v = _cfg()["validity"]
    assert 0.0 < v["min_valid_leg_fraction"] <= 1.0
    assert v["unknown_min_present_legs"] >= 1
    assert 0.0 < v["min_scored_weight"] <= 1.0

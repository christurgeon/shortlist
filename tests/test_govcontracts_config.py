import yaml
from pathlib import Path


def test_config_has_gov_contracts_blocks():
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    assert "gov_contracts" in cfg["harness_sources"]
    g = cfg["gov_contracts"]
    assert 0 < g["match_min_confidence"] <= 1
    assert g["trailing_months"] == 24
    assert g["max_pages"] >= 1
    assert cfg["research"]["gov_contracts"]["enabled"] is True

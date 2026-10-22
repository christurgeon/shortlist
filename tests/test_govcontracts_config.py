import yaml
from pathlib import Path


def test_config_has_gov_contracts_blocks():
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    from shortlist.screen import research_chain
    assert "gov_contracts" not in cfg["harness_sources"]
    assert "gov_contracts" in research_chain(cfg["harness_sources"], cfg)
    g = cfg["gov_contracts"]
    assert 0 < g["match_min_confidence"] <= 1
    assert g["trailing_months"] == 24
    assert g["max_pages"] >= 1
    assert cfg["research"]["gov_contracts"]["enabled"] is True


def test_gov_contracts_fetch_deadline_is_configured():
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    assert cfg["gov_contracts"]["deadline_s"] > 0

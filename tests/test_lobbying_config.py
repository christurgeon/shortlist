import yaml
from pathlib import Path


def test_config_has_lobbying_blocks():
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    assert "lobbying" in cfg["harness_sources"]
    g = cfg["lobbying"]
    assert g["base_url"].startswith("https://")
    assert 0 < g["match_min_confidence"] <= 1
    assert g["trailing_months"] == 24
    assert g["max_pages_per_year"] >= 1
    assert cfg["research"]["lobbying"]["enabled"] is True

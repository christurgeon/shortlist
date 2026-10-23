import yaml
from pathlib import Path


def test_config_has_lobbying_blocks():
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    from shortlist.screen import research_chain
    assert "lobbying" not in cfg["harness_sources"]
    assert "lobbying" in research_chain(cfg["harness_sources"], cfg)
    g = cfg["lobbying"]
    assert g["base_url"].startswith("https://")
    assert 0 < g["match_min_confidence"] <= 1
    assert g["trailing_months"] == 24
    assert g["max_pages_per_year"] >= 1
    assert cfg["research"]["lobbying"]["enabled"] is True


def test_lobbying_fetch_deadline_is_configured():
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    assert cfg["lobbying"]["deadline_s"] > 0


def test_configured_aliases_all_survive_parsing():
    from shortlist.data.sources._common import _parse_aliases
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    for section in ("lobbying", "gov_contracts"):
        raw = cfg[section]["aliases"]
        assert all(isinstance(k, str) and k == k.upper() for k in raw), section
        parsed = _parse_aliases(raw)
        assert {k: list(v) for k, v in parsed.items()} == raw, section

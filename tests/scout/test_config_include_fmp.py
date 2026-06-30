import yaml
from pathlib import Path


def test_repo_config_rations_fmp_on_digest():
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    dp = cfg["scout"]["daily_push"]
    assert dp["include_fmp"] is False, "repo config should ration FMP on the daily digest"

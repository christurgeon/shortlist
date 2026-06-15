import yaml
from pathlib import Path

from shortlist.cache import ttl_for


def test_company_news_mapped_to_6h_bucket():
    # Mapped explicitly -> no "unmapped endpoint" warning, 6h TTL (quote bucket).
    assert ttl_for("finnhub", "company-news", {}) == 21600


def test_config_has_news_spike_flag():
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    ns = cfg["flags"]["news_spike"]
    assert ns["min_count_7d"] >= 1
    assert ns["require_rising"] is True
    assert ns["max_staleness_days"] >= 1

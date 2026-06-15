import yaml
from pathlib import Path

from shortlist.models import StockMetrics
from shortlist.scoring import score
from shortlist.cache import ttl_for
from shortlist.research import assess


def test_earnings_endpoints_cache_mapped():
    assert ttl_for("finnhub", "stock/earnings", {}) == 86400      # fundamentals 1d
    assert ttl_for("finnhub", "calendar/earnings", {}) == 21600   # quote 6h


def test_config_has_earnings_research_block():
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    assert cfg["research"]["earnings"]["enabled"] is True


def test_earnings_fields_do_not_change_composite():
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    base = StockMetrics(ticker="AAPL", gross_margin=0.4, net_margin=0.25,
                        revenue=4e11, market_cap=3e12)
    withe = StockMetrics(ticker="AAPL", gross_margin=0.4, net_margin=0.25,
                         revenue=4e11, market_cap=3e12,
                         earnings_beat_rate=1.0, earnings_avg_surprise_pct=5.0,
                         earnings_quarters=4, earnings_days_to_next=10)
    a, b = score(base, cfg), score(withe, cfg)
    assert a.composite == b.composite
    assert a.flags == b.flags


class _Card:
    def __init__(self, m):
        self.metrics = m
        self.composite = 50.0
        self.gates = []
        self.flags = []
        for k in ("quality", "moat", "growth", "momentum", "value", "insider",
                  "risk", "confidence", "sic_bucket"):
            setattr(self, k, None)


def test_quant_context_includes_earnings_line():
    m = StockMetrics(ticker="AAPL", earnings_quarters=4, earnings_beat_rate=1.0,
                     earnings_avg_surprise_pct=4.2)
    out = assess._quant_context(_Card(m), "", None, {"enabled": True})
    assert "Earnings execution" in out


def test_quant_context_omits_earnings_line_when_absent():
    out = assess._quant_context(_Card(StockMetrics(ticker="AAPL")), "", None, {"enabled": True})
    assert "Earnings execution" not in out

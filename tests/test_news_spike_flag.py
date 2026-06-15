import yaml
from pathlib import Path

from shortlist.models import StockMetrics
from shortlist.scoring import check_flags, score


CFG_FLAGS = {"news_spike": {"min_count_7d": 10, "require_rising": True,
                            "max_staleness_days": 3}}


def _m(**kw):
    return StockMetrics(ticker="AAPL", **kw)


def test_fires_when_elevated_rising_fresh():
    flags = check_flags(_m(news_count_7d=15, news_flow_rising=True,
                           news_data_age_days=1), CFG_FLAGS)
    assert "news_spike" in flags


def test_no_fire_below_floor():
    assert "news_spike" not in check_flags(
        _m(news_count_7d=5, news_flow_rising=True, news_data_age_days=1), CFG_FLAGS)


def test_no_fire_when_not_rising():
    assert "news_spike" not in check_flags(
        _m(news_count_7d=20, news_flow_rising=False, news_data_age_days=1), CFG_FLAGS)


def test_no_fire_when_stale():
    assert "news_spike" not in check_flags(
        _m(news_count_7d=20, news_flow_rising=True, news_data_age_days=10), CFG_FLAGS)


def test_no_fire_when_truncated_blanks_rising():
    # Free-tier cap truncated the window: prior unknown -> rising None -> no spurious fire
    # (this is the AAPL case: 247 articles all in the last few days).
    m = _m(news_count_7d=247, news_count_prior_7d=None, news_flow_rising=None,
           news_truncated=True, news_data_age_days=1)
    assert "news_spike" not in check_flags(m, CFG_FLAGS)


def test_noop_when_config_absent():
    # No news_spike block -> never fires (back-compat).
    assert "news_spike" not in check_flags(
        _m(news_count_7d=99, news_flow_rising=True), {})


def test_news_fields_do_not_change_composite():
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    base = StockMetrics(ticker="AAPL", gross_margin=0.4, net_margin=0.25,
                        revenue=4e11, market_cap=3e12)
    withnews = StockMetrics(ticker="AAPL", gross_margin=0.4, net_margin=0.25,
                            revenue=4e11, market_cap=3e12,
                            news_count_7d=40, news_flow_rising=True, news_data_age_days=0)
    a, b = score(base, cfg), score(withnews, cfg)
    assert a.composite == b.composite        # news never touches the composite
    assert "news_spike" in (b.flags or [])   # but the advisory flag does fire

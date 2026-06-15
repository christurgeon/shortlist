from datetime import date, datetime, timezone, timedelta

from shortlist.data.sources import _news_flow, _normalize_finnhub
from shortlist.data.models import TickerSnapshot, NewsFlow
from shortlist.data.bridge import snapshot_to_metrics
from shortlist.models import StockMetrics


REF = date(2026, 6, 15)


def _ts(days_ago: int) -> int:
    d = datetime(2026, 6, 15, tzinfo=timezone.utc) - timedelta(days=days_ago)
    return int(d.timestamp())


def test_news_flow_buckets_recent_prior_window():
    articles = [
        {"datetime": _ts(1)}, {"datetime": _ts(3)}, {"datetime": _ts(6)},  # recent (<=7d): 3
        {"datetime": _ts(9)}, {"datetime": _ts(13)},                       # prior (7-14d): 2
        {"datetime": _ts(20)},                                             # window only
        {"datetime": None},                                               # skipped
    ]
    nf = _news_flow(articles, ref=REF)
    assert nf.count_recent == 3
    assert nf.count_prior == 2
    assert nf.count_window == 6
    assert nf.latest_dt == (REF - timedelta(days=1)).isoformat()


def test_news_flow_empty_is_zero():
    nf = _news_flow([], ref=REF)
    assert nf.count_recent == 0 and nf.count_window == 0 and nf.latest_dt is None
    assert nf.truncated is False


def test_news_flow_detects_free_tier_cap_truncation():
    # 240 articles ALL within the last 5 days (no history past the prior window):
    # the free-tier cap dropped older articles -> prior is unreliable.
    articles = [{"datetime": _ts(d % 5 + 1)} for d in range(240)]
    nf = _news_flow(articles, ref=REF)
    assert nf.truncated is True
    assert nf.count_prior is None          # blanked: a false 0 otherwise
    assert nf.count_window == 240


def test_high_but_spread_volume_is_not_truncated():
    # 220 articles spread across the full 30d window (< cap) -> real data.
    articles = [{"datetime": _ts(d % 28 + 1)} for d in range(220)]
    nf = _news_flow(articles, ref=REF)
    assert nf.truncated is False
    assert nf.count_prior is not None


def test_capped_but_prior_reliable_keeps_prior():
    # 260 articles spread across the FULL 30d (oldest reaches back past 14d): the list
    # is capped (truncated=True) but the prior window survived, so prior stays reliable.
    articles = [{"datetime": _ts(d % 28 + 1)} for d in range(260)]
    nf = _news_flow(articles, ref=REF)
    assert nf.truncated is True          # honest: the list IS capped
    assert nf.count_prior is not None    # ...but prior was not eaten -> kept


def test_boundary_7d_and_14d_buckets():
    # exactly 7d ago -> recent (>= recent_cut); exactly 14d ago -> prior; 15d -> window only
    nf = _news_flow([{"datetime": _ts(7)}, {"datetime": _ts(14)}, {"datetime": _ts(15)}],
                    ref=REF)
    assert nf.count_recent == 1
    assert nf.count_prior == 1
    assert nf.count_window == 3


def test_cache_hit_reaging_shifts_buckets():
    # Same raw list re-bucketed a day later: an article that was 7d-recent ages into prior.
    articles = [{"datetime": _ts(7)}]
    today = _news_flow(articles, ref=REF)
    tomorrow = _news_flow(articles, ref=REF + timedelta(days=1))
    assert today.count_recent == 1 and today.count_prior == 0
    assert tomorrow.count_recent == 0 and tomorrow.count_prior == 1


def test_millisecond_timestamp_tolerated():
    ms = int((datetime(2026, 6, 15, tzinfo=timezone.utc) - timedelta(days=2)).timestamp()) * 1000
    nf = _news_flow([{"datetime": ms}], ref=REF)
    assert nf.count_window == 1 and nf.count_recent == 1   # not silently dropped


def test_normalize_finnhub_populates_news_section():
    raw = {"news": [{"datetime": _ts(2)}, {"datetime": _ts(2)}]}
    snap = _normalize_finnhub("AAPL", raw)
    assert snap.news is not None
    assert snap.news.count_window == 2


def test_normalize_finnhub_no_news_key_leaves_none():
    assert _normalize_finnhub("AAPL", {"quote": {"c": 1.0}}).news is None


def test_bridge_derives_news_metrics():
    s = TickerSnapshot(ticker="AAPL", as_of="2026-06-15")
    s.news = NewsFlow(as_of="2026-06-15", count_recent=12, count_prior=4,
                      count_window=30, latest_dt="2026-06-14")
    m = snapshot_to_metrics(s)
    assert m.news_count_7d == 12
    assert m.news_count_prior_7d == 4
    assert m.news_count_30d == 30
    assert m.news_flow_rising is True
    assert m.news_data_age_days == 1


def test_bridge_rising_false_and_none_safe():
    s = TickerSnapshot(ticker="AAPL", as_of="2026-06-15")
    s.news = NewsFlow(as_of="2026-06-15", count_recent=2, count_prior=9)
    assert snapshot_to_metrics(s).news_flow_rising is False
    # no section -> all None
    assert snapshot_to_metrics(TickerSnapshot(ticker="KO")).news_count_7d is None


def test_news_section_roundtrips():
    s = TickerSnapshot(ticker="AAPL")
    s.news = NewsFlow(as_of="2026-06-15", count_recent=12, count_prior=4,
                      count_window=30, latest_dt="2026-06-14", truncated=True)
    back = TickerSnapshot.from_dict(s.to_dict())
    assert back.news.count_recent == 12 and back.news.latest_dt == "2026-06-14"
    assert back.news.truncated is True   # the bool survives the round-trip


def test_metrics_fields_default_none():
    m = StockMetrics(ticker="AAPL")
    for fld in ("news_count_7d", "news_count_prior_7d", "news_count_30d",
                "news_flow_rising", "news_truncated", "news_data_age_days"):
        assert getattr(m, fld) is None

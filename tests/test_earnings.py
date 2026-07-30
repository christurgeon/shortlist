from datetime import date, timedelta

from shortlist.data.sources import _earnings, _normalize_finnhub
from shortlist.data.models import TickerSnapshot, Earnings
from shortlist.data.bridge import snapshot_to_metrics
from shortlist.models import StockMetrics
from shortlist.research.earnings import context_line


REF = date(2026, 6, 15)


def _rows(*pcts):
    return [{"surprisePercent": p} for p in pcts]


def _cal(*dates_with_actual):
    return {"earningsCalendar": [{"date": d, "epsActual": a} for d, a in dates_with_actual]}


def test_earnings_counts_beats_and_skips_none():
    e = _earnings(_rows(1.1, -2.0, 3.5, None), None, ref=REF)
    assert e.quarters == 3            # the None surprise is skipped
    assert e.beats == 2               # 1.1 and 3.5
    assert e.last_surprise_pct == 1.1
    assert e.recent_surprise_pcts == [1.1, -2.0, 3.5]


def test_earnings_orders_newest_first_by_period():
    # Rows supplied OUT of order; latest must be the most-recent period regardless.
    rows = [{"surprisePercent": 1.0, "period": "2025-06-30"},
            {"surprisePercent": 9.0, "period": "2026-03-31"},   # newest
            {"surprisePercent": 2.0, "period": "2025-12-31"}]
    e = _earnings(rows, None, ref=REF)
    assert e.last_surprise_pct == 9.0
    assert e.recent_surprise_pcts == [9.0, 2.0, 1.0]


def test_earnings_same_day_amc_report_is_next():
    # A report dated exactly today (after-close print, no actual yet) must be selected.
    cal = _cal((REF.isoformat(), None))
    e = _earnings(_rows(1.0), cal, ref=REF)
    assert e.next_date == REF.isoformat()


def test_earnings_next_date_picks_earliest_future_unreported():
    cal = _cal(("2026-05-01", 2.0),   # past + reported -> ignored
               ("2026-07-29", None),  # future, unreported -> candidate
               ("2026-10-30", None))  # later future
    e = _earnings(_rows(1.0), cal, ref=REF)
    assert e.next_date == "2026-07-29"


def test_earnings_no_future_date_is_none():
    e = _earnings(_rows(1.0), _cal(("2026-01-01", 2.0)), ref=REF)
    assert e.next_date is None


def test_earnings_last_report_date_prefers_calendar_past():
    # SUE leg input: the latest PAST calendar entry with an actual is the announcement date.
    cal = _cal(("2026-03-01", 2.0),   # past + reported -> candidate
               ("2026-05-10", 3.0),   # latest past + reported -> picked
               ("2026-07-29", None))  # future -> ignored
    e = _earnings(_rows(1.0), cal, ref=REF)
    assert e.last_report_date == "2026-05-10"
    assert e.last_report_date_estimated is False   # a true announcement date


def test_earnings_last_report_date_falls_back_to_period():
    # No past calendar entries -> the weaker fiscal-quarter-END proxy (over-states staleness).
    rows = [{"surprisePercent": 1.0, "period": "2026-03-31"},   # newest period
            {"surprisePercent": 2.0, "period": "2025-12-31"}]
    e = _earnings(rows, _cal(("2026-07-29", None)), ref=REF)
    assert e.last_report_date == "2026-03-31"
    assert e.last_report_date_estimated is True    # quarter-end proxy, not a print date


def test_earnings_last_report_date_none_when_no_data():
    assert _earnings([], None, ref=REF).last_report_date is None


def test_earnings_empty_inputs():
    e = _earnings([], None, ref=REF)
    assert e.quarters is None and e.beats is None and e.last_surprise_pct is None


def test_normalize_finnhub_populates_earnings():
    # `_normalize_finnhub` has no `ref` seam (unlike `_earnings` above) -- it reads
    # date.today() -- so the unreported calendar entry must be future-dated RELATIVE TO NOW.
    # A hardcoded date rots: this test went red on 2026-07-30 when the calendar passed the
    # literal "2026-07-29" it used to pin.
    future = (date.today() + timedelta(days=30)).isoformat()
    raw = {"earnings": _rows(1.0, 2.0), "earnings_calendar": _cal((future, None))}
    snap = _normalize_finnhub("AAPL", raw)
    assert snap.earnings is not None
    assert snap.earnings.quarters == 2
    assert snap.earnings.next_date == future


def test_normalize_finnhub_no_earnings_key_leaves_none():
    assert _normalize_finnhub("AAPL", {"quote": {"c": 1.0}}).earnings is None


def test_calendar_request_window_reaches_back_for_past_announcements(monkeypatch):
    # SUE decay anchor: the calendar/earnings request must reach BACK past a full
    # quarter (~91d between prints, plus margin) so the latest PAST announcement
    # (epsActual set) is in the payload. A today-forward window starves the
    # `past` branch in _earnings, forcing the quarter-end fallback and making the
    # SUE leg decay systematically fast (TODO 2026-07-07 item 6).
    import asyncio
    from datetime import timedelta

    from shortlist.data.sources import FinnhubSource

    captured = {}

    async def fake_get(self, path, **params):
        if path == "calendar/earnings":
            captured.update(params)
        return {}

    monkeypatch.setattr(FinnhubSource, "_get", fake_get)

    async def go():
        src = FinnhubSource(api_key="test")
        try:
            await src.fetch("AAPL")
        finally:
            await src.aclose()

    asyncio.run(go())
    today = date.today()
    assert date.fromisoformat(captured["from"]) <= today - timedelta(days=100)
    assert date.fromisoformat(captured["to"]) >= today + timedelta(days=90)


def test_bridge_derives_earnings_metrics():
    s = TickerSnapshot(ticker="AAPL", as_of="2026-06-15")
    s.earnings = Earnings(as_of="2026-06-15", recent_surprise_pcts=[2.0, 4.0, 0.0, -1.0],
                          quarters=4, beats=2, last_surprise_pct=2.0, next_date="2026-07-29")
    m = snapshot_to_metrics(s)
    assert m.earnings_quarters == 4
    assert m.earnings_beats == 2
    assert m.earnings_beat_rate == 0.5
    assert abs(m.earnings_avg_surprise_pct - 1.25) < 1e-9
    assert m.earnings_last_surprise_pct == 2.0
    assert m.earnings_days_to_next == 44    # 2026-06-15 -> 2026-07-29


def test_bridge_derives_sue_inputs():
    # dispersion = pop std-dev of [2,4,0,-1]; days_since from last_report_date.
    s = TickerSnapshot(ticker="AAPL", as_of="2026-06-15")
    s.earnings = Earnings(as_of="2026-06-15", recent_surprise_pcts=[2.0, 4.0, 0.0, -1.0],
                          quarters=4, beats=2, last_surprise_pct=2.0,
                          next_date="2026-07-29", last_report_date="2026-05-10")
    m = snapshot_to_metrics(s)
    from statistics import pstdev
    assert abs(m.earnings_surprise_dispersion - pstdev([2.0, 4.0, 0.0, -1.0])) < 1e-9
    assert m.earnings_days_since_last_report == 36   # 2026-05-10 -> 2026-06-15


def _events_with_report(filed):
    from shortlist.data.models import Events
    return Events(last_report_filed=filed)


def test_bridge_sue_anchor_prefers_10q_filing_over_quarter_end():
    # Free-tier Finnhub serves NO past calendar entries (live-probed 2026-07-09), so
    # last_report_date is in practice always the quarter-END proxy. The EDGAR filing
    # stream has the latest 10-Q's filed date — a ~0-5d announcement proxy vs ~30-45d.
    # Truth is bracketed: quarter_end <= announcement <= 10-Q filed -> take the max.
    s = TickerSnapshot(ticker="AAPL", as_of="2026-07-09")
    s.earnings = Earnings(as_of="2026-07-09", recent_surprise_pcts=[2.0, 4.0, 0.0],
                          quarters=3, last_surprise_pct=2.0,
                          last_report_date="2026-03-31", last_report_date_estimated=True)
    s.events = _events_with_report("2026-05-05")
    m = snapshot_to_metrics(s)
    assert m.earnings_days_since_last_report == 65   # 2026-05-05 -> 2026-07-09, not 100


def test_bridge_sue_anchor_keeps_true_announcement_date():
    # A real calendar announcement date (estimated=False) is exact — never bumped to
    # the (later) 10-Q filed date.
    s = TickerSnapshot(ticker="AAPL", as_of="2026-07-09")
    s.earnings = Earnings(as_of="2026-07-09", recent_surprise_pcts=[2.0, 4.0, 0.0],
                          quarters=3, last_surprise_pct=2.0,
                          last_report_date="2026-04-30", last_report_date_estimated=False)
    s.events = _events_with_report("2026-05-05")
    m = snapshot_to_metrics(s)
    assert m.earnings_days_since_last_report == 70   # 2026-04-30 -> 2026-07-09


def test_bridge_sue_anchor_max_keeps_later_quarter_end():
    # Post-announce, pre-10-Q window: the latest filed 10-Q is the PRIOR quarter's and
    # predates the newest quarter-end -> max() keeps the quarter-end (never worse than
    # the pre-fix fallback).
    s = TickerSnapshot(ticker="AAPL", as_of="2026-07-09")
    s.earnings = Earnings(as_of="2026-07-09", recent_surprise_pcts=[2.0, 4.0, 0.0],
                          quarters=3, last_surprise_pct=2.0,
                          last_report_date="2026-06-30", last_report_date_estimated=True)
    s.events = _events_with_report("2026-05-05")
    m = snapshot_to_metrics(s)
    assert m.earnings_days_since_last_report == 9    # 2026-06-30 -> 2026-07-09


def test_earnings_estimated_flag_survives_roundtrip_and_defaults_true():
    # Old persisted snapshots lack the flag -> from_dict defaults to True (the honest
    # prior on the free tier), so replay of pre-fix accumulated data gets the anchor.
    s = TickerSnapshot(ticker="AAPL", as_of="2026-07-09")
    s.earnings = Earnings(as_of="2026-07-09", last_report_date="2026-05-10",
                          last_report_date_estimated=False)
    back = TickerSnapshot.from_dict(s.to_dict())
    assert back.earnings.last_report_date_estimated is False
    legacy = s.to_dict()
    del legacy["earnings"]["last_report_date_estimated"]
    assert TickerSnapshot.from_dict(legacy).earnings.last_report_date_estimated is True


def test_bridge_sue_dispersion_none_below_min_quarters():
    # 2 quarters < the min-quarters floor -> dispersion None (the SUE >=3 guard).
    s = TickerSnapshot(ticker="AAPL", as_of="2026-06-15")
    s.earnings = Earnings(as_of="2026-06-15", recent_surprise_pcts=[2.0, 4.0],
                          quarters=2, last_surprise_pct=2.0)
    m = snapshot_to_metrics(s)
    assert m.earnings_surprise_dispersion is None
    assert m.earnings_days_since_last_report is None  # no last_report_date


def test_earnings_section_roundtrips_last_report_date():
    s = TickerSnapshot(ticker="AAPL")
    s.earnings = Earnings(as_of="2026-06-15", last_report_date="2026-05-10")
    back = TickerSnapshot.from_dict(s.to_dict())
    assert back.earnings.last_report_date == "2026-05-10"


def test_bridge_none_safe_and_no_section():
    s = TickerSnapshot(ticker="AAPL", as_of="2026-06-15")
    s.earnings = Earnings(as_of="2026-06-15")   # no quarters / no next_date
    m = snapshot_to_metrics(s)
    assert m.earnings_beat_rate is None and m.earnings_days_to_next is None
    assert snapshot_to_metrics(TickerSnapshot(ticker="KO")).earnings_quarters is None


def test_earnings_section_roundtrips():
    s = TickerSnapshot(ticker="AAPL")
    s.earnings = Earnings(as_of="2026-06-15", recent_surprise_pcts=[1.1, 2.2],
                          quarters=2, beats=2, last_surprise_pct=1.1, next_date="2026-07-29")
    back = TickerSnapshot.from_dict(s.to_dict())
    assert back.earnings.recent_surprise_pcts == [1.1, 2.2]
    assert back.earnings.next_date == "2026-07-29"


def test_metrics_fields_default_none():
    m = StockMetrics(ticker="AAPL")
    for fld in ("earnings_beat_rate", "earnings_beats", "earnings_avg_surprise_pct",
                "earnings_last_surprise_pct", "earnings_quarters", "earnings_days_to_next"):
        assert getattr(m, fld) is None


# --- research context line ---
CFG = {"enabled": True}


def _m(**kw):
    return StockMetrics(ticker="AAPL", **kw)


def test_line_renders_with_beats_avg_and_next():
    line = context_line(_m(earnings_quarters=4, earnings_beats=4, earnings_beat_rate=1.0,
                           earnings_avg_surprise_pct=4.2, earnings_last_surprise_pct=1.1,
                           earnings_days_to_next=44), CFG)
    assert line is not None
    assert "4/4 recent quarters" in line
    assert "+4.2%" in line and "~44d" in line


def test_line_abstains_when_disabled_or_no_quarters():
    assert context_line(_m(earnings_quarters=4), {"enabled": False}) is None
    assert context_line(_m(), CFG) is None
    assert context_line(_m(earnings_quarters=0), CFG) is None

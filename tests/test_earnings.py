from datetime import date

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


def test_earnings_empty_inputs():
    e = _earnings([], None, ref=REF)
    assert e.quarters is None and e.beats is None and e.last_surprise_pct is None


def test_normalize_finnhub_populates_earnings():
    raw = {"earnings": _rows(1.0, 2.0), "earnings_calendar": _cal(("2026-07-29", None))}
    snap = _normalize_finnhub("AAPL", raw)
    assert snap.earnings is not None
    assert snap.earnings.quarters == 2
    assert snap.earnings.next_date == "2026-07-29"


def test_normalize_finnhub_no_earnings_key_leaves_none():
    assert _normalize_finnhub("AAPL", {"quote": {"c": 1.0}}).earnings is None


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

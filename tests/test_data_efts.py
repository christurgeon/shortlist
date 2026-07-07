"""Offline tests for the EFTS shared leaf (data/efts.py). All HTTP is injected via the
`_get(params) -> (status, payload)` seam — no network, matching the finra/short_interest
fixture idiom."""
import json
from datetime import date
from pathlib import Path

from shortlist.data.efts import (EFTS_LAG_DAYS, fetch_eightk_day,
                                 fetch_eightk_range, fetch_eightk_window,
                                 normalize_hit)


def _hit(adsh, cik="0000320193", items=("1.01", "9.01"), file_date="2026-06-03",
         file_type="8-K", sics=("3571",),
         names=("Apple Inc.  (AAPL)  (CIK 0000320193)",)):
    """One EFTS hit in the live payload shape (probed 2026-07-07)."""
    return {"_id": f"{adsh}:doc.htm",
            "_source": {"adsh": adsh, "ciks": [cik], "items": list(items),
                        "file_date": file_date, "file_type": file_type,
                        "root_forms": ["8-K"], "sics": list(sics),
                        "display_names": list(names)}}


def _payload(hits, total=None):
    return {"hits": {"total": {"value": total if total is not None else len(hits)},
                     "hits": hits}}


class _ScriptedGet:
    """Test seam: returns scripted (status, payload) responses in order; records params."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, params):
        self.calls.append(dict(params))
        return self.responses.pop(0)


def test_normalize_hit_shape():
    row = normalize_hit(_hit("0001-26-000001"))
    assert row == {"adsh": "0001-26-000001", "cik": "0000320193",
                   "items": ["1.01", "9.01"], "file_date": "2026-06-03",
                   "file_type": "8-K", "sics": ["3571"],
                   "display_names": ["Apple Inc.  (AAPL)  (CIK 0000320193)"]}


def test_normalize_hit_junk_returns_none():
    assert normalize_hit(None) is None
    assert normalize_hit({}) is None
    assert normalize_hit({"_source": {"adsh": "", "ciks": ["1"]}}) is None
    assert normalize_hit({"_source": {"adsh": "a", "ciks": []}}) is None


def test_range_paginates_and_assembles():
    hits1 = [_hit(f"a-{i}") for i in range(100)]
    hits2 = [_hit(f"b-{i}") for i in range(100)]
    hits3 = [_hit(f"c-{i}") for i in range(50)]
    get = _ScriptedGet([(200, _payload(hits1, total=250)),
                        (200, _payload(hits2, total=250)),
                        (200, _payload(hits3, total=250))])
    rows = fetch_eightk_range(date(2026, 6, 3), date(2026, 6, 3),
                              identity="t@example.com", throttle_s=0, _get=get)
    assert len(rows) == 250
    assert [c["from"] for c in get.calls] == [0, 100, 200]
    assert get.calls[0]["forms"] == "8-K"
    assert get.calls[0]["startdt"] == "2026-06-03" and get.calls[0]["enddt"] == "2026-06-03"


def test_range_retries_on_500_then_succeeds():
    get = _ScriptedGet([(500, None), (503, None), (200, _payload([_hit("a-1")]))])
    rows = fetch_eightk_range(date(2026, 6, 3), date(2026, 6, 3),
                              identity="t@example.com", throttle_s=0, _get=get)
    assert len(rows) == 1 and len(get.calls) == 3


def test_range_gives_up_after_bounded_retries():
    get = _ScriptedGet([(500, None), (500, None), (500, None)])
    rows = fetch_eightk_range(date(2026, 6, 3), date(2026, 6, 3),
                              identity="t@example.com", throttle_s=0, _get=get)
    assert rows is None
    assert len(get.calls) == 3          # max_retries=2 -> exactly 3 attempts, never more


def test_range_does_not_retry_non_5xx():
    get = _ScriptedGet([(429, None)])
    rows = fetch_eightk_range(date(2026, 6, 3), date(2026, 6, 3),
                              identity="t@example.com", throttle_s=0, _get=get)
    assert rows is None and len(get.calls) == 1


def test_range_splits_when_total_at_es_window():
    """A multi-day range whose probe reports total >= 9,900 splits at the midpoint —
    the recursion re-probes each half."""
    left_hits = [_hit(f"l-{i}", file_date="2026-06-01") for i in range(2)]
    right_hits = [_hit(f"r-{i}", file_date="2026-06-03") for i in range(3)]
    get = _ScriptedGet([
        (200, _payload([], total=9_900)),          # probe 06-01..06-04 -> split
        (200, _payload(left_hits, total=2)),       # 06-01..06-02
        (200, _payload(right_hits, total=3)),      # 06-03..06-04
    ])
    rows = fetch_eightk_range(date(2026, 6, 1), date(2026, 6, 4),
                              identity="t@example.com", throttle_s=0, _get=get)
    assert len(rows) == 5
    spans = [(c["startdt"], c["enddt"]) for c in get.calls]
    assert spans == [("2026-06-01", "2026-06-04"),
                     ("2026-06-01", "2026-06-02"),
                     ("2026-06-03", "2026-06-04")]


def test_day_cache_writes_complete_unfiltered_rows(tmp_path):
    """Cache-write-before-filter: the 8-K/A amendment row is IN the cached file with its
    file_type preserved — filtering is the aggregator's job, never the leaf's."""
    hits = [_hit("a-1", file_type="8-K"), _hit("a-2", file_type="8-K/A")]
    get = _ScriptedGet([(200, _payload(hits))])
    day = date(2026, 6, 3)
    rows = fetch_eightk_day(day, identity="t@example.com", cache_dir=str(tmp_path),
                            today=date(2026, 6, 10), throttle_s=0, _get=get)
    assert [r["file_type"] for r in rows] == ["8-K", "8-K/A"]
    env = json.loads((tmp_path / "2026-06-03.json").read_text())
    assert env["fetched_on"] == "2026-06-10"
    assert [r["file_type"] for r in env["rows"]] == ["8-K", "8-K/A"]


def test_day_cache_idempotent_second_call_zero_fetches(tmp_path):
    get = _ScriptedGet([(200, _payload([_hit("a-1")]))])
    day = date(2026, 6, 3)
    kw = dict(identity="t@example.com", cache_dir=str(tmp_path),
              today=date(2026, 6, 10), throttle_s=0)
    fetch_eightk_day(day, _get=get, **kw)
    poison = _ScriptedGet([])            # any call would IndexError
    rows2 = fetch_eightk_day(day, _get=poison, **kw)
    assert len(rows2) == 1 and poison.calls == []


def test_day_cache_lag_window_refetched_next_day(tmp_path):
    """A day cached the SAME day it happened (EFTS still lagging) is reused intra-day but
    is a MISS on a later day — it may have gained filings once EFTS caught up."""
    day = date(2026, 6, 10)
    kw = dict(identity="t@example.com", cache_dir=str(tmp_path), throttle_s=0)
    get1 = _ScriptedGet([(200, _payload([], total=0))])       # fetched on the day itself
    assert fetch_eightk_day(day, today=day, _get=get1, **kw) == []
    # same run-day: served from cache (the originator + veto share one fetch)
    poison = _ScriptedGet([])
    assert fetch_eightk_day(day, today=day, _get=poison, **kw) == []
    # two days later: the young cache is stale -> refetched, and NOW final
    get2 = _ScriptedGet([(200, _payload([_hit("late-1", file_date="2026-06-10")]))])
    later = day + __import__("datetime").timedelta(days=EFTS_LAG_DAYS)
    rows = fetch_eightk_day(day, today=later, _get=get2, **kw)
    assert len(rows) == 1 and len(get2.calls) == 1
    poison2 = _ScriptedGet([])
    assert len(fetch_eightk_day(day, today=later + __import__("datetime").timedelta(days=5),
                                _get=poison2, **kw)) == 1     # final forever now


def test_window_fully_cached_makes_zero_requests(tmp_path):
    day1, day2 = date(2026, 6, 1), date(2026, 6, 2)
    today = date(2026, 6, 20)
    for d, adsh in ((day1, "a-1"), (day2, "a-2")):
        (tmp_path / f"{d.isoformat()}.json").write_text(json.dumps(
            {"fetched_on": "2026-06-10",
             "rows": [normalize_hit(_hit(adsh, file_date=d.isoformat()))]}))
    poison = _ScriptedGet([])
    rows = fetch_eightk_window(day1, day2, identity="t@example.com",
                               cache_dir=str(tmp_path), today=today,
                               throttle_s=0, _get=poison)
    assert [r["adsh"] for r in rows] == ["a-1", "a-2"] and poison.calls == []


def test_window_partial_cache_fetches_range_and_backfills_day_caches(tmp_path):
    """One uncached day in the chunk -> one ranged fetch; per-day caches are written for
    EVERY day in the chunk (empty days too, so weekends never force a refetch)."""
    today = date(2026, 7, 1)
    hits = [_hit("a-1", file_date="2026-06-01"), _hit("a-3", file_date="2026-06-03")]
    get = _ScriptedGet([(200, _payload(hits))])
    rows = fetch_eightk_window(date(2026, 6, 1), date(2026, 6, 3),
                               identity="t@example.com", cache_dir=str(tmp_path),
                               today=today, throttle_s=0, _get=get)
    assert len(rows) == 2 and len(get.calls) == 1
    assert get.calls[0]["startdt"] == "2026-06-01" and get.calls[0]["enddt"] == "2026-06-03"
    for d, n in (("2026-06-01", 1), ("2026-06-02", 0), ("2026-06-03", 1)):
        env = json.loads((tmp_path / f"{d}.json").read_text())
        assert len(env["rows"]) == n


def test_window_chunks_by_month(tmp_path):
    today = date(2026, 7, 1)
    get = _ScriptedGet([(200, _payload([])), (200, _payload([]))])
    rows = fetch_eightk_window(date(2026, 4, 15), date(2026, 5, 10),
                               identity="t@example.com", cache_dir=str(tmp_path),
                               today=today, throttle_s=0, _get=get)
    assert rows == []
    spans = [(c["startdt"], c["enddt"]) for c in get.calls]
    assert spans == [("2026-04-15", "2026-04-30"), ("2026-05-01", "2026-05-10")]


def test_window_failed_chunk_returns_none(tmp_path):
    get = _ScriptedGet([(500, None), (500, None), (500, None)])
    rows = fetch_eightk_window(date(2026, 6, 1), date(2026, 6, 2),
                               identity="t@example.com", cache_dir=str(tmp_path),
                               today=date(2026, 7, 1), throttle_s=0, _get=get)
    assert rows is None

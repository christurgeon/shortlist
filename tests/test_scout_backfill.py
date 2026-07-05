from datetime import date, timedelta

from shortlist.backtest.prices import PriceHistory
from shortlist.scout.backfill import assemble_events, measure_event, next_trading_day
from shortlist.scout.delisting import FilingRecord
from shortlist.scout.firehose import CohortEvent

SIG = "edgar:activist_13d"


def _rec(cik, subject="Target Corp", activist="Fund LP", acc="a-1",
         fdate=date(2023, 10, 13), form="SCHEDULE 13D"):
    return {"cik": cik, "subject_name": subject, "activist": activist,
            "form": form, "accession": acc, "filing_date": fdate}


def test_next_trading_day_skips_weekend():
    assert next_trading_day(date(2023, 10, 13)) == date(2023, 10, 16)   # Fri -> Mon
    assert next_trading_day(date(2023, 10, 16)) == date(2023, 10, 17)   # Mon -> Tue (STRICT)


def test_resolved_event_carries_entry_shift_key_and_strength():
    evs = assemble_events({date(2023, 10, 13): [_rec("0000886158")]},
                          lambda cik, as_of: "BBBY")
    assert len(evs) == 1
    e = evs[0]
    assert e.signal == SIG and e.ticker == "BBBY" and e.cik == "0000886158"
    assert e.origin == "backfill"
    assert e.event_date == date(2023, 10, 16)                # F12: next session AFTER filing
    assert e.meta["filing_date"] == "2023-10-13"
    assert e.meta["key"] == f"{SIG}|0000886158|2023-10-13"
    assert e.strength is not None and e.strength >= 0.7


def test_spac_and_affiliate_are_excluded_not_selected():
    day = {date(2023, 10, 13): [
        _rec("0000000001", subject="Blank Check Acquisition Corp", acc="a-1"),
        _rec("0000000002", subject="Hawkeye Systems", activist="Hawkeye HoldCo LLC", acc="a-2"),
        _rec("0000000003", subject="Real Business Inc", acc="a-3"),
    ]}
    evs = assemble_events(day, lambda cik, as_of: {"0000000003": "REAL"}.get(cik))
    assert [e.ticker for e in evs] == ["REAL"]               # SPAC + affiliate never selected


def test_unresolved_and_headerless_become_sentinel_events():
    day = {date(2023, 10, 13): [
        _rec("0000000042", acc="a-1"),                        # resolver -> None (delisted, no snap)
        _rec(None, acc="a-2"),                                # header fetch failed upstream
    ]}
    evs = assemble_events(day, lambda cik, as_of: None)
    tickers = sorted(e.ticker for e in evs)
    assert tickers == ["CIK:0000000042", "CIK:unknown-a-2"]
    assert all(e.origin == "backfill" for e in evs)
    # keys stay unique + stable for idempotent resume
    assert evs[0].meta["key"] != evs[1].meta["key"]


def test_cofilers_collapse_to_one_event():
    day = {date(2023, 10, 13): [
        _rec("0000000007", activist="Fund A LP", acc="a-1"),
        _rec("0000000007", activist="Fund B LP", acc="a-2"),
    ]}
    evs = assemble_events(day, lambda cik, as_of: "TGT")
    assert len(evs) == 1 and evs[0].ticker == "TGT"
    assert evs[0].strength > 0.7                              # co-filer bump from the aggregator


def test_resolver_called_with_filing_date_not_entry_date():
    seen = []

    def resolver(cik, as_of):
        seen.append(as_of)
        return "TGT"
    assemble_events({date(2023, 10, 13): [_rec("0000000007")]}, resolver)
    assert seen == [date(2023, 10, 13)]                       # PiT at FILING date


TODAY = date(2026, 7, 1)


def _ev(ticker="TGT", cik="0000000007", edate=date(2022, 8, 1)):
    return CohortEvent(signal=SIG, ticker=ticker, cik=cik, event_date=edate,
                       as_of_price=None, strength=0.7, gated=None, composite=None,
                       origin="backfill", meta={"filing_date": "2022-07-29", "key": "k"})


def _hist(ticker, start, n_days, base=100.0, stop_after=None):
    """Daily synthetic history; stop_after truncates the series (delisting-style)."""
    dates, closes = [], []
    d, i = start, 0
    while len(dates) < n_days:
        if d.weekday() < 5:
            dates.append(d)
            closes.append(base + i * 0.1)
            i += 1
        d = d + timedelta(days=1)
    if stop_after is not None:
        dates, closes = dates[:stop_after], closes[:stop_after]
    return PriceHistory(ticker=ticker, dates=dates, closes=closes, nominal_closes=list(closes))


def test_priced_path_is_measurable_with_entry_price():
    h = _hist("TGT", date(2022, 7, 1), 400)                   # ~19 months of sessions
    ev = measure_event(_ev(), h, 12, today=TODAY, fetch_delisting_records=lambda cik: [])
    assert ev.meta["measurable"] is True
    assert ev.as_of_price is not None


def test_sentinel_and_missing_history_are_non_measurable():
    ev = measure_event(_ev(ticker="CIK:0000000042"), None, 12, today=TODAY,
                       fetch_delisting_records=lambda cik: [])
    assert ev.meta["measurable"] is False
    assert ev.meta["non_measurable_reason"] == "unresolved_ticker"
    ev2 = measure_event(_ev(), None, 12, today=TODAY, fetch_delisting_records=lambda cik: [])
    assert ev2.meta["non_measurable_reason"] == "no_price_series"


def test_immature_event_flagged():
    h = _hist("TGT", date(2026, 5, 1), 30)
    ev = measure_event(_ev(edate=date(2026, 6, 2)), h, 12, today=TODAY,
                       fetch_delisting_records=lambda cik: [])
    assert ev.meta["non_measurable_reason"] == "immature"


def test_classified_bankruptcy_terminal_return():
    # series ends ~3 months after entry; Form 25 (Nasdaq) + 8-K 1.03 in window
    h = _hist("BBBY", date(2022, 7, 1), 70)                   # ends ~2022-10
    recs = [FilingRecord("8-K", date(2022, 9, 20), items=("1.03",)),
            FilingRecord("25-NSE", date(2022, 10, 5), filer="The Nasdaq Stock Market LLC")]
    ev = measure_event(_ev(ticker="BBBY", edate=date(2022, 8, 1)), h, 12, today=TODAY,
                       fetch_delisting_records=lambda cik: recs)
    assert ev.meta["measurable"] is True
    assert ev.meta["delisting_reason"] == "bankruptcy"
    entry = h.close_asof(date(2022, 8, 1))
    from shortlist.scout.delisting import classify_delisting, terminal_price
    expected = terminal_price(classify_delisting(recs), h.dates, h.closes) / entry - 1.0
    assert abs(ev.meta["delisting_event_return"] - expected) < 1e-12


def test_unclassified_termination_and_fetchfail_are_non_measurable():
    h = _hist("GONE", date(2022, 7, 1), 70)
    ev = measure_event(_ev(ticker="GONE", edate=date(2022, 8, 1)), h, 12, today=TODAY,
                       fetch_delisting_records=lambda cik: [])   # no Form 25/15 at all
    assert ev.meta["measurable"] is False
    assert ev.meta["non_measurable_reason"] == "series_ends_no_form25"
    ev2 = measure_event(_ev(ticker="GONE", edate=date(2022, 8, 1)), h, 12, today=TODAY,
                        fetch_delisting_records=lambda cik: None)  # fetch failed
    assert ev2.meta["non_measurable_reason"] == "delisting_fetch_failed"


def test_trading_gap_guard_r_a1():
    # series has data well past horizon but a hole AT the horizon month -> gap, not delisting
    h = _hist("HALT", date(2022, 7, 1), 400)
    horizon = date(2023, 8, 1)
    dates, closes = [], []
    for d, c in zip(h.dates, h.closes):
        if abs((d - horizon).days) <= 20:                     # excise +/-20d around horizon
            continue
        dates.append(d)
        closes.append(c)
    h2 = PriceHistory(ticker="HALT", dates=dates, closes=closes, nominal_closes=list(closes))
    ev = measure_event(_ev(ticker="HALT", edate=date(2022, 8, 1)), h2, 12, today=TODAY,
                       fetch_delisting_records=lambda cik: [])
    assert ev.meta["measurable"] is False
    assert ev.meta["non_measurable_reason"] == "trading_gap"


def test_look_ahead_invariance_post_horizon_corruption():
    h = _hist("TGT", date(2022, 7, 1), 400)
    ev1 = measure_event(_ev(), h, 12, today=TODAY, fetch_delisting_records=lambda cik: [])
    cut = [c if d <= date(2023, 9, 1) else 999.0 for d, c in zip(h.dates, h.closes)]
    h2 = PriceHistory(ticker="TGT", dates=list(h.dates), closes=cut, nominal_closes=cut)
    ev2 = measure_event(_ev(), h2, 12, today=TODAY, fetch_delisting_records=lambda cik: [])
    assert ev1.meta["measurable"] == ev2.meta["measurable"]
    assert ev1.as_of_price == ev2.as_of_price                 # entry untouched by post-horizon data


def test_zero_entry_price_is_non_measurable_never_raises():
    # a (synthetic/bad-data) zero close at entry must not divide-by-zero in the delisting arm
    h = _hist("BAD", date(2022, 7, 1), 70)
    # zero out every close at/before entry
    closes = [0.0 if d <= date(2022, 8, 1) else c for d, c in zip(h.dates, h.closes)]
    h2 = PriceHistory(ticker="BAD", dates=list(h.dates), closes=closes,
                      nominal_closes=list(closes))
    recs = [FilingRecord("8-K", date(2022, 9, 20), items=("1.03",)),
            FilingRecord("25-NSE", date(2022, 10, 5), filer="The Nasdaq Stock Market LLC")]
    ev = measure_event(_ev(ticker="BAD", edate=date(2022, 8, 1)), h2, 12, today=TODAY,
                       fetch_delisting_records=lambda cik: recs)
    assert ev.meta["measurable"] is False
    assert ev.meta["non_measurable_reason"] == "no_entry_price"


def test_missing_cik_reads_no_cik_not_fetch_failed():
    h = _hist("GONE", date(2022, 7, 1), 70)
    ev = measure_event(_ev(ticker="GONE", cik=None, edate=date(2022, 8, 1)), h, 12,
                       today=TODAY, fetch_delisting_records=lambda cik: (_ for _ in ()).throw(
                           AssertionError("fetcher must not be called without a cik")))
    assert ev.meta["non_measurable_reason"] == "no_cik"


from shortlist.scout.backfill import append_events, load_backfill_events, summarize


def test_append_is_idempotent_and_resumable(tmp_path):
    from dataclasses import replace
    p = str(tmp_path / "13d-test.jsonl")
    e1 = replace(_ev(), meta={**_ev().meta, "key": "k1", "measurable": True})
    e2 = replace(_ev(ticker="OTHER"),
                 meta={**_ev().meta, "key": "k2", "measurable": False,
                       "non_measurable_reason": "no_price_series"})
    assert append_events(p, [e1, e2]) == 2
    assert append_events(p, [e1, e2]) == 0                    # resume: nothing re-written
    rows = load_backfill_events(p)
    assert len(rows) == 2 and rows[0]["origin"] == "backfill"
    assert rows[0]["event_date"] == e1.event_date.isoformat()


def test_load_skips_malformed_lines(tmp_path):
    import pytest
    p = tmp_path / "bad.jsonl"
    p.write_text('{"signal": "edgar:activist_13d", "ticker": "A", "meta": {"key": "k"}}\nnot json\n')
    with pytest.warns(UserWarning, match="backfill"):
        rows = load_backfill_events(str(p))
    assert len(rows) == 1


def test_summarize_counts_and_vintages():
    rows = [
        {"event_date": "2022-08-01", "meta": {"measurable": True,
                                              "delisting_reason": "bankruptcy"}},
        {"event_date": "2022-09-01", "meta": {"measurable": False,
                                              "non_measurable_reason": "unresolved_ticker"}},
        {"event_date": "2023-02-01", "meta": {"measurable": True}},
    ]
    s = summarize(rows)
    assert s["n_selected"] == 3 and s["n_measurable"] == 2
    assert abs(s["fraction"] - 2 / 3) < 1e-9
    assert s["by_reason"] == {"unresolved_ticker": 1}
    assert s["by_vintage"][2022] == {"selected": 2, "measurable": 1}
    assert s["by_vintage"][2023] == {"selected": 1, "measurable": 1}
    assert s["delisting_by_reason"] == {"bankruptcy": 1}


def test_unreal_resolved_ticker_becomes_sentinel_not_silently_dropped():
    day = {date(2023, 10, 13): [_rec("0000000042", acc="a-1")]}
    evs = assemble_events(day, lambda cik, as_of: "N/A")   # 'unreal' ticker shape
    assert len(evs) == 1 and evs[0].ticker == "CIK:0000000042"


from shortlist.scout.backfill import run_backfill_13d


def test_run_backfill_end_to_end_with_injected_seams(tmp_path):
    out = str(tmp_path / "13d.jsonl")
    windows = []

    def fake_window(start, end, identity, **kw):
        windows.append((start, end))
        if start != date(2022, 8, 1):
            return []
        return [{"cik": "0000000007", "subject_name": "Target Corp", "activist": "Fund LP",
                 "form": "SCHEDULE 13D", "accession": "a-1", "filing_date": date(2022, 8, 10)},
                {"cik": "0000000042", "subject_name": "Ghost Inc", "activist": "Other LP",
                 "form": "SCHEDULE 13D", "accession": "a-2", "filing_date": date(2022, 8, 10)}]

    class FakeSym:
        low_confidence = []
        disagreements = []

        def resolve_ticker(self, cik, as_of):
            return "TGT" if cik == "0000000007" else None

        def close(self):
            pass

    h = _hist("TGT", date(2022, 7, 1), 400)
    # score_events off here — this test exercises the RAW coordinator wiring (walk / assemble /
    # measure / idempotent append); Task-4 scoring integration has its own dedicated tests below.
    cfg = {"scout": {"backfill": {"out_dir": str(tmp_path), "sec_throttle_s": 0.0,
                                  "yahoo_throttle_s": 0.0, "score_events": False}}}

    hist_calls: list = []
    delist_calls: list = []

    def fetch_history_recording(tkr):
        hist_calls.append(tkr)
        return h if tkr == "TGT" else None

    def fetch_delisting_recording(cik):
        delist_calls.append(cik)
        return []

    summary = run_backfill_13d(cfg, start=date(2022, 8, 1), end=date(2022, 9, 15),
                               identity="t@example.com", today=TODAY, out_path=out,
                               _fetch_window=fake_window, _symbology=FakeSym(),
                               _fetch_history=fetch_history_recording,
                               _fetch_delisting=fetch_delisting_recording)
    assert (date(2022, 8, 1), date(2022, 8, 31)) in windows   # month chunking
    assert (date(2022, 9, 1), date(2022, 9, 15)) in windows
    assert summary["n_selected"] == 2                          # TGT + unresolved sentinel
    assert summary["n_measurable"] == 1
    assert summary["written"] == 2
    assert hist_calls == ["TGT"]                                # first run fetched exactly once

    # resume: second run must skip existing keys BEFORE any fetch — zero calls
    hist_calls.clear()
    delist_calls.clear()
    summary2 = run_backfill_13d(cfg, start=date(2022, 8, 1), end=date(2022, 9, 15),
                                identity="t@example.com", today=TODAY, out_path=out,
                                _fetch_window=fake_window, _symbology=FakeSym(),
                                _fetch_history=fetch_history_recording,
                                _fetch_delisting=fetch_delisting_recording)
    assert summary2["written"] == 0
    assert hist_calls == []
    assert delist_calls == []
    rows = load_backfill_events(out)
    assert len(rows) == 2 and all(r["origin"] == "backfill" for r in rows)


def test_run_backfill_never_touches_scout_state(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import shortlist.scout.state as state_mod
    called = []
    monkeypatch.setattr(state_mod.ScoutState, "record_firehose",
                        lambda self, *a, **k: called.append(1), raising=True)
    out = str(tmp_path / "13d.jsonl")
    # NB: pass a FAKE symbology — _symbology=None makes the coordinator construct a real
    # network-touching Symbology (that is prod behavior, not test behavior).
    fake_sym = SimpleNamespace(resolve_ticker=lambda cik, as_of: None,
                               close=lambda: None, low_confidence=[], disagreements=[])
    run_backfill_13d({"scout": {"backfill": {"sec_throttle_s": 0.0}}},
                     start=date(2022, 8, 1), end=date(2022, 8, 31),
                     identity="t@example.com", today=TODAY, out_path=out,
                     _fetch_window=lambda *a, **k: [], _symbology=fake_sym,
                     _fetch_history=lambda tkr: None, _fetch_delisting=lambda cik: [])
    assert called == []


def test_default_fetch_history_seam_returns_real_pricehistory(tmp_path):
    import httpx

    def handler(request):
        # minimal valid Yahoo chart payload: 3 daily bars
        ts = [1659312000, 1659398400, 1659484800]
        return httpx.Response(200, json={"chart": {"result": [{
            "timestamp": ts,
            "indicators": {"adjclose": [{"adjclose": [10.0, 10.5, 11.0]}],
                           "quote": [{"close": [10.0, 10.5, 11.0]}]}}]}})

    from shortlist.scout.backfill import fetch_history_sync
    h = fetch_history_sync("TGT", identity="t@example.com", today=date(2022, 8, 5),
                           cache_dir=str(tmp_path), _transport=httpx.MockTransport(handler))
    assert h is not None and len(h.dates) == 3 and h.closes[-1] == 11.0


# --- Task 1: fetch_companyfacts_sync + fetch_sic_sync (sync bridges, month-cached) --------

def test_companyfacts_warm_cache_returns_without_client(tmp_path):
    """A pre-populated month cache short-circuits BEFORE any client/transport is touched —
    the same guarantee fetch_history_sync gives for a warm price cache."""
    import json

    import httpx

    from shortlist.backtest.xbrl import _facts_cache_path
    from shortlist.scout.backfill import fetch_companyfacts_sync

    cache_dir = str(tmp_path / "sec_xbrl")
    month = "2026-07"
    cik10 = "0000000007"
    payload = {"facts": {"us-gaap": {"Assets": {"units": {"USD": [{"val": 1}]}}}}}
    cp = _facts_cache_path(cache_dir, cik10, month)
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps(payload))

    def poison(request):
        raise AssertionError("must not touch the network on a warm cache")

    result = fetch_companyfacts_sync(7, identity="t@example.com", cache_dir=cache_dir,
                                     month=month, _transport=httpx.MockTransport(poison))
    assert result == payload                                  # int cik normalized to cik10


def test_companyfacts_cold_path_via_transport_returns_payload(tmp_path):
    """No cache file -> the async bridge fetches via the injected _transport (no network)."""
    import httpx

    from shortlist.backtest.xbrl import _facts_cache_path
    from shortlist.scout.backfill import fetch_companyfacts_sync

    cache_dir = str(tmp_path / "sec_xbrl")
    month = "2026-07"
    payload = {"facts": {"us-gaap": {"Assets": {"units": {"USD": [{"val": 1}]}}}}}

    def handler(request):
        return httpx.Response(200, json=payload)

    result = fetch_companyfacts_sync("0000000042", identity="t@example.com",
                                     cache_dir=cache_dir, month=month,
                                     _transport=httpx.MockTransport(handler))
    assert result == payload
    # written to the SAME path fetch_companyfacts itself would use (shared XBRL-backtest cache)
    assert _facts_cache_path(cache_dir, "0000000042", month).exists()


def test_sic_happy_path(tmp_path):
    import httpx

    from shortlist.scout.backfill import fetch_sic_sync

    def handler(request):
        return httpx.Response(200, json={"sic": "3841"})

    sic = fetch_sic_sync("0000000007", identity="t@example.com",
                         cache_dir=str(tmp_path), month="2026-07",
                         _transport=httpx.MockTransport(handler))
    assert sic == "3841"


def test_sic_200_empty_returns_none_and_is_cached_null(tmp_path):
    """A 200-with-no-sic caches a null so a real negative isn't refetched within the month."""
    import httpx

    from shortlist.scout.backfill import fetch_sic_sync

    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={"sic": ""})

    kw = dict(identity="t@example.com", cache_dir=str(tmp_path), month="2026-07")
    sic1 = fetch_sic_sync("0000000099", _transport=httpx.MockTransport(handler), **kw)
    sic2 = fetch_sic_sync("0000000099", _transport=httpx.MockTransport(handler), **kw)
    assert sic1 is None and sic2 is None
    assert len(calls) == 1                                    # second call served from cache


def test_sic_network_failure_not_cached_and_warns(tmp_path):
    """A network failure warns, returns None, and is NEVER cached (re-attempted next call)."""
    import httpx
    import pytest

    from shortlist.scout.backfill import fetch_sic_sync

    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ConnectError("boom", request=request)

    kw = dict(identity="t@example.com", cache_dir=str(tmp_path), month="2026-07")
    with pytest.warns(UserWarning, match="backfill"):
        sic1 = fetch_sic_sync("0000000055", _transport=httpx.MockTransport(handler), **kw)
    with pytest.warns(UserWarning, match="backfill"):
        sic2 = fetch_sic_sync("0000000055", _transport=httpx.MockTransport(handler), **kw)
    assert sic1 is None and sic2 is None
    assert len(calls) == 2                                    # never cached -> both hit network


def test_companyfacts_none_or_malformed_cik_returns_none_never_raises(tmp_path):
    """fetch_companyfacts_sync degrade gracefully on None/malformed CIK — warn, never raise."""
    import httpx
    import pytest

    from shortlist.scout.backfill import fetch_companyfacts_sync

    def poison(request):
        raise AssertionError("must not touch the network on malformed cik")

    kw = dict(identity="t@example.com", cache_dir=str(tmp_path), month="2026-07",
              _transport=httpx.MockTransport(poison))
    for bad_cik in (None, "not-a-cik", "xyz"):
        with pytest.warns(UserWarning, match="backfill.*malformed"):
            result = fetch_companyfacts_sync(bad_cik, **kw)
        assert result is None


def test_sic_none_or_malformed_cik_returns_none_never_raises(tmp_path):
    """fetch_sic_sync degrade gracefully on None/malformed CIK — warn, never raise."""
    import httpx
    import pytest

    from shortlist.scout.backfill import fetch_sic_sync

    def poison(request):
        raise AssertionError("must not touch the network on malformed cik")

    kw = dict(identity="t@example.com", cache_dir=str(tmp_path), month="2026-07",
              _transport=httpx.MockTransport(poison))
    for bad_cik in (None, "not-a-cik", "xyz"):
        with pytest.warns(UserWarning, match="backfill.*malformed"):
            result = fetch_sic_sync(bad_cik, **kw)
        assert result is None


# --- Task 2: merge_metrics — reflective None-overlay (fundamentals + price legs) --------

from shortlist.models import StockMetrics
from shortlist.scout.backfill import merge_metrics


def test_merge_metrics_disjoint_overlay():
    """Fundamentals-only + price-only → merged has all three fields."""
    fundamentals = StockMetrics(ticker="T", revenue_cagr=0.1)
    price = StockMetrics(ticker="T", price=10.0, realized_vol=0.2)
    merged = merge_metrics(fundamentals, price)
    assert merged.ticker == "T"
    assert merged.revenue_cagr == 0.1
    assert merged.price == 10.0
    assert merged.realized_vol == 0.2


def test_merge_metrics_primary_wins_on_overlap():
    """Both set price → primary's survives."""
    primary = StockMetrics(ticker="T", price=15.0)
    secondary = StockMetrics(ticker="T", price=10.0)
    merged = merge_metrics(primary, secondary)
    assert merged.price == 15.0


def test_merge_metrics_all_none_secondary_returns_primary_unchanged():
    """All-None secondary → primary returned unchanged (identity ok)."""
    primary = StockMetrics(ticker="T", revenue_cagr=0.1)
    secondary = StockMetrics(ticker="T")
    merged = merge_metrics(primary, secondary)
    assert merged.ticker == "T"
    assert merged.revenue_cagr == 0.1
    # Identity check: the function should return primary itself when secondary is all-None
    assert merged is primary


def test_merge_metrics_merges_sources_provenance_primary_wins_per_key():
    """`sources` defaults to {} (never None), so the None-overlay loop never fires for it —
    without an explicit merge, the secondary leg's provenance would be silently dropped."""
    primary = StockMetrics(ticker="T", price=15.0,
                           sources={"price": "primary_src", "only_primary": "p"})
    secondary = StockMetrics(ticker="T", price=10.0,
                             sources={"price": "secondary_src", "only_secondary": "s"})
    merged = merge_metrics(primary, secondary)
    assert merged.sources == {"only_secondary": "s", "only_primary": "p", "price": "primary_src"}


def test_merge_metrics_sources_one_sided_and_both_empty():
    empty = StockMetrics(ticker="T")
    with_sources = StockMetrics(ticker="T", sources={"x": "y"})
    assert merge_metrics(empty, with_sources).sources == {"x": "y"}
    assert merge_metrics(with_sources, empty).sources == {"x": "y"}
    assert merge_metrics(empty, empty).sources == {}


# --- Task 3: score_event — PiT score() reconstruction (design A3 + v2 §4/§5) -------------
#
# Companyfacts fixture pattern REUSED from tests/test_xbrl_signal.py (`_facts_for`) / the
# shared `_row`/`_inst`/`_annual` helpers first introduced in tests/test_xbrl_facts.py —
# the smallest existing fixture shape that `extract_panel` turns into a scoreable panel
# (proven by test_xbrl_signal.py's own `test_xbrl_signal_emits_fundamental_subscores`).

import json
from dataclasses import replace
from pathlib import Path

import yaml

from shortlist import scoring
from shortlist.data.bridge import snapshot_to_metrics
from shortlist.data.sources import snapshot_from_closes_dated
from shortlist.providers._xbrl_facts import extract_panel, panel_to_metrics
from shortlist.scout.backfill import score_event

_SE_CONFIG = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text())


def _se_row(start, end, val, filed, form="10-K"):
    return {"start": start, "end": end, "val": val, "filed": filed, "form": form}


def _se_inst(end, val, filed, form="10-K"):
    return {"end": end, "val": val, "filed": filed, "form": form}


def _se_annual(concept, rows, unit="USD"):
    return {concept: {"units": {unit: rows}}}


def _se_facts():
    """3 fiscal years (2020-2022) of revenue/net-income/OCF/capex/gross-profit/EPS/equity/
    shares — the same minimal shape as test_xbrl_signal.py's `_facts_for`, filed through
    2023-02-01 (i.e. strictly before an as_of of 2023-06-01)."""
    gaap = {}
    gaap.update(_se_annual("Revenues", [
        _se_row("2020-01-01", "2020-12-31", 1000, "2021-02-01"),
        _se_row("2021-01-01", "2021-12-31", 1100, "2022-02-01"),
        _se_row("2022-01-01", "2022-12-31", 1300, "2023-02-01")]))
    gaap.update(_se_annual("NetIncomeLoss", [
        _se_row("2020-01-01", "2020-12-31", 100, "2021-02-01"),
        _se_row("2021-01-01", "2021-12-31", 120, "2022-02-01"),
        _se_row("2022-01-01", "2022-12-31", 160, "2023-02-01")]))
    gaap.update(_se_annual("GrossProfit", [
        _se_row("2020-01-01", "2020-12-31", 500, "2021-02-01"),
        _se_row("2021-01-01", "2021-12-31", 560, "2022-02-01"),
        _se_row("2022-01-01", "2022-12-31", 700, "2023-02-01")]))
    gaap.update(_se_annual("NetCashProvidedByUsedInOperatingActivities", [
        _se_row("2022-01-01", "2022-12-31", 240, "2023-02-01")]))
    gaap.update(_se_annual("PaymentsToAcquirePropertyPlantAndEquipment", [
        _se_row("2022-01-01", "2022-12-31", 40, "2023-02-01")]))
    gaap.update(_se_annual("EarningsPerShareDiluted", [
        _se_row("2021-01-01", "2021-12-31", 2.6, "2022-02-01"),
        _se_row("2022-01-01", "2022-12-31", 3.2, "2023-02-01")], unit="USD/shares"))
    gaap.update(_se_annual("StockholdersEquity", [_se_inst("2022-12-31", 600, "2023-02-01")]))
    return {"facts": {"us-gaap": gaap,
                      "dei": _se_annual("EntityCommonStockSharesOutstanding",
                                        [_se_inst("2022-12-31", 10, "2023-02-01")],
                                        unit="shares")}}


AS_OF = date(2023, 6, 1)
_SE_ENTRY = date(2023, 6, 5)   # F12-shifted entry day: AFTER as_of, well within the hist window


def _se_ev(ticker="TST", cik="0000000007", filing_date=AS_OF, event_date=_SE_ENTRY):
    return CohortEvent(signal=SIG, ticker=ticker, cik=cik, event_date=event_date,
                       as_of_price=None, strength=0.7, gated=None, composite=None,
                       origin="backfill",
                       meta={"filing_date": filing_date.isoformat(), "key": "se-k"})


def _se_hist(ticker="TST", base=100.0):
    # 1000 weekday closes from 2020-01-01 comfortably spans well past AS_OF (~2023-06),
    # giving room for post-as_of / post-entry PiT-corruption tests.
    return _hist(ticker, date(2020, 1, 1), 1000, base=base)


def _se_reconstruct(ev, hist, facts, spy, sic, config):
    """Manual reconstruction of score_event's chain, for self-consistency assertions —
    NOT a duplicate of the implementation under test, but the independent oracle the
    brief requires (compare against a direct scoring.score() call, not a magic number)."""
    as_of = date.fromisoformat(ev.meta["filing_date"])
    panel = extract_panel(facts, as_of)
    price = hist.nominal_close_asof(as_of)
    price_at = lambda d: hist.nominal_price_on(d) if d <= as_of else None
    m1 = panel_to_metrics(panel, ticker=ev.ticker, sic=sic, price=price, price_at=price_at)
    dates, closes = hist.through(as_of)
    spy_d, spy_c = spy.through(as_of) if spy is not None else ([], [])
    m2 = snapshot_to_metrics(snapshot_from_closes_dated(ev.ticker, dates, closes, spy_d, spy_c))
    card = scoring.score(merge_metrics(m1, m2), config)
    return bool(card.gates), card.composite


def test_score_event_happy_path_matches_direct_reconstruction():
    facts = _se_facts()
    h = _se_hist()
    spy = _se_hist("SPY", base=300.0)
    ev = _se_ev()
    gated, composite = score_event(ev, h, facts, spy, "3711", _SE_CONFIG)
    assert gated in (True, False)
    assert isinstance(composite, float)
    exp_gated, exp_composite = _se_reconstruct(ev, h, facts, spy, "3711", _SE_CONFIG)
    assert gated == exp_gated
    assert composite == exp_composite


def test_score_event_none_when_facts_or_hist_missing():
    ev = _se_ev()
    h = _se_hist()
    spy = _se_hist("SPY", base=300.0)
    assert score_event(ev, h, None, spy, None, _SE_CONFIG) == (None, None)
    assert score_event(ev, None, _se_facts(), spy, None, _SE_CONFIG) == (None, None)


def test_score_event_uses_filing_date_never_event_date():
    """v2 §4 — as_of MUST be meta['filing_date'], never ev.event_date. A malformed
    ev.meta without 'filing_date' hits the try/except -> (None, None), never raises,
    proving event_date is never silently substituted in."""
    ev = _se_ev()
    bad = replace(ev, meta={"key": "se-k"})            # no filing_date at all
    h = _se_hist()
    spy = _se_hist("SPY", base=300.0)
    with __import__("pytest").warns(UserWarning, match="backfill"):
        assert score_event(bad, h, _se_facts(), spy, None, _SE_CONFIG) == (None, None)


def test_score_event_pit_invariance_trio():
    facts = _se_facts()
    h = _se_hist()
    spy = _se_hist("SPY", base=300.0)
    ev = _se_ev()
    baseline = score_event(ev, h, facts, spy, "3711", _SE_CONFIG)

    # (a) closes corrupted STRICTLY AFTER as_of -> identical result.
    cut = [c if d <= AS_OF else 999.0 for d, c in zip(h.dates, h.closes)]
    h_after = PriceHistory(ticker="TST", dates=list(h.dates), closes=cut,
                           nominal_closes=cut)
    assert score_event(ev, h_after, facts, spy, "3711", _SE_CONFIG) == baseline

    # (b) a facts row with filed > as_of (huge revenue) -> identical result (extract_panel
    # already excludes it; this pins that score_event's as_of choice keeps it excluded).
    facts_leaked = json.loads(json.dumps(facts))     # deep copy
    facts_leaked["facts"]["us-gaap"]["Revenues"]["units"]["USD"].append(
        _se_row("2023-01-01", "2023-12-31", 10_000_000, "2023-06-02"))
    assert score_event(ev, h, facts_leaked, spy, "3711", _SE_CONFIG) == baseline

    # (c) closes corrupted specifically in (filing_date, entry] -- the F12 announcement-pop
    # window. Using event_date (rather than filing_date) as as_of would pull this window
    # into the price legs and change the result; using filing_date must not.
    cut2 = [c if not (AS_OF < d <= _SE_ENTRY) else 12345.0 for d, c in zip(h.dates, h.closes)]
    h_leak_window = PriceHistory(ticker="TST", dates=list(h.dates), closes=cut2,
                                 nominal_closes=cut2)
    assert score_event(ev, h_leak_window, facts, spy, "3711", _SE_CONFIG) == baseline


def test_score_event_passes_sic_through(monkeypatch):
    """SIC pass-through: assert via a monkeypatched panel_to_metrics spy that the sic kwarg
    is threaded through unchanged (not pinning sector-masking behavior itself)."""
    from shortlist.providers import _xbrl_facts as xf
    seen = []
    orig = xf.panel_to_metrics

    def spy_fn(p, *, ticker, sic, price, price_at):
        seen.append(sic)
        return orig(p, ticker=ticker, sic=sic, price=price, price_at=price_at)

    monkeypatch.setattr(xf, "panel_to_metrics", spy_fn)
    facts = _se_facts()
    h = _se_hist()
    spy = _se_hist("SPY", base=300.0)
    ev = _se_ev()
    score_event(ev, h, facts, spy, "6022", _SE_CONFIG)     # financials SIC
    score_event(ev, h, facts, spy, None, _SE_CONFIG)
    assert seen == ["6022", None]


def test_score_event_pins_price_at_clamp(monkeypatch):
    """price_at clamp — callback must not reach past as_of (forward tolerance guard).
    Removing the clamp in backfill.py line 491 would fail this test."""
    from shortlist.providers import _xbrl_facts as xf
    captured = {}
    orig = xf.panel_to_metrics

    def spy_fn(p, *, ticker, sic, price, price_at):
        captured["price_at"] = price_at
        return orig(p, ticker=ticker, sic=sic, price=price, price_at=price_at)

    monkeypatch.setattr(xf, "panel_to_metrics", spy_fn)
    facts = _se_facts()
    h = _se_hist()
    spy = _se_hist("SPY", base=300.0)
    ev = _se_ev()
    score_event(ev, h, facts, spy, "3711", _SE_CONFIG)

    # Extract the captured price_at callback
    price_at = captured["price_at"]

    # Past as_of must be clamped to None
    assert price_at(AS_OF + timedelta(days=1)) is None, "price_at must clamp future dates"

    # At as_of must return a real close (not None)
    assert price_at(AS_OF) is not None, "price_at must return close at/before as_of"


# --- Task 4: run_backfill_13d integration — score_events flag, SPY-once, prereg window -----

class _FakeSym4:
    """Minimal injectable Symbology: resolves cik7 -> TST, cik99 -> OTHR, else None."""
    low_confidence: list = []
    disagreements: list = []

    def resolve_ticker(self, cik, as_of):
        return {"0000000007": "TST", "0000000099": "OTHR"}.get(cik)

    def close(self):
        pass


def _t4_window_3(start, end, identity, **kw):
    """One scoreable event (cik7/TST), one facts-less resolved event (cik99/OTHR), one
    unresolved sentinel (cik123) — all filed on AS_OF (2023-06-01), the Task-3 fixture date."""
    if start != date(2023, 6, 1):
        return []
    return [_rec("0000000007", acc="a-1", fdate=AS_OF),
           _rec("0000000099", acc="a-2", fdate=AS_OF),
           _rec("0000000123", acc="a-3", fdate=AS_OF)]


def test_run_backfill_scores_events_when_flag_on(tmp_path):
    """score_events on (default): the scoreable event (hist + facts + sic) gains a real
    gated/composite pair; the facts-less resolved event stays None despite having hist
    (n_sic_missing counts its failed sic fetch); the sentinel never even attempts a fetch."""
    out = str(tmp_path / "13d.jsonl")
    hist_calls: list = []
    facts_calls: list = []
    sic_calls: list = []

    def fake_hist(tkr):
        hist_calls.append(tkr)
        if tkr == "SPY":
            return _se_hist("SPY", base=300.0)
        if tkr == "TST":
            return _se_hist()
        if tkr == "OTHR":
            return _hist("OTHR", date(2020, 1, 1), 1000, base=50.0)
        return None

    def fake_facts(cik):
        facts_calls.append(cik)
        return _se_facts() if cik == "0000000007" else None

    def fake_sic(cik):
        sic_calls.append(cik)
        return "3711" if cik == "0000000007" else None

    import copy
    cfg = copy.deepcopy(_SE_CONFIG)                             # score_event scores through
    cfg.setdefault("scout", {})["backfill"] = {
        "out_dir": str(tmp_path), "sec_throttle_s": 0.0, "yahoo_throttle_s": 0.0,
        "score_events": True}
    summary = run_backfill_13d(cfg, start=date(2023, 6, 1), end=date(2023, 6, 30),
                               identity="t@example.com", today=TODAY, out_path=out,
                               _fetch_window=_t4_window_3, _symbology=_FakeSym4(),
                               _fetch_history=fake_hist, _fetch_delisting=lambda cik: [],
                               _fetch_facts=fake_facts, _fetch_sic=fake_sic)

    # SPY fetched exactly once for the whole run, ahead of any per-event fetch.
    assert hist_calls[0] == "SPY"
    assert hist_calls.count("SPY") == 1
    assert set(hist_calls) == {"SPY", "TST", "OTHR"}          # sentinel never fetched

    # facts/sic only attempted for the two non-sentinel events with hist.
    assert sorted(facts_calls) == ["0000000007", "0000000099"]
    assert sorted(sic_calls) == ["0000000007", "0000000099"]

    rows = load_backfill_events(out)
    by_ticker = {r["ticker"]: r for r in rows}
    assert set(by_ticker) == {"TST", "OTHR", "CIK:0000000123"}

    tst = by_ticker["TST"]
    assert tst["gated"] in (True, False)
    assert isinstance(tst["composite"], float)
    exp_gated, exp_composite = _se_reconstruct(_se_ev(ticker="TST"), _se_hist(), _se_facts(),
                                               _se_hist("SPY", base=300.0), "3711", _SE_CONFIG)
    assert tst["gated"] == exp_gated
    assert tst["composite"] == exp_composite

    othr = by_ticker["OTHR"]                                   # facts-less: hist present, no facts
    assert othr["gated"] is None and othr["composite"] is None

    sentinel = by_ticker["CIK:0000000123"]
    assert sentinel["gated"] is None and sentinel["composite"] is None

    assert summary["n_scored"] == 1                            # only TST
    assert summary["n_sic_missing"] == 1                       # only OTHR's sic fetch failed


def test_run_backfill_score_events_off_is_byte_identical_except_scored_fields(tmp_path):
    """score_events: false regression pin — every field on every written row is identical
    to the score_events: true run, except gated/composite (which stay None off)."""
    def fake_hist(tkr):
        if tkr == "SPY":
            return _se_hist("SPY", base=300.0)
        if tkr == "TST":
            return _se_hist()
        if tkr == "OTHR":
            return _hist("OTHR", date(2020, 1, 1), 1000, base=50.0)
        return None

    def fake_facts(cik):
        return _se_facts() if cik == "0000000007" else None

    def fake_sic(cik):
        return "3711" if cik == "0000000007" else None

    out_on = str(tmp_path / "on.jsonl")
    out_off = str(tmp_path / "off.jsonl")
    base_kw = dict(start=date(2023, 6, 1), end=date(2023, 6, 30), identity="t@example.com",
                  today=TODAY, _fetch_window=_t4_window_3, _fetch_delisting=lambda cik: [])

    import copy
    cfg_on = copy.deepcopy(_SE_CONFIG)                          # full config -> real scores
    cfg_on.setdefault("scout", {})["backfill"] = {
        "sec_throttle_s": 0.0, "yahoo_throttle_s": 0.0, "score_events": True}
    cfg_off = copy.deepcopy(_SE_CONFIG)
    cfg_off.setdefault("scout", {})["backfill"] = {
        "sec_throttle_s": 0.0, "yahoo_throttle_s": 0.0, "score_events": False}

    run_backfill_13d(cfg_on, out_path=out_on, _symbology=_FakeSym4(),
                     _fetch_history=fake_hist, _fetch_facts=fake_facts, _fetch_sic=fake_sic,
                     **base_kw)
    run_backfill_13d(cfg_off, out_path=out_off, _symbology=_FakeSym4(),
                     _fetch_history=fake_hist, **base_kw)

    rows_on = load_backfill_events(out_on)
    rows_off = load_backfill_events(out_off)
    assert len(rows_on) == len(rows_off) == 3
    by_key_on = {r["meta"]["key"]: r for r in rows_on}
    by_key_off = {r["meta"]["key"]: r for r in rows_off}
    assert set(by_key_on) == set(by_key_off)
    for k, on_row in by_key_on.items():
        off_row = by_key_off[k]
        assert off_row["gated"] is None and off_row["composite"] is None
        assert {**on_row, "gated": None, "composite": None} == off_row


def test_run_backfill_prereg_window_mismatch_warns_and_flags(tmp_path):
    import pytest

    def window_one(start, end, identity, **kw):
        if start != date(2023, 6, 1):
            return []
        return [_rec("0000000007", acc="a-1", fdate=date(2023, 6, 1))]

    out = str(tmp_path / "13d.jsonl")
    prereg = {"k_months": 12, "window_start": "2020-01-01", "window_end": "2020-12-31"}
    with pytest.warns(UserWarning, match="pre-registered"):
        summary = run_backfill_13d(
            {"scout": {"backfill": {"sec_throttle_s": 0.0, "yahoo_throttle_s": 0.0,
                                    "score_events": False}}},
            start=date(2023, 6, 1), end=date(2023, 6, 30), identity="t@example.com",
            today=TODAY, out_path=out, _fetch_window=window_one, _symbology=_FakeSym4(),
            _fetch_history=lambda tkr: None, _fetch_delisting=lambda cik: [], _prereg=prereg)
    assert summary["window_not_preregistered"] is True
    rows = load_backfill_events(out)
    assert rows and all(r["meta"].get("window_not_preregistered") is True for r in rows)


def test_run_backfill_prereg_window_keys_absent_is_back_compat_noop(tmp_path):
    """Pre-Task-6 prereg yaml (no window_start/window_end) -> no warning, no summary flag,
    no meta stamp — the CLI window is never compared against a window that isn't registered."""
    def window_one(start, end, identity, **kw):
        if start != date(2023, 6, 1):
            return []
        return [_rec("0000000007", acc="a-1", fdate=date(2023, 6, 1))]

    out = str(tmp_path / "13d.jsonl")
    prereg = {"k_months": 12}                                   # no window keys at all
    summary = run_backfill_13d(
        {"scout": {"backfill": {"sec_throttle_s": 0.0, "yahoo_throttle_s": 0.0,
                                "score_events": False}}},
        start=date(2023, 6, 1), end=date(2023, 6, 30), identity="t@example.com",
        today=TODAY, out_path=out, _fetch_window=window_one, _symbology=_FakeSym4(),
        _fetch_history=lambda tkr: None, _fetch_delisting=lambda cik: [], _prereg=prereg)
    assert "window_not_preregistered" not in summary
    rows = load_backfill_events(out)
    assert rows and all("window_not_preregistered" not in r["meta"] for r in rows)


def test_run_backfill_malformed_prereg_window_degrades_loudly(tmp_path):
    """Malformed window_start/window_end (not-a-date) -> warns and continues, run completes,
    no window_not_preregistered flag in summary (treated as unregistered, not mismatched)."""
    import pytest

    def window_one(start, end, identity, **kw):
        if start != date(2023, 6, 1):
            return []
        return [_rec("0000000007", acc="a-1", fdate=date(2023, 6, 1))]

    out = str(tmp_path / "13d.jsonl")
    prereg = {"k_months": 12, "window_start": "not-a-date", "window_end": "2023-06-30"}
    with pytest.warns(UserWarning, match="malformed prereg window"):
        summary = run_backfill_13d(
            {"scout": {"backfill": {"sec_throttle_s": 0.0, "yahoo_throttle_s": 0.0,
                                    "score_events": False}}},
            start=date(2023, 6, 1), end=date(2023, 6, 30), identity="t@example.com",
            today=TODAY, out_path=out, _fetch_window=window_one, _symbology=_FakeSym4(),
            _fetch_history=lambda tkr: None, _fetch_delisting=lambda cik: [], _prereg=prereg)
    assert "window_not_preregistered" not in summary  # malformed = unregistered, not mismatched
    rows = load_backfill_events(out)
    assert rows  # run completed successfully
    assert all("window_not_preregistered" not in r["meta"] for r in rows)

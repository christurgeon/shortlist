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

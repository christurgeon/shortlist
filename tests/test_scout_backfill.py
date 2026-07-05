from datetime import date

from shortlist.scout.backfill import assemble_events, next_trading_day

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

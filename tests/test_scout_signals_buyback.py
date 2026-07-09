"""EdgarBuybackSignal (scout/signals.py): registration, walk-back + phrase loop, seen-accession
dedup, file_date-desc cap with named overflow, error degradation + redaction."""
from datetime import date

from shortlist.scout.signals import EdgarBuybackSignal, build_signals


def _row(adsh, cik="0000000007", file_date="2026-07-03", file_type="8-K",
         items=("8.01",), sics=("3571",), names=("Real Business Inc",)):
    return {"adsh": adsh, "cik": cik, "file_date": file_date, "file_type": file_type,
            "items": list(items), "sics": list(sics), "display_names": list(names)}


def test_registered_and_named():
    sig = build_signals(["edgar_buyback"], {"edgar_buyback": {"identity": "x@y.z"}})[0]
    assert isinstance(sig, EdgarBuybackSignal)
    assert sig.name == "edgar_buyback" and sig.is_discovery is True


def test_scan_walks_back_per_phrase_and_emits(monkeypatch):
    import shortlist.data.efts as efts
    sig = EdgarBuybackSignal(identity="x@y.z", phrases=["p1", "p2"])
    sig._resolver = {"0000000007": "RBI"}
    calls = []

    def fake(phrase, day, *, identity, **kw):
        calls.append((phrase, day))
        # only the earliest day of the first phrase carries a hit
        return [_row("a-1")] if (phrase == "p1" and day == date(2026, 7, 1)) else []

    monkeypatch.setattr(efts, "fetch_phrase_day", fake)
    ems = sig.scan(date(2026, 7, 3))
    assert [e.ticker for e in ems] == ["RBI"]
    assert ems[0].signal == "edgar:buyback_auth"
    assert ems[0].meta["adsh"] == "a-1" and ems[0].meta["phrase"] == "p1"
    # both phrases walked session-2..session
    assert (("p1", date(2026, 7, 1)) in calls) and (("p2", date(2026, 7, 3)) in calls)
    assert len(calls) == 2 * 3
    assert sig.new_accessions == ["a-1"]


def test_scan_seen_accessions_deduped_across_runs(monkeypatch):
    import shortlist.data.efts as efts

    def fake(phrase, day, *, identity, **kw):
        if phrase == "p1" and day == date(2026, 7, 3):
            return [_row("a-1"), _row("a-2", cik="0000000008")]
        return []

    monkeypatch.setattr(efts, "fetch_phrase_day", fake)
    sig = EdgarBuybackSignal(identity="x@y.z", phrases=["p1"], seen_accessions=["a-1"])
    sig._resolver = {"0000000007": "RBI", "0000000008": "OTHR"}
    ems = sig.scan(date(2026, 7, 3))
    assert [e.meta["adsh"] for e in ems] == ["a-2"]
    assert sig.new_accessions == ["a-2"]


def test_scan_file_date_desc_cap_with_named_overflow(monkeypatch):
    import shortlist.data.efts as efts
    # three distinct filings on three different dates, one phrase, one day of walk-back
    rows = [_row("old", cik="0000000001", file_date="2026-07-01"),
            _row("mid", cik="0000000002", file_date="2026-07-02"),
            _row("new", cik="0000000003", file_date="2026-07-03")]

    def fake(phrase, day, *, identity, **kw):
        return rows if day == date(2026, 7, 3) else []

    monkeypatch.setattr(efts, "fetch_phrase_day", fake)
    sig = EdgarBuybackSignal(identity="x@y.z", phrases=["p1"], daily_cap=2, lookback_days=0)
    sig._resolver = {"0000000001": "OLD", "0000000002": "MID", "0000000003": "NEW"}
    ems = sig.scan(date(2026, 7, 3))
    # freshest first, cap 2 -> NEW + MID kept, OLD overflows
    assert [e.ticker for e in ems] == ["NEW", "MID"]
    assert sig.new_accessions == ["new", "mid"]
    ran, detail = sig.available()
    assert ran is True and "overflow past cap: OLD" in detail


def test_scan_failed_day_degrades_with_honest_status(monkeypatch):
    import shortlist.data.efts as efts

    def fake(phrase, day, *, identity, **kw):
        return None if day == date(2026, 7, 2) else [_row("a-1")]

    monkeypatch.setattr(efts, "fetch_phrase_day", fake)
    sig = EdgarBuybackSignal(identity="x@y.z", phrases=["p1"])
    sig._resolver = {"0000000007": "RBI"}
    ems = sig.scan(date(2026, 7, 3))
    assert [e.ticker for e in ems] == ["RBI"]
    ran, detail = sig.available()
    assert ran is False and "2026-07-02" in detail


def test_scan_persists_suppressed_sibling_accession(monkeypatch):
    """Two same-ticker-same-day accessions -> ONE emission, but the persisted seen-set
    (new_accessions) contains BOTH — so the losing sibling can never win a later (unstable
    relevance-order) run and double-emit the same authorization."""
    import shortlist.data.efts as efts
    rows = [_row("a-1", cik="0000000007", file_date="2026-07-03"),
            _row("a-2", cik="0000000007", file_date="2026-07-03")]   # same ticker + day

    def fake(phrase, day, *, identity, **kw):
        return rows if day == date(2026, 7, 3) else []

    monkeypatch.setattr(efts, "fetch_phrase_day", fake)
    sig = EdgarBuybackSignal(identity="x@y.z", phrases=["p1"], lookback_days=0)
    sig._resolver = {"0000000007": "RBI"}
    ems = sig.scan(date(2026, 7, 3))
    assert [e.ticker for e in ems] == ["RBI"]            # one emission
    assert sorted(sig.new_accessions) == ["a-1", "a-2"]  # BOTH persisted as seen


def test_scan_empty_resolver_guard_skips_and_resets(monkeypatch):
    """An empty CIK->ticker resolver must skip the scan LOUDLY: status False, no emissions,
    NO recorded new_accessions, and the memoized resolver dropped so the retry is real."""
    import shortlist.data.efts as efts
    import shortlist.scout.cik_tickers as ct

    monkeypatch.setattr(ct, "load_cik_to_ticker", lambda *a, **k: {})   # resolver loads empty

    def boom(*a, **k):  # EFTS must never be reached once the guard fires
        raise AssertionError("EFTS fetched despite an empty resolver")

    monkeypatch.setattr(efts, "fetch_phrase_day", boom)
    sig = EdgarBuybackSignal(identity="x@y.z", phrases=["p1"])
    ems = sig.scan(date(2026, 7, 3))
    assert ems == [] and sig.new_accessions == []
    assert sig._resolver is None                          # dropped -> real retry next session
    ran, detail = sig.available()
    assert ran is False and "resolver empty" in detail


def test_degrades_on_error_and_redacts(monkeypatch):
    import shortlist.data.efts as efts

    def boom(*a, **k):
        raise RuntimeError("EFTS 500 https://efts.sec.gov/x?token=SECRET")

    monkeypatch.setattr(efts, "fetch_phrase_day", boom)
    sig = EdgarBuybackSignal(identity="x@y.z", phrases=["p1"])
    sig._resolver = {}
    assert sig.scan(date(2026, 7, 3)) == []
    ran, detail = sig.available()
    assert ran is False and "SECRET" not in detail

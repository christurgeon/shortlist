from datetime import date

from shortlist.scout.signals import EdgarEightKSignal, build_signals


def _rows_for(day_rows):
    """fetch_eightk_day fake: day_rows maps iso-date -> list[row] (None = failed day)."""
    def fake(day, *, identity, cache_dir, **kw):
        return day_rows.get(day.isoformat(), [])
    return fake


def _row(adsh, cik="0000000007", items=("1.01", "3.03"), file_date="2026-07-03"):
    return {"adsh": adsh, "cik": cik, "items": list(items), "file_date": file_date,
            "file_type": "8-K", "sics": ["3571"], "display_names": ["Real Business Inc"]}


def test_registered_and_named():
    sig = build_signals(["edgar_8k"], {"edgar_8k": {"identity": "x@y.z"}})[0]
    assert isinstance(sig, EdgarEightKSignal)
    assert sig.name == "edgar_8k"
    assert sig.is_discovery is True


def test_scan_walks_back_three_days_and_emits(monkeypatch):
    import shortlist.data.efts as efts
    sig = EdgarEightKSignal(identity="x@y.z")
    sig._resolver = {"0000000007": "RBI"}          # skip the network resolver load
    calls = []

    def fake(day, *, identity, cache_dir, **kw):
        calls.append(day)
        return [_row("a-1")] if day == date(2026, 7, 1) else []

    monkeypatch.setattr(efts, "fetch_eightk_day", fake)
    ems = sig.scan(date(2026, 7, 3))
    assert [e.ticker for e in ems] == ["RBI"]
    assert ems[0].signal == "edgar:8k"
    assert ems[0].meta["adsh"] == "a-1"
    assert calls == [date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)]  # today-2..today
    ran, detail = sig.available()
    assert ran is True and "1" in detail
    assert sig.new_accessions == ["a-1"]


def test_scan_seen_accessions_deduped_across_runs(monkeypatch):
    import shortlist.data.efts as efts
    monkeypatch.setattr(efts, "fetch_eightk_day",
                        _rows_for({"2026-07-03": [_row("a-1"), _row("a-2", cik="0000000008")]}))
    sig = EdgarEightKSignal(identity="x@y.z", seen_accessions=["a-1"])
    sig._resolver = {"0000000007": "RBI", "0000000008": "OTHR"}
    ems = sig.scan(date(2026, 7, 3))
    assert [e.meta["adsh"] for e in ems] == ["a-2"]   # a-1 already surfaced a prior run
    assert sig.new_accessions == ["a-2"]


def test_scan_daily_cap(monkeypatch):
    import shortlist.data.efts as efts
    rows = [_row(f"a-{i}", cik=f"{i:010d}") for i in range(10)]
    monkeypatch.setattr(efts, "fetch_eightk_day", _rows_for({"2026-07-03": rows}))
    sig = EdgarEightKSignal(identity="x@y.z", daily_cap=3)
    sig._resolver = {f"{i:010d}": f"TK{i}" for i in range(10)}
    ems = sig.scan(date(2026, 7, 3))
    assert len(ems) == 3
    assert len(sig.new_accessions) == 3               # only surfaced accessions are marked seen


def test_scan_failed_day_degrades_with_honest_status(monkeypatch):
    import shortlist.data.efts as efts

    def fake(day, *, identity, cache_dir, **kw):
        return None if day == date(2026, 7, 2) else [_row("a-1")]

    monkeypatch.setattr(efts, "fetch_eightk_day", fake)
    sig = EdgarEightKSignal(identity="x@y.z")
    sig._resolver = {"0000000007": "RBI"}
    ems = sig.scan(date(2026, 7, 3))
    assert [e.ticker for e in ems] == ["RBI"]         # surviving days still emit
    ran, detail = sig.available()
    assert ran is False and "2026-07-02" in detail


def test_degrades_on_error_and_redacts(monkeypatch):
    import shortlist.data.efts as efts

    def boom(*a, **k):
        raise RuntimeError("EFTS 500 https://efts.sec.gov/x?token=SECRET")

    monkeypatch.setattr(efts, "fetch_eightk_day", boom)
    sig = EdgarEightKSignal(identity="x@y.z")
    # populated resolver: the empty-resolver guard must not short-circuit before the fetch
    sig._resolver = {"0000000007": "RBI"}
    assert sig.scan(date(2026, 7, 3)) == []
    ran, detail = sig.available()
    assert ran is False and "SECRET" not in detail


def test_scan_empty_resolver_guard_skips_and_resets(monkeypatch):
    """An empty CIK->ticker resolver must skip the scan LOUDLY: status False, no emissions,
    NO recorded new_accessions, and the memoized resolver dropped so the retry is real
    (mirrors the buyback originator's guard)."""
    import shortlist.data.efts as efts
    import shortlist.scout.cik_tickers as ct

    monkeypatch.setattr(ct, "load_cik_to_ticker", lambda *a, **k: {})   # resolver loads empty

    def boom(*a, **k):  # EFTS must never be reached once the guard fires
        raise AssertionError("EFTS fetched despite an empty resolver")

    monkeypatch.setattr(efts, "fetch_eightk_day", boom)
    sig = EdgarEightKSignal(identity="x@y.z")
    ems = sig.scan(date(2026, 7, 3))
    assert ems == [] and sig.new_accessions == []
    assert sig._resolver is None                          # dropped -> real retry next session
    ran, detail = sig.available()
    assert ran is False and "resolver empty" in detail

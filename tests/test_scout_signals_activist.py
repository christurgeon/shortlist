from datetime import date

from shortlist.scout.signals import EdgarActivist13DSignal, build_signals


def test_registered_and_named():
    sig = build_signals(["edgar_activist_13d"],
                        {"edgar_activist_13d": {"identity": "x@y.z"}})[0]
    assert isinstance(sig, EdgarActivist13DSignal)
    assert sig.name == "edgar_activist_13d"
    assert sig.is_discovery is True


def test_scan_success(monkeypatch):
    sig = EdgarActivist13DSignal(identity="x@y.z")
    sig._resolver = {"0000000001": "XYZ"}  # skip network load
    import shortlist.scout.edgar_index as ei

    def fake_fetch(session, cap, identity, resolve, **kw):
        return ([{"ticker": "XYZ", "cik": "0000000001", "subject_name": "XYZ Corp",
                  "activist": "Elliott Investment Management L.P.", "form": "SCHEDULE 13D",
                  "accession": "a1"}], session)

    monkeypatch.setattr(ei, "fetch_recent_activist_records", fake_fetch)
    ems = sig.scan(date(2026, 6, 18))
    assert [e.ticker for e in ems] == ["XYZ"]
    assert ems[0].signal == "edgar:activist_13d"
    ran, detail = sig.available()
    assert ran is True and "1" in detail


def test_degrades_on_error(monkeypatch):
    sig = EdgarActivist13DSignal(identity="x@y.z")
    # populated resolver: the empty-resolver guard must not short-circuit before the fetch
    sig._resolver = {"0000000001": "XYZ"}
    import shortlist.scout.edgar_index as ei

    def boom(*a, **k):
        raise RuntimeError("SEC 500 https://sec.gov/x?token=SECRET")

    monkeypatch.setattr(ei, "fetch_recent_activist_records", boom)
    ems = sig.scan(date(2026, 6, 18))
    assert ems == []
    ran, detail = sig.available()
    assert ran is False
    assert "SECRET" not in detail  # redact_secrets applied


def test_scan_empty_resolver_guard_skips_and_resets(monkeypatch):
    """An empty CIK->ticker resolver would abstain on EVERY subject, so a broken resolver
    reads as a quiet day and filings age out of the walk-back window unemitted. The scan
    must skip LOUDLY and drop the memoized resolver so the retry is real (mirrors the
    buyback originator's guard)."""
    import shortlist.scout.cik_tickers as ct
    import shortlist.scout.edgar_index as ei

    monkeypatch.setattr(ct, "load_cik_to_ticker", lambda *a, **k: {})   # resolver loads empty

    def boom(*a, **k):  # the index must never be fetched once the guard fires
        raise AssertionError("index fetched despite an empty resolver")

    monkeypatch.setattr(ei, "fetch_recent_activist_records", boom)
    sig = EdgarActivist13DSignal(identity="x@y.z")
    ems = sig.scan(date(2026, 6, 18))
    assert ems == []
    assert sig._resolver is None                          # dropped -> real retry next session
    ran, detail = sig.available()
    assert ran is False and "resolver empty" in detail

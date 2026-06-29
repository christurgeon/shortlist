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
    sig._resolver = {}  # skip network load
    import shortlist.scout.edgar_index as ei

    def boom(*a, **k):
        raise RuntimeError("SEC 500 https://sec.gov/x?token=SECRET")

    monkeypatch.setattr(ei, "fetch_recent_activist_records", boom)
    ems = sig.scan(date(2026, 6, 18))
    assert ems == []
    ran, detail = sig.available()
    assert ran is False
    assert "SECRET" not in detail  # redact_secrets applied

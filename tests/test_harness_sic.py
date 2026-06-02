from shortlist.data.bridge import snapshot_to_metrics
from shortlist.data.models import (
    Profile,
    SourceResult,
    TickerSnapshot,
    merge_snapshots,
)


def _res(source, **profile_kw):
    snap = TickerSnapshot(ticker="SCHW")
    snap.profile = Profile(**profile_kw)
    return SourceResult(source=source, partial=snap)


def test_merge_keeps_edgar_sic_when_fmp_profile_absent():
    # Finnhub supplies name/mktcap but NO sic; EDGAR supplies a partial profile w/ only sic.
    finnhub = _res("finnhub", name="Schwab", market_cap=152e9)
    edgar = _res("edgar", sic="6211")
    merged = merge_snapshots("SCHW", [finnhub, edgar], priority=["finnhub", "edgar"])
    assert merged.profile.sic == "6211"
    assert merged.profile.name == "Schwab"


def test_bridge_copies_sic_to_metrics():
    snap = TickerSnapshot(ticker="SCHW")
    snap.profile = Profile(name="Schwab", sic="6211")
    m = snapshot_to_metrics(snap)
    assert m.sic == "6211"


def test_edgarsource_fetch_sync_attaches_sic_profile(monkeypatch):
    from shortlist.data.sources import EdgarSource

    src = EdgarSource(identity="test test@example.com")
    # Patch the seams so no network/edgartools is touched.
    monkeypatch.setattr(src, "_fetch_insider",
                        lambda t: SourceResult(source="edgar", partial=TickerSnapshot(ticker=t)))
    monkeypatch.setattr(src, "_fetch_sic", lambda t: "6211")
    monkeypatch.setattr(src, "_fetch_financials_object",
                        lambda t: (_ for _ in ()).throw(RuntimeError("skip")))
    monkeypatch.setattr(src, "_fetch_filings_index", lambda t: [])

    res = src._fetch_sync("SCHW")
    assert res.partial.profile is not None
    assert res.partial.profile.sic == "6211"

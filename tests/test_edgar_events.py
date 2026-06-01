from datetime import date

from shortlist.data.models import Events, FilingEvent, TickerSnapshot
from shortlist.data.sources import build_events_section, classify_event_form


def _sample_events():
    return Events(
        recent=[FilingEvent(form="SC 13D", filed="2026-05-26",
                            accession="0000-1", url="https://sec.gov/x")],
        activist_13d=True,
    )


def test_events_roundtrips_through_to_from_dict():
    snap = TickerSnapshot(ticker="AAPL")
    snap.events = _sample_events()
    rebuilt = TickerSnapshot.from_dict(snap.to_dict())
    assert rebuilt.events is not None
    assert rebuilt.events.activist_13d is True
    assert len(rebuilt.events.recent) == 1
    assert isinstance(rebuilt.events.recent[0], FilingEvent)
    assert rebuilt.events.recent[0].form == "SC 13D"


def test_events_does_not_affect_coverage():
    bare = TickerSnapshot(ticker="AAPL")
    withev = TickerSnapshot(ticker="AAPL")
    withev.events = _sample_events()
    assert bare.coverage() == withev.coverage()
    assert bare.missing() == withev.missing()


def _rec(form, filed, acc="a", url="u"):
    return {"form": form, "filed": filed, "accession": acc, "url": url}


def test_classify_covers_all_families_and_variants():
    assert classify_event_form("8-K") == "recent_8k"
    assert classify_event_form("8-K/A") == "recent_8k"
    assert classify_event_form("SC 13D") == "activist_13d"
    assert classify_event_form("SC 13D/A") == "activist_13d"
    assert classify_event_form("SCHEDULE 13D") == "activist_13d"
    assert classify_event_form("SC 13G") == "passive_13g"
    assert classify_event_form("SCHEDULE 13G/A") == "passive_13g"
    assert classify_event_form("144") == "planned_insider_sale_144"
    assert classify_event_form("144/A") == "planned_insider_sale_144"
    assert classify_event_form("10-K") is None


def test_build_filters_by_lookback_and_sets_flags():
    today = date(2026, 6, 1)
    recs = [
        _rec("8-K", "2026-05-20"),
        _rec("SC 13D", "2026-04-01"),
        _rec("10-K", "2026-05-15"),          # not an event form -> dropped
        _rec("144", "2026-01-01"),           # outside 90d window -> dropped
    ]
    ev = build_events_section(recs, lookback_days=90, today=today)
    assert ev is not None
    assert ev.recent_8k is True
    assert ev.activist_13d is True
    assert ev.planned_insider_sale_144 is False     # the only 144 was out of window
    assert [e.form for e in ev.recent] == ["8-K", "SC 13D"]   # newest-first, in-window only


def test_build_returns_none_when_no_inwindow_events():
    today = date(2026, 6, 1)
    assert build_events_section([], 90, today) is None
    assert build_events_section([_rec("8-K", "2020-01-01")], 90, today) is None
    assert build_events_section([_rec("10-K", "2026-05-30")], 90, today) is None


def test_build_never_returns_all_falsy_events():
    today = date(2026, 6, 1)
    ev = build_events_section([_rec("8-K", "2026-05-30")], 90, today)
    assert ev is not None
    assert any([ev.recent_8k, ev.activist_13d, ev.passive_13g,
                ev.planned_insider_sale_144])
    assert ev.recent  # and recent is non-empty


from shortlist.data.models import SourceResult, merge_snapshots


def test_events_merge_picks_edgar_and_records_provenance():
    edgar = SourceResult(source="edgar")
    edgar.partial = TickerSnapshot(ticker="AAPL")
    edgar.partial.events = _sample_events()
    fmp = SourceResult(source="fmp")
    fmp.partial = TickerSnapshot(ticker="AAPL")          # no events
    merged = merge_snapshots("AAPL", [fmp, edgar], priority=["yahoo", "edgar", "fmp"])
    assert merged.events is not None
    assert merged.events.activist_13d is True
    assert merged.provenance["events"] == ["edgar"]


def test_merge_without_events_leaves_section_none():
    fmp = SourceResult(source="fmp")
    fmp.partial = TickerSnapshot(ticker="AAPL")
    merged = merge_snapshots("AAPL", [fmp], priority=["fmp"])
    assert merged.events is None
    assert "events" not in merged.provenance


from datetime import date as _date

from shortlist.data.models import Insider, SourceResult
from shortlist.data.sources import EdgarSource


class _FakeFiling:
    """edgartools EntityFiling-like (has .form, so the normalizer treats it as single)."""
    def __init__(self, form, d):
        self.form = form
        self.filing_date = d           # a datetime.date (has .isoformat)
        self.accession_no = "acc"
        self.url = "https://sec.gov/x"


class _StubEdgar(EdgarSource):
    """EdgarSource with network seams stubbed; bypasses __init__/identity. Overrides
    only `_raw_filings` so the REAL `_fetch_filings_index` normalization is exercised."""
    def __init__(self, *, raw=None, insider_snap=None, raise_index=False):
        self._raw = raw
        self._insider_snap = insider_snap
        self._raise_index = raise_index
        self._event_forms = ["8-K", "SC 13D"]
        self._event_lookback_days = 90
        self._index_limit = 40

    def _fetch_insider(self, ticker):
        res = SourceResult(source="edgar")
        res.partial = self._insider_snap or TickerSnapshot(ticker=ticker)
        return res

    def _fetch_financials_object(self, ticker):
        raise RuntimeError("financials skipped in this test")

    def _raw_filings(self, ticker):
        if self._raise_index:
            raise RuntimeError("SEC down")
        return self._raw


def test_events_failure_does_not_drop_insider():
    snap = TickerSnapshot(ticker="AAPL")
    snap.insider = Insider(net_value_6m=1.0, buy_count=1, sell_count=0)
    src = _StubEdgar(insider_snap=snap, raise_index=True)
    res = src._fetch_sync("AAPL")
    assert res.partial.insider.net_value_6m == 1.0          # insider survived
    assert res.partial.events is None
    assert any("edgar-events:" in e for e in res.errors)


def test_events_populate_from_index():
    src = _StubEdgar(raw=[_FakeFiling("8-K", _date.today())])  # today => always in-window
    res = src._fetch_sync("AAPL")
    assert res.partial.events is not None
    assert res.partial.events.recent_8k is True
    assert res.partial.events.recent[0].form == "8-K"


def test_fetch_filings_index_normalizes_none_single_collection():
    src = _StubEdgar()
    src._raw = None                                          # None -> []
    assert src._fetch_filings_index("AAPL") == []
    src._raw = _FakeFiling("8-K", _date(2026, 5, 30))        # single -> one-element list
    out = src._fetch_filings_index("AAPL")
    assert len(out) == 1 and out[0]["form"] == "8-K" and out[0]["filed"] == "2026-05-30"
    src._raw = [_FakeFiling("8-K", _date(2026, 5, 30)),      # collection -> full list
                _FakeFiling("144", _date(2026, 5, 1))]
    assert [r["form"] for r in src._fetch_filings_index("AAPL")] == ["8-K", "144"]

import os

import pytest
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
    assert ev.last_report_filed == "2026-05-15"     # the 10-K anchors, never joins recent


def test_build_returns_none_when_no_inwindow_events():
    today = date(2026, 6, 1)
    assert build_events_section([], 90, today) is None
    assert build_events_section([_rec("8-K", "2020-01-01")], 90, today) is None


def test_build_last_report_filed_exact_forms_only():
    # 10-Q/10-K only — a 10-Q/A can land months after the print and an 8-K is any
    # event; either would wrongly FRESHEN the SUE decay anchor.
    today = date(2026, 7, 9)
    recs = [_rec("10-Q/A", "2026-06-25"), _rec("8-K", "2026-07-01"),
            _rec("10-Q", "2026-05-05"), _rec("10-K", "2025-11-01")]
    ev = build_events_section(recs, lookback_days=90, today=today)
    assert ev.last_report_filed == "2026-05-05"     # latest exact 10-Q/10-K
    # advisory list untouched: 10-Q/A is not an advisory form (and the exact-match
    # fetch never retrieves it live); report forms never join `recent`.
    assert [e.form for e in ev.recent] == ["8-K"]


def test_build_events_from_lone_10q_carries_anchor_only():
    # A quiet name whose only recent filing is the 10-Q still gets the anchor — an
    # Events with a real last_report_filed is NOT the all-falsy case the None-return
    # guards against (_has_data sees the date).
    today = date(2026, 6, 1)
    ev = build_events_section([_rec("10-K", "2026-05-30")], 90, today)
    assert ev is not None
    assert ev.last_report_filed == "2026-05-30"
    assert ev.recent == []
    assert not any([ev.recent_8k, ev.activist_13d, ev.passive_13g,
                    ev.planned_insider_sale_144])


def test_last_report_filed_roundtrips():
    snap = TickerSnapshot(ticker="AAPL")
    snap.events = Events(last_report_filed="2026-05-05")
    rebuilt = TickerSnapshot.from_dict(snap.to_dict())
    assert rebuilt.events.last_report_filed == "2026-05-05"


def test_default_event_forms_include_10q_10k(monkeypatch):
    # The fetch filter is exact-match — without these forms the anchor never fires.
    monkeypatch.setenv("SEC_IDENTITY", "test@example.com")
    monkeypatch.setattr("edgar.set_identity", lambda *_a, **_k: None)
    edgar = [s for s in build_sources(["edgar"]) if s.name == "edgar"][0]
    assert "10-Q" in edgar._event_forms and "10-K" in edgar._event_forms


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


from shortlist.data.sources import build_sources


def test_build_sources_passes_config_to_edgar(monkeypatch):
    monkeypatch.setenv("SEC_IDENTITY", "test@example.com")
    monkeypatch.setattr("edgar.set_identity", lambda *_a, **_k: None)
    cfg = {"edgar_events": {"lookback_days": 7, "forms": ["8-K"], "index_limit": 5}}
    sources = build_sources(["edgar"], config=cfg)
    edgar = [s for s in sources if s.name == "edgar"][0]
    assert edgar._event_lookback_days == 7
    assert edgar._event_forms == ["8-K"]
    assert edgar._index_limit == 5


def test_build_sources_without_config_uses_defaults(monkeypatch):
    monkeypatch.setenv("SEC_IDENTITY", "test@example.com")
    monkeypatch.setattr("edgar.set_identity", lambda *_a, **_k: None)
    edgar = [s for s in build_sources(["edgar"]) if s.name == "edgar"][0]
    assert edgar._event_lookback_days == 90


def _min_config():
    import yaml, pathlib
    return yaml.safe_load((pathlib.Path(__file__).parent.parent / "config.yaml").read_text())


def test_bridge_copies_events_when_present():
    snap = TickerSnapshot(ticker="AAPL")
    snap.events = _sample_events()
    from shortlist.data.bridge import snapshot_to_metrics
    m = snapshot_to_metrics(snap)
    assert m.activist_13d is True
    assert m.recent_8k is False
    # `items` joined the payload with the 8-K item-code fix; None for non-8-K forms.
    assert m.filing_events == [
        {"form": "SC 13D", "filed": "2026-05-26", "accession": "0000-1",
         "url": "https://sec.gov/x", "items": None}]


def test_bridge_leaves_events_none_when_absent():
    from shortlist.data.bridge import snapshot_to_metrics
    m = snapshot_to_metrics(TickerSnapshot(ticker="AAPL"))
    assert m.activist_13d is None
    assert m.filing_events is None


def test_events_have_zero_score_impact():
    from shortlist.data.bridge import snapshot_to_metrics
    from shortlist.scoring import score
    snap = TickerSnapshot(ticker="AAPL")
    config = _min_config()
    before = score(snapshot_to_metrics(snap), config)
    snap.events = _sample_events()
    after = score(snapshot_to_metrics(snap), config)
    assert (before.composite, before.quality, before.moat, before.growth,
            before.momentum, before.value, before.insider) == \
           (after.composite, after.quality, after.moat, after.growth,
            after.momentum, after.value, after.insider)


from shortlist.models import ScoreCard, StockMetrics
from shortlist.scoring import check_flags
from shortlist.screen import _card_dict


def _metrics_with_events():
    m = StockMetrics(ticker="AAPL")
    m.activist_13d = True
    m.recent_8k = True
    m.filing_events = [{"form": "SC 13D", "filed": "2026-05-26", "accession": "x", "url": "u"}]
    return m


def test_check_flags_emits_event_advisories():
    flags = check_flags(_metrics_with_events(), {})
    assert "activist_13d" in flags
    assert "recent_8k" in flags
    assert "passive_13g" not in flags          # not set


def test_check_flags_no_events_no_advisories():
    assert check_flags(StockMetrics(ticker="AAPL"), {}) == []


def _card_with_events():
    return ScoreCard(ticker="AAPL", composite=50.0, quality=None, moat=None, growth=None,
                     momentum=None, value=None, opportunity=None, insider=None,
                     metrics=_metrics_with_events())


def test_card_dict_emits_events_block_only_when_present():
    assert _card_dict(_card_with_events())["events"]["recent"][0]["form"] == "SC 13D"
    plain = ScoreCard(ticker="AAPL", composite=50.0, quality=None, moat=None, growth=None,
                      momentum=None, value=None, opportunity=None, insider=None,
                      metrics=StockMetrics(ticker="AAPL"))
    assert "events" not in _card_dict(plain)


from shortlist.research.assess import _build_user_prompt
from shortlist.research.models import FilingText


def _wrap(ft):
    from shortlist.research.models import FilingBundle
    return FilingBundle(tenk=ft, primary_accession=ft.accession,
                        cache_key=ft.accession, filing_date=ft.filing_date)


def _filing():
    return FilingText(ticker="AAPL", accession="acc", filing_date="2026-05-01",
                      business="b", mda="m", risk_factors="r")


def test_prompt_includes_recent_filings_when_events_present():
    events = [{"form": "SC 13D", "filed": "2026-05-26", "accession": "x", "url": "u"}]
    p = _build_user_prompt(_wrap(_filing()), {}, filing_events=events)
    assert "Recent SEC filings" in p
    assert "SC 13D" in p and "2026-05-26" in p


def test_prompt_unchanged_when_no_events():
    base = _build_user_prompt(_wrap(_filing()), {})
    assert "Recent SEC filings" not in base


@pytest.mark.live
def test_live_edgar_events_returns_event_forms():
    """Re-pins the form-string contract against real SEC data. Run with
    `uv run pytest -k live_edgar_events -m live` and SEC_IDENTITY set."""
    if not os.environ.get("SEC_IDENTITY"):
        pytest.skip("SEC_IDENTITY not set")
    from shortlist.data.sources import EdgarSource
    src = EdgarSource(config={"edgar_events": {"lookback_days": 3650, "index_limit": 50}})
    records = src._fetch_filings_index("AAPL")
    forms = {r["form"] for r in records}
    assert any(f.startswith("8-K") for f in forms)
    assert any("13" in f for f in forms)   # a 13D or 13G should appear over 10y


def test_filing_event_carries_8k_item_codes():
    """D5: edgartools' filings index already has an `items` column; dropping it made a
    non-reliance restatement (4.02) indistinguishable from a routine 8-K in the brief."""
    ev = build_events_section(
        [{"form": "8-K", "filed": "2026-07-23", "accession": "0000-9",
          "url": "https://sec.gov/x", "items": "4.02,9.01"}],
        lookback_days=90, today=date(2026, 7, 24))
    assert ev is not None
    assert ev.recent[0].items == "4.02,9.01"


def test_filing_event_without_items_is_none_not_empty():
    """Non-8-K forms carry no items; abstain rather than render an empty item list."""
    ev = build_events_section(
        [{"form": "SC 13D", "filed": "2026-07-23", "accession": "0000-9"}],
        lookback_days=90, today=date(2026, 7, 24))
    assert ev is not None and ev.recent[0].items is None


def test_filing_event_items_survive_snapshot_roundtrip():
    """Old persisted snapshots predate `items`; _build must tolerate its absence and
    new ones must round-trip it."""
    snap = TickerSnapshot(ticker="AAPL")
    snap.events = Events(recent=[FilingEvent(form="8-K", filed="2026-07-23",
                                             accession="0000-9", items="2.02,9.01")],
                         recent_8k=True)
    rebuilt = TickerSnapshot.from_dict(snap.to_dict())
    assert rebuilt.events.recent[0].items == "2.02,9.01"
    # a pre-feature payload (no `items` key at all) must still load
    legacy = snap.to_dict()
    del legacy["events"]["recent"][0]["items"]
    assert TickerSnapshot.from_dict(legacy).events.recent[0].items is None


# ------------------------------------------- distress / dilution / integrity forms

def test_new_forms_classify_to_their_flags():
    assert classify_event_form("NT 10-K") == "late_filing"
    assert classify_event_form("NT 10-Q/A") == "late_filing"
    assert classify_event_form("S-3ASR") == "shelf_offering"
    assert classify_event_form("424B5") == "shelf_offering"
    assert classify_event_form("UPLOAD") == "sec_comment_letter"
    assert classify_event_form("CORRESP") == "sec_comment_letter"


def test_prefix_matching_does_not_reclassify_neighbouring_forms():
    """`classify_event_form` is a PREFIX match over a form list that now contains
    424B5, so a bare "4" prefix would silently swallow Form 4 — the insider feed.
    These are the collisions the list was checked against."""
    assert classify_event_form("4") is None          # Form 4 — insider, not an event
    assert classify_event_form("4/A") is None
    assert classify_event_form("3") is None
    assert classify_event_form("5") is None
    assert classify_event_form("10-K") is None       # handled separately (SUE anchor)
    assert classify_event_form("10-Q") is None
    assert classify_event_form("S-1") is None        # not a shelf
    assert classify_event_form("") is None


def test_form_25_is_excluded_on_purpose():
    """17 of 228 names filed a 25/25-NSE within a year, essentially all for a matured
    note or warrant rather than the issuer's common stock. Do not re-add it as a
    delisting flag without new evidence."""
    assert classify_event_form("25") is None
    assert classify_event_form("25-NSE") is None


def test_eightk_item_codes_map_to_flags():
    from shortlist.data.sources.edgar import classify_event_items
    assert classify_event_items("4.02,9.01") == ["restatement_8k"]
    assert classify_event_items(["4.01"]) == ["auditor_change"]
    assert classify_event_items("3.01") == ["listing_deficiency"]
    assert classify_event_items("2.02,9.01") == []
    assert classify_event_items(None) == []
    assert classify_event_items("") == []


def test_item_codes_are_matched_whole_not_as_substrings():
    """"4.02" is a substring of "14.02" and of "4.021"; a substring match would
    invent a restatement flag out of an unrelated item code."""
    from shortlist.data.sources.edgar import classify_event_items
    assert classify_event_items("14.02") == []
    assert classify_event_items("4.021") == []


def test_build_events_sets_item_derived_flags():
    ev = build_events_section(
        [{"form": "8-K", "filed": "2026-08-01", "accession": "a", "items": "4.02,9.01"}],
        lookback_days=90, today=date(2026, 8, 23))
    assert ev is not None
    assert ev.recent_8k is True and ev.restatement_8k is True
    assert ev.auditor_change is False and ev.listing_deficiency is False


def test_item_flags_come_only_from_8ks_in_the_window():
    """An out-of-window 8-K must not set the item flag either — the flags are
    advisory statements about the CURRENT window, not about history."""
    ev = build_events_section(
        [{"form": "8-K", "filed": "2025-01-01", "accession": "a", "items": "4.02"},
         {"form": "NT 10-K", "filed": "2026-08-01", "accession": "b"}],
        lookback_days=90, today=date(2026, 8, 23))
    assert ev is not None
    assert ev.restatement_8k is False and ev.recent_8k is False
    assert ev.late_filing is True


def test_a_shelf_takedown_sets_only_its_own_flag():
    ev = build_events_section(
        [{"form": "424B5", "filed": "2026-08-10", "accession": "a"}],
        lookback_days=90, today=date(2026, 8, 23))
    assert ev is not None
    assert ev.shelf_offering is True
    assert ev.recent_8k is False and ev.late_filing is False

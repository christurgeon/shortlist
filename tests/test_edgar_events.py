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

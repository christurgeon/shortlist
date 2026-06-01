from shortlist.data.models import Events, FilingEvent, TickerSnapshot


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

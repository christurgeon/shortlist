from datetime import date
from shortlist.scout.models import Emission
from shortlist.scout.firehose import CohortEvent, cohort_events_from_emissions


def test_builder_maps_every_emission_presorter():
    ems = [
        Emission(ticker="ABC", signal="edgar:activist_13d", strength=0.9,
                 evidence="Elliott -> ABC", is_discovery=True, cik="0000123"),
        Emission(ticker="XYZ", signal="finra:short_interest", strength=0.4,
                 evidence="SI jump 35%", is_discovery=True, cik=None),
    ]
    events = cohort_events_from_emissions(ems, date(2026, 7, 1))
    assert len(events) == 2
    e0 = events[0]
    assert e0.signal == "edgar:activist_13d"
    assert e0.ticker == "ABC"
    assert e0.cik == "0000123"
    assert e0.event_date == date(2026, 7, 1)
    assert e0.strength == 0.9
    assert e0.as_of_price is None      # derived later (Yahoo), not known at emit time
    assert e0.gated is None and e0.composite is None
    assert e0.origin == "live"


def test_to_dict_is_json_safe():
    ev = cohort_events_from_emissions(
        [Emission("ABC", "edgar:activist_13d", 0.9, "ev", True, "0000123")],
        date(2026, 7, 1))[0]
    d = ev.to_dict()
    assert d["event_date"] == "2026-07-01"     # ISO string, not a date object
    assert d["ticker"] == "ABC"


def test_origin_override_for_backfill():
    ev = cohort_events_from_emissions(
        [Emission("ABC", "edgar:activist_13d", 0.9, "ev", True, "0000123")],
        date(2025, 1, 2), origin="backfill")[0]
    assert ev.origin == "backfill"

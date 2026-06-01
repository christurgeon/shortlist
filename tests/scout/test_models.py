from datetime import date
from shortlist.scout.models import Emission, Candidate, SignalStatus, RunManifest, INTEREST_CAP


def test_candidate_aggregates_signals_and_interest():
    c = Candidate(ticker="AAPL")
    c.add(Emission(ticker="AAPL", signal="yahoo:day_gainers", strength=0.8,
                   evidence="+8% on 3x vol", is_discovery=True), weight=1.0)
    c.add(Emission(ticker="AAPL", signal="wikipedia", strength=0.5,
                   evidence="+30% pageviews", is_discovery=False), weight=0.5)
    assert c.interest == 0.8 * 1.0 + 0.5 * 0.5
    assert c.has_discovery is True
    assert {e.signal for e in c.emissions} == {"yahoo:day_gainers", "wikipedia"}


def test_candidate_interest_is_capped():
    c = Candidate(ticker="X")
    for i in range(20):
        c.add(Emission(ticker="X", signal=f"s{i}", strength=1.0, evidence="",
                       is_discovery=True), weight=1.0)
    assert c.interest <= INTEREST_CAP


def test_runmanifest_roundtrips_to_dict():
    m = RunManifest(session=date(2026, 5, 29),
                    signals=[SignalStatus(name="yahoo", ran=True, detail="42 hits")],
                    raw=42, after_dedup=30, after_prefilter=18, screened=15,
                    dropped_for_budget=3, researched=["AAPL"])
    d = m.to_dict()
    assert d["session"] == "2026-05-29"
    assert d["funnel"]["screened"] == 15
    assert d["signals"][0]["name"] == "yahoo"

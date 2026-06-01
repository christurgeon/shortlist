import yaml
from pathlib import Path

from shortlist.screen import run_harness, main

CONFIG = yaml.safe_load(
    (Path(__file__).parent.parent / "config.yaml").read_text())


def test_run_harness_scores_mock_snapshots():
    # NOTE: MockSource snapshots have no `statements`, so the bridge's
    # statements-derived fields (gross_margin_stability, fcf_positive) are None
    # on this path by design — that derivation is covered in test_bridge.py.
    # `quality` here comes from `fundamentals` (which mock DOES populate), so it
    # is the right signal that the harness->bridge->score path works end-to-end.
    cards = run_harness(["GEV", "LMT", "GOOGL"], ["mock"], CONFIG)
    assert cards, "expected scored cards from the mock source"
    # sorted descending by composite
    comps = [c.composite for c in cards]
    assert comps == sorted(comps, reverse=True)
    # bridge populated the fundamentals-based metrics the scorer needs
    assert any(c.quality is not None for c in cards)


def test_main_engine_harness_demo_runs(capsys):
    rc = main(["--demo", "--engine", "harness", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"composite"' in out


def test_main_default_engine_is_screener_demo(capsys):
    rc = main(["--demo", "--json"])
    assert rc == 0


def test_harness_card_carries_coverage(monkeypatch):
    from shortlist.data.models import TickerSnapshot, Profile, Fundamentals
    from shortlist import screen

    def fake_collect(tickers, source_names):
        return [TickerSnapshot(
            ticker="GEV",
            profile=Profile(market_cap=2e10),
            fundamentals=Fundamentals(fcf_yield=None),
            provenance={"profile": ["finnhub"], "price": ["yahoo"]},
            errors=["fmp: 402 Special Endpoint for GEV"],
        )]

    # run_harness does `from .data.collector import collect` -> patch THERE, not screen.collect.
    monkeypatch.setattr("shortlist.data.collector.collect", fake_collect)
    cards = screen.run_harness(["GEV"], ["yahoo", "fmp", "finnhub", "edgar"], CONFIG)
    assert len(cards) == 1
    assert cards[0].coverage is not None
    assert cards[0].coverage.providers.get("fmp") == "gated_402"

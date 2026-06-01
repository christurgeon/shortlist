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

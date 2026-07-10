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


def test_main_demo_runs(capsys):
    rc = main(["--demo", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"composite"' in out


def test_csv_confirmation_goes_to_stderr_not_stdout(capsys, tmp_path):
    # "wrote <path>" on stdout would corrupt the --json stream.
    import json as _json
    csv_path = tmp_path / "out.csv"
    rc = main(["--demo", "--json", "--csv", str(csv_path)])
    assert rc == 0
    captured = capsys.readouterr()
    _json.loads(captured.out)                      # stdout is pure JSON
    assert "wrote" not in captured.out
    assert f"wrote {csv_path}" in captured.err
    assert csv_path.exists()


def test_ticker_and_provider_parsing_strips_and_drops_empties(monkeypatch):
    from shortlist import screen
    seen = {}

    def fake_run_harness(tickers, sources, config, macro=None):
        seen["tickers"], seen["sources"] = tickers, sources
        return []

    monkeypatch.setattr(screen, "run_harness", fake_run_harness)
    monkeypatch.setattr("shortlist.data.macro.fetch_macro", lambda config: None)
    rc = main(["--tickers", " aapl, ,msft ,", "--provider", " mock , mock ,",
               "--json", "--no-cache"])
    assert rc == 0
    assert seen["tickers"] == ["AAPL", "MSFT"]     # stripped, uppercased, empties dropped
    assert seen["sources"] == ["mock", "mock"]     # stripped, empties dropped


def test_all_empty_provider_or_tickers_fails_loudly():
    # An ALL-empty value (misexpanded shell var) must be a loud argparse error —
    # sources=[] would otherwise "succeed" with a plausible all-null screen.
    import pytest
    with pytest.raises(SystemExit) as e:
        main(["--tickers", "AAPL", "--provider", " , ", "--json", "--no-cache"])
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        main(["--tickers", " , ", "--json", "--no-cache"])
    assert e.value.code == 2


def test_missing_config_file_exits_2_with_message(capsys, tmp_path):
    rc = main(["--demo", "--config", str(tmp_path / "nope.yaml")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "cannot read config file" in err and "nope.yaml" in err


def test_empty_config_file_exits_2_with_message(capsys, tmp_path):
    p = tmp_path / "empty.yaml"
    p.write_text("")                               # yaml.safe_load -> None
    rc = main(["--demo", "--config", str(p)])
    assert rc == 2
    assert "is empty" in capsys.readouterr().err


def test_non_mapping_config_exits_2_with_message(capsys, tmp_path):
    p = tmp_path / "list.yaml"
    p.write_text("- just\n- a\n- list\n")
    rc = main(["--demo", "--config", str(p)])
    assert rc == 2
    assert "must be a YAML mapping" in capsys.readouterr().err


def test_main_routes_to_harness(monkeypatch):
    # The harness is the only engine: main must dispatch to run_harness.
    from shortlist import screen
    calls = []
    monkeypatch.setattr(screen, "run_harness",
                        lambda t, s, c, macro=None: calls.append("harness") or [])
    rc = main(["--demo", "--json"])
    assert rc == 0
    assert calls == ["harness"]


def test_harness_card_carries_coverage(monkeypatch):
    from shortlist.data.models import TickerSnapshot, Profile, Fundamentals
    from shortlist import screen

    def fake_collect(tickers, source_names, config=None):
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


from shortlist.models import ScoreCard
from shortlist.screen import _card_dict, _flags_cell


def _card(**kw):
    base = dict(ticker="X", composite=50.0, quality=None, moat=None, growth=None,
                momentum=None, value=None, opportunity=None, insider=None)
    base.update(kw)
    return ScoreCard(**base)


def test_flags_cell_merges_gates_and_flags():
    assert _flags_cell(_card()) == "-"
    assert _flags_cell(_card(flags=["crowded_short"])) == "crowded_short"
    assert _flags_cell(_card(gates=["over_leveraged"], flags=["crowded_short"])) \
        == "over_leveraged,crowded_short"


def test_card_dict_includes_flags():
    d = _card_dict(_card(flags=["crowded_short"]))
    assert d["flags"] == ["crowded_short"]
    assert d["gates"] == []


def test_rank_key_sort_is_no_bury_and_tiebreaks_on_confidence():
    from shortlist.models import ScoreCard, rank_key
    thin80 = ScoreCard(ticker="THIN", composite=80.0, quality=None, moat=None,
                       growth=None, momentum=None, value=None, opportunity=80.0,
                       insider=None, confidence=0.30, scored=True)
    full78 = ScoreCard(ticker="FULL", composite=78.0, quality=78.0, moat=78.0,
                       growth=78.0, momentum=78.0, value=78.0, opportunity=78.0,
                       insider=78.0, confidence=1.0, scored=True)
    ordered = sorted([full78, thin80], key=rank_key, reverse=True)
    assert [c.ticker for c in ordered] == ["THIN", "FULL"]   # composite dominates


def test_flags_cell_appends_thin():
    from shortlist.models import ScoreCard
    from shortlist.screen import _flags_cell
    c = ScoreCard(ticker="T", composite=50.0, quality=None, moat=None, growth=None,
                  momentum=None, value=None, opportunity=None, insider=None, thin=True)
    assert "thin" in _flags_cell(c).split(",")

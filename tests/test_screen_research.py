import pytest

from shortlist.models import ScoreCard, StockMetrics
from shortlist.screen import _card_dict, build_arg_parser


def _card():
    return ScoreCard(ticker="AAPL", composite=70.0, quality=80, moat=80, momentum=10,
                     value=20, opportunity=20, insider=50, gates=[],
                     metrics=StockMetrics(ticker="AAPL", price=100.0, target_median=120.0))


def test_parser_has_research_flags():
    ap = build_arg_parser()
    args = ap.parse_args(["--tickers", "AAPL", "--research", "5", "--refresh"])
    assert args.research == 5
    assert args.refresh is True


def test_research_defaults_to_none():
    ap = build_arg_parser()
    args = ap.parse_args(["--tickers", "AAPL"])
    assert args.research is None
    assert args.refresh is False


def test_card_dict_includes_research_path_when_present():
    d = _card_dict(_card(), research_paths={"AAPL": "research/AAPL/x.md"})
    assert d["research_path"] == "research/AAPL/x.md"


def test_card_dict_omits_research_path_when_absent():
    d = _card_dict(_card())
    assert "research_path" not in d


def test_run_research_phase_prints_maps_paths_and_handles_cache(capsys, monkeypatch):
    from shortlist import screen
    from shortlist.research import ResearchResult

    fake_results = [
        ResearchResult("AAPL", brief_path="research/AAPL/x.md", cost_usd=0.04, synthesis="Solid moat."),
        ResearchResult("MSFT", brief_path="research/MSFT/y.md", from_cache=True),
        ResearchResult("GEV", skipped="no 10-K"),
    ]
    monkeypatch.setattr(screen, "_research_available", lambda: True)
    monkeypatch.setattr(screen, "_run_enrich", lambda cards, cfg, n, refresh: fake_results)

    paths = screen._run_research_phase([_card()], {"research": {}}, n=3, refresh=False)
    err = capsys.readouterr().err
    assert paths == {"AAPL": "research/AAPL/x.md", "MSFT": "research/MSFT/y.md"}
    assert "AAPL" in err and "Solid moat." in err
    assert "0.04" in err
    assert "cached" in err.lower()
    assert "no 10-K" in err


def test_run_research_phase_skips_when_unavailable(capsys, monkeypatch):
    from shortlist import screen
    monkeypatch.setattr(screen, "_research_available", lambda: False)
    paths = screen._run_research_phase([_card()], {}, n=2, refresh=False)
    assert paths == {}
    assert "skipping research" in capsys.readouterr().err.lower()


def test_research_rejects_non_positive():
    ap = build_arg_parser()
    for bad in ["0", "-1"]:
        with pytest.raises(SystemExit):
            ap.parse_args(["--tickers", "AAPL", "--research", bad])


def test_run_research_phase_survives_enrich_exception(capsys, monkeypatch):
    from shortlist import screen
    monkeypatch.setattr(screen, "_research_available", lambda: True)
    def boom(cards, cfg, n, refresh):
        raise RuntimeError("edgar blew up with token=sk-ant-SECRET99")
    monkeypatch.setattr(screen, "_run_enrich", boom)
    paths = screen._run_research_phase([_card()], {}, n=1, refresh=False)
    assert paths == {}
    err = capsys.readouterr().err
    assert "research phase failed" in err
    assert "sk-ant-SECRET99" not in err          # redacted

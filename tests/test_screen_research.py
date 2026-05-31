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

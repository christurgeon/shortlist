from datetime import date

from shortlist.backtest.cli import build_arg_parser
from shortlist.backtest.engine import BacktestReport, SignalReport
from shortlist.backtest.metrics import ICStats, QuantileResult
from shortlist.backtest.report import report_to_dict


def _report():
    return BacktestReport(
        universe=["AAA", "BBB"], price_asof=date(2026, 6, 1), horizons=[3],
        return_mode="excess",
        reports=[SignalReport("momentum", 3,
            ts_ic=ICStats(0.05, 0.2, 0.25, 1.2, 0.6, 30),
            xs_ic=None, spread=QuantileResult([0.0, 0.1], 0.1, True, 2, 60),
            n_obs=60, breadth=2.0, notes=["EXPLORATORY: below trust floor"])],
        caveats=["survivorship upper bound"])


def test_report_to_dict_shape():
    d = report_to_dict(_report())
    assert d["return_mode"] == "excess"
    assert d["price_asof"] == "2026-06-01"
    assert d["signals"][0]["signal"] == "momentum"
    assert d["signals"][0]["ts_ic"]["mean"] == 0.05
    assert "survivorship" in d["caveats"][0]


def test_arg_parser_defaults():
    ap = build_arg_parser()
    args = ap.parse_args([])
    assert args.universe == "largecap"
    assert args.horizons == "3"
    assert args.return_mode == "excess"
    assert args.source == "momentum"


def test_arg_parser_accepts_xbrl_source():
    ap = build_arg_parser()
    args = ap.parse_args(["--source", "xbrl"])
    assert args.source == "xbrl"
    assert args.xbrl_cache_dir == ".cache/sec_xbrl"

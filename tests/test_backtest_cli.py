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


def test_arg_parser_accepts_fit_flags():
    ap = build_arg_parser()
    args = ap.parse_args(["--source", "xbrl", "--fit", "--fit-horizon", "6"])
    assert args.fit is True
    assert args.fit_horizon == 6
    assert args.fit_axes == "quality,moat,growth,value"
    assert args.n_folds == 6
    assert args.shrink == 0.5


def test_fit_requires_xbrl_source():
    from shortlist.backtest.cli import main
    rc = main(["--source", "momentum", "--fit", "--fit-horizon", "6"])
    assert rc == 2


def test_fit_requires_fit_horizon(monkeypatch):
    from shortlist.backtest.cli import main
    monkeypatch.setenv("SEC_IDENTITY", "test@example.com")
    rc = main(["--source", "xbrl", "--fit"])
    assert rc == 2


def test_fit_prior_is_only_the_fit_axes():
    # The prior handed to fit_weights must be exactly the fundamental subset, never the
    # full 7-axis weights block (else momentum/insider/risk contaminate the composite).
    from shortlist.backtest.cli import _fit_prior_from_config
    config = {"weights": {"quality": 0.18, "moat": 0.18, "growth": 0.135, "value": 0.22,
                          "momentum": 0.08, "insider": 0.135, "risk": 0.10}}
    prior, s_f = _fit_prior_from_config(config, ["quality", "moat", "growth", "value"])
    assert set(prior) == {"quality", "moat", "growth", "value"}
    assert abs(s_f - 0.715) < 1e-9

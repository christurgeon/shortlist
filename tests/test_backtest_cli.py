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


# --- CLI clean-exit guards (added 2026-06-14: bad/empty horizons, start>end, SPY fail) ---

def test_main_rejects_non_integer_horizons():
    from shortlist.backtest.cli import main
    assert main(["--tickers", "AAPL", "--horizons", "3,abc"]) == 2


def test_main_rejects_empty_horizons():
    from shortlist.backtest.cli import main
    assert main(["--tickers", "AAPL", "--horizons", ""]) == 2


def test_main_rejects_non_positive_horizons():
    from shortlist.backtest.cli import main
    assert main(["--tickers", "AAPL", "--horizons", "0"]) == 2


def test_main_rejects_start_after_end(monkeypatch):
    from shortlist.backtest import cli
    from shortlist.backtest.prices import PriceHistory

    async def fake_load(tickers, cache_dir, today):
        h = PriceHistory("AAPL", ["2024-01-02", "2024-02-01"], [100.0, 110.0])
        spy = PriceHistory("SPY", ["2024-01-02", "2024-02-01"], [400.0, 410.0])
        return {"AAPL": h}, spy

    monkeypatch.setattr(cli, "_load_histories", fake_load)
    rc = cli.main(["--tickers", "AAPL", "--horizons", "6",
                   "--start", "2025-01-01", "--end", "2024-01-01"])
    assert rc == 2


def test_main_clean_exit_when_price_fetch_fails(monkeypatch):
    # SPY benchmark + ticker fetches all raise -> empty histories -> clean return 1,
    # never an unhandled traceback (the SPY-fetch isolation path).
    from shortlist.backtest import cli

    async def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(cli, "fetch_history", boom)
    assert cli.main(["--tickers", "AAPL", "--horizons", "6"]) == 1


# --- FIX M2: collinearity gate is exercised for --source momentum -------------

def test_main_collinearity_emitted_for_momentum_source(monkeypatch, capsys):
    """Run main(--source momentum) with synthetic deterministic histories and assert
    that at least one 'Leg collinearity' line is printed to stderr. Guards against a
    silent revert of `args.source in ('xbrl', 'momentum')` to `== 'xbrl'`."""
    from datetime import date, timedelta
    from shortlist.backtest import cli
    from shortlist.backtest.prices import PriceHistory

    def _ramp(ticker, step):
        """600 consecutive calendar-day closes from 2020-01-01 with different steps
        -> genuinely different max_daily_return / momentum / vol_scaled_momentum per
        ticker, satisfying spearman_ic's n >= 3 co-present-pairs requirement."""
        d0 = date(2020, 1, 1)
        dates = [d0 + timedelta(days=i) for i in range(600)]
        closes = [100.0 + step * i for i in range(600)]
        return PriceHistory(ticker, dates, closes)

    async def fake_load(tickers, cache_dir, today):
        hists = {
            "AAA": _ramp("AAA", step=3.0),
            "BBB": _ramp("BBB", step=1.0),
            "CCC": _ramp("CCC", step=0.3),
        }
        spy = _ramp("SPY", step=0.5)
        return hists, spy

    monkeypatch.setattr(cli, "_load_histories", fake_load)
    rc = cli.main([
        "--tickers", "AAA,BBB,CCC",
        "--source", "momentum",
        "--horizons", "1",
        "--start", "2021-04-01",   # well inside the 600-day range (day 456)
        "--end", "2021-06-01",     # day 517, forward-return horizon lands at day 547
    ])
    assert rc == 0
    captured = capsys.readouterr()
    assert "Leg collinearity" in captured.err


# --- guarded config / date / fit-horizon parsing (hardening round 1) -----------

def test_main_rejects_missing_config(tmp_path, capsys):
    from shortlist.backtest.cli import main
    rc = main(["--tickers", "AAPL", "--config", str(tmp_path / "nope.yaml")])
    assert rc == 2
    assert "nope.yaml" in capsys.readouterr().err


def test_main_rejects_empty_yaml_config(tmp_path, capsys):
    from shortlist.backtest.cli import main
    cfg = tmp_path / "empty.yaml"
    cfg.write_text("")           # yaml.safe_load -> None
    rc = main(["--tickers", "AAPL", "--config", str(cfg)])
    assert rc == 2
    assert "empty.yaml" in capsys.readouterr().err


def test_main_rejects_config_missing_thresholds(tmp_path, capsys):
    from shortlist.backtest.cli import main
    cfg = tmp_path / "partial.yaml"
    cfg.write_text("weights: {}\n")   # no 'thresholds'
    rc = main(["--tickers", "AAPL", "--config", str(cfg)])
    assert rc == 2
    assert "thresholds" in capsys.readouterr().err


def test_main_accepts_thresholds_only_config(tmp_path, capsys, monkeypatch):
    # 'weights' is only read by the --fit path (which has its own guard); a
    # thresholds-only config must pass validation for plain IC-measurement runs.
    import shortlist.backtest.cli as cli
    from shortlist.backtest.prices import PriceHistory

    async def _no_histories(tickers, cache_dir, today):
        return {}, PriceHistory("SPY", [], [], nominal_closes=[])

    monkeypatch.setattr(cli, "_load_histories", _no_histories)
    cfg = tmp_path / "thresholds_only.yaml"
    cfg.write_text("thresholds: {}\n")
    rc = cli.main(["--tickers", "AAPL", "--config", str(cfg)])
    err = capsys.readouterr().err
    # past config validation: fails later on (stubbed-empty) price data, not on config
    assert rc == 1
    assert "no price history" in err


def test_main_rejects_invalid_yaml_config(tmp_path, capsys):
    from shortlist.backtest.cli import main
    cfg = tmp_path / "broken.yaml"
    cfg.write_text("thresholds: [unclosed\n")
    rc = main(["--tickers", "AAPL", "--config", str(cfg)])
    assert rc == 2
    assert "broken.yaml" in capsys.readouterr().err


def test_main_rejects_malformed_start_date(capsys):
    from shortlist.backtest.cli import main
    rc = main(["--tickers", "AAPL", "--start", "not-a-date"])
    assert rc == 2
    assert "--start" in capsys.readouterr().err


def test_main_rejects_malformed_end_date(capsys):
    from shortlist.backtest.cli import main
    rc = main(["--tickers", "AAPL", "--end", "2026-13-99"])
    assert rc == 2
    assert "--end" in capsys.readouterr().err


def test_fit_horizon_zero_rejected(capsys):
    # horizon 0 would spin observation_grid forever (_add_months(cur, 0) never
    # advances) — the arg check must fire before ANY work, without SEC_IDENTITY.
    from shortlist.backtest.cli import main
    rc = main(["--source", "xbrl", "--fit", "--fit-horizon", "0"])
    assert rc == 2
    assert "--fit-horizon" in capsys.readouterr().err


def test_fit_horizon_negative_rejected(capsys):
    from shortlist.backtest.cli import main
    rc = main(["--source", "xbrl", "--fit", "--fit-horizon", "-3"])
    assert rc == 2
    assert "--fit-horizon" in capsys.readouterr().err

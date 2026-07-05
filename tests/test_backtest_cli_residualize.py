"""Tests for `--residualize TARGET~CTRL1,CTRL2` (design spec
2026-07-05-leverage-residualized-ic-design.md, §Implementation 3).

Two layers:
  - parser/argv-level validation (bad spec strings, the --step-months combo
    error) via `main()`, asserting SystemExit(2) (an argparse `ap.error`, not
    the print+return-2 pattern the rest of the CLI uses).
  - the pure `run_residualize()` hook, exercised directly against a planted
    SignalSource + PriceHistory dict (mirroring test_backtest_golden.py's
    approach of testing the engine directly rather than through argv/network).

--- Planted discriminating fixture -----------------------------------------

40 tickers T00..T39 (>= the residual regression floor AND the report's
xs_min_breadth=30, so cross-sectional IC is never suppressed). Two controls:

    ctrl1[i] = i + 1                                  (1..40, ticker rank order)
    ctrl2[i] = PERM[i]                                 (a fixed shuffle of 1..40,
                                                         picked so it is NOT an
                                                         affine function of ctrl1
                                                         -- an affine control pair
                                                         is exactly singular under
                                                         OLS)
    target[i] = 0.7*ctrl1[i] + 0.3*ctrl2[i]            (an EXACT linear re-encoding
                                                         of the controls -- the
                                                         "leverage duplicates
                                                         growth+quality" scenario
                                                         this design measures)

Forward returns are driven purely by ctrl1's rank (monthly price growth factor
strictly increasing in ctrl1), so rank(forward_return) == rank(ctrl1) exactly.
Consequences, verified numerically before being hard-coded here (the PERM list
below was selected by search over `random.Random(seed).shuffle` seeds for one
that clears both bounds with margin -- seed=1):
  - `target_rawx` (raw target restricted to the co-presence set) correlates
    strongly with forward returns (IC > 0.8): target is 70% ctrl1 by
    construction.
  - `target_resid` (target rank-residualized on ctrl1+ctrl2) has NEAR-ZERO IC
    (|IC| < 0.15): once ctrl1 (and ctrl2) are controlled for, target carries no
    information the controls didn't already have -- "it's rank-noise" (design
    §Implementation 3 / the pre-registered decision rule's rationale).
  - `target_resid_level` (the LEVEL/secondary estimator) is NOT asserted on
    numerically: target is an EXACT level combination of the raw controls, so
    the level residual is ~1e-14 floating-point noise whose RANK can spuriously
    correlate with anything (a known degenerate case for rank stats on
    near-zero-variance inputs) -- the design's PRIMARY decision statistic is
    the rank estimator for exactly this reason (review I3).

start=2020-01-01, end=2020-04-01, horizons=[1, 3]:
  - the step=1 grid is [Jan, Feb, Mar, Apr] (4 dates)
  - the step=3 grid is [Jan, Apr] (2 dates) -- a strict subset
so `n_obs` for h=1 (4 * 40 = 160) must differ from h=3 (2 * 40 = 80), directly
evidencing that each horizon's report used ITS OWN step=h grid, not a shared
union grid (design review B1 -- the t-inflation bug this whole design exists
to avoid repeating).
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from shortlist.backtest import cli
from shortlist.backtest.cli import _parse_residualize, main, run_residualize
from shortlist.backtest.prices import PriceHistory
from shortlist.backtest.signals import Observation

N = 40
TICKERS = [f"T{i:02d}" for i in range(N)]
_PERM = [24, 3, 12, 2, 38, 33, 6, 10, 18, 30, 35, 21, 39, 22, 28, 23, 11, 34, 19, 36,
         40, 15, 27, 20, 14, 13, 1, 16, 4, 7, 26, 25, 31, 29, 32, 8, 17, 5, 37, 9]
assert sorted(_PERM) == list(range(1, N + 1))          # sanity: a genuine permutation

CTRL1 = {TICKERS[i]: float(i + 1) for i in range(N)}
CTRL2 = {TICKERS[i]: float(_PERM[i]) for i in range(N)}
TARGET = {tk: 0.7 * CTRL1[tk] + 0.3 * CTRL2[tk] for tk in TICKERS}
_GROWTH = {TICKERS[i]: 0.0005 * (i + 1) for i in range(N)}   # monotonic in ctrl1's rank

MONTHS = [date(2020, m, 1) for m in range(1, 8)]        # Jan..Jul 2020
_SIGNAL_DATES = set(MONTHS[:4])                          # Jan..Apr: union of both grids


class _ResidualPlantedSource:
    """A SignalSource (per signals.py's protocol) emitting a literal
    {target, ctrl1, ctrl2} triple per (ticker, date) over the planted universe --
    no price reconstruction, matching test_backtest_golden.py's _PlantedSource."""
    name = "planted"

    def observe(self, ticker: str, as_of: date):
        if ticker not in TARGET or as_of not in _SIGNAL_DATES:
            return None
        return Observation(as_of, ticker,
                           {"target": TARGET[ticker], "ctrl1": CTRL1[ticker],
                            "ctrl2": CTRL2[ticker]})


def _planted_fixture():
    hists = {}
    for tk in TICKERS:
        closes = [1000.0 * ((1 + _GROWTH[tk]) ** k) for k in range(len(MONTHS))]
        hists[tk] = PriceHistory(tk, MONTHS, closes)
    spy = PriceHistory("SPY", MONTHS, [100.0] * len(MONTHS))   # flat -> excess == raw
    return _ResidualPlantedSource(), hists, spy


# --- _parse_residualize: pure spec-string validation ------------------------

def test_parse_residualize_ok():
    target, controls = _parse_residualize("net_debt_to_ebitda~growth,quality")
    assert target == "net_debt_to_ebitda"
    assert controls == ["growth", "quality"]


def test_parse_residualize_rejects_no_tilde():
    with pytest.raises(ValueError):
        _parse_residualize("target_with_no_tilde")


def test_parse_residualize_rejects_two_tildes():
    with pytest.raises(ValueError):
        _parse_residualize("a~b~c")


def test_parse_residualize_rejects_empty_target():
    with pytest.raises(ValueError):
        _parse_residualize("~ctrl1,ctrl2")


def test_parse_residualize_rejects_empty_controls():
    with pytest.raises(ValueError):
        _parse_residualize("target~")


# --- main()-level validation: argparse .error -> SystemExit(2) -------------

def test_main_rejects_residualize_bad_spec():
    with pytest.raises(SystemExit) as exc:
        main(["--tickers", "AAPL", "--residualize", "bad_spec_no_tilde"])
    assert exc.value.code == 2


def test_main_rejects_residualize_with_explicit_step_months():
    # The step-months guard must fire before any network/env dependency.
    with pytest.raises(SystemExit) as exc:
        main(["--tickers", "AAPL", "--residualize", "net_debt_to_ebitda~growth,quality",
              "--step-months", "2"])
    assert exc.value.code == 2


def test_main_rejects_residualize_unknown_xbrl_axis():
    with pytest.raises(SystemExit) as exc:
        main(["--tickers", "AAPL", "--source", "xbrl",
              "--residualize", "not_a_real_axis~growth,quality"])
    assert exc.value.code == 2


# --- run_residualize: planted end-to-end ------------------------------------

def test_run_residualize_three_signals_appear_per_horizon():
    src, hists, spy = _planted_fixture()
    extra_reports, _ = run_residualize(
        src, hists, spy, start=date(2020, 1, 1), end=date(2020, 4, 1),
        horizons=[1, 3], target="target", controls=["ctrl1", "ctrl2"])
    got = {(r.signal, r.horizon) for r in extra_reports}
    expected = {(f"target_{suffix}", h)
               for suffix in ("resid", "resid_level", "rawx") for h in (1, 3)}
    assert got == expected


def test_run_residualize_per_horizon_grids_differ():
    src, hists, spy = _planted_fixture()
    extra_reports, _ = run_residualize(
        src, hists, spy, start=date(2020, 1, 1), end=date(2020, 4, 1),
        horizons=[1, 3], target="target", controls=["ctrl1", "ctrl2"])
    by_key = {(r.signal, r.horizon): r for r in extra_reports}
    # step=1 grid: 4 dates x 40 names; step=3 grid: 2 dates x 40 names -- distinct
    # n_obs proves each horizon aggregated over its OWN non-overlapping grid.
    for suffix in ("resid", "resid_level", "rawx"):
        assert by_key[(f"target_{suffix}", 1)].n_obs == 4 * N
        assert by_key[(f"target_{suffix}", 3)].n_obs == 2 * N
        assert by_key[(f"target_{suffix}", 1)].breadth == float(N)
        assert by_key[(f"target_{suffix}", 3)].breadth == float(N)


def test_run_residualize_discriminating_fixture():
    # target is an EXACT linear re-encoding of the controls -> after controlling for
    # ctrl1/ctrl2, nothing predictive is left (rank-noise), while the raw target
    # (dominated 70% by ctrl1, which drives forward returns) is strongly predictive.
    src, hists, spy = _planted_fixture()
    extra_reports, _ = run_residualize(
        src, hists, spy, start=date(2020, 1, 1), end=date(2020, 4, 1),
        horizons=[1, 3], target="target", controls=["ctrl1", "ctrl2"])
    by_key = {(r.signal, r.horizon): r for r in extra_reports}
    for h in (1, 3):
        rawx = by_key[("target_rawx", h)]
        resid = by_key[("target_resid", h)]
        assert rawx.xs_ic is not None
        assert rawx.xs_ic.mean > 0.8
        assert resid.xs_ic is not None
        assert abs(resid.xs_ic.mean) < 0.15


def test_run_residualize_json_block_shape():
    src, hists, spy = _planted_fixture()
    _, residualized = run_residualize(
        src, hists, spy, start=date(2020, 1, 1), end=date(2020, 4, 1),
        horizons=[1, 3], target="target", controls=["ctrl1", "ctrl2"])
    assert residualized["target"] == "target"
    assert residualized["controls"] == ["ctrl1", "ctrl2"]
    assert residualized["overlap_fraction"] is not None
    assert 0.0 <= residualized["overlap_fraction"] <= 1.0
    assert set(residualized["diagnostics"]) == {"rank", "level"}
    for method in ("rank", "level"):
        diag = residualized["diagnostics"][method]
        assert set(diag) == {"skipped_floor", "skipped_singular", "n_dates",
                             "mean_r2", "beta_std"}
        assert diag["n_dates"] == 4          # union grid: Jan,Feb,Mar,Apr all fit
        assert diag["skipped_floor"] == 0
        assert diag["skipped_singular"] == 0
    assert set(residualized["paired_ic_diff"]) == {"1", "3"}
    for h_key in ("1", "3"):
        pid = residualized["paired_ic_diff"][h_key]
        assert pid is not None
        assert set(pid) == {"mean", "std", "icir", "t_stat", "hit_rate", "n"}


# --- full CLI wiring: main() end-to-end with a planted source --------------

def test_main_residualize_end_to_end_json(monkeypatch, capsys):
    # Also proves the default step_months=0 is ACCEPTED alongside --residualize
    # (rc == 0, no ap.error) -- only an explicit override is rejected.
    src, hists, spy = _planted_fixture()

    async def fake_load(tickers, cache_dir, today):
        return hists, spy

    monkeypatch.setattr(cli, "_load_histories", fake_load)
    monkeypatch.setattr(cli, "MomentumSignalSource", lambda *a, **k: src)

    rc = cli.main([
        "--tickers", ",".join(sorted(hists)),
        "--source", "momentum",
        "--horizons", "1,3",
        "--start", "2020-01-01",
        "--end", "2020-04-01",
        "--residualize", "target~ctrl1,ctrl2",
        "--json",
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    signals = {s["signal"] for s in out["signals"]}
    assert {"target_resid", "target_resid_level", "target_rawx"} <= signals
    assert "residualized" in out
    r = out["residualized"]
    assert r["target"] == "target"
    assert r["controls"] == ["ctrl1", "ctrl2"]
    assert set(r["diagnostics"]) == {"rank", "level"}
    assert set(r["paired_ic_diff"]) == {"1", "3"}

"""shortlist-backtest — validate scores against forward returns (rank IC + spreads)."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import yaml

from ..env import load_env
from .engine import collect_observations, observation_grid, run_backtest
from .metrics import cross_signal_xs_corr
from .prices import _UA, PriceHistory, _add_months, fetch_history
from .report import render_table, report_to_dict
from .signals import MomentumSignalSource, XbrlSignalSource
from .universe import load_universe
from .xbrl import cik_for, fetch_cik_index, fetch_companyfacts, read_companyfacts_cache

# Collinearity diagnostic pairs (candidate axis, already-scored axis). Each is checked
# for cross-sectional rank correlation; >~_COLLINEARITY_REDUNDANT means the candidate
# duplicates the existing axis (the EV/EBIT-vs-fcf_yield precedent: don't-ship a
# correlated leg). Add a pair here when proposing a new standalone axis for scoring.
_COLLINEARITY_PAIRS = [
    ("ebit_ev_yield", "value_fcf_yield"),   # EV/EBIT leg vs absolute fcf yield (spec §11)
    ("net_debt_to_ebitda", "growth"),       # leverage vs scored growth (measured corr ~0.54)
    ("net_debt_to_ebitda", "value"),        # leverage vs scored value
    ("net_debt_to_ebitda", "quality"),      # leverage vs scored quality
    ("accruals", "piotroski"),              # accruals vs Piotroski CFO>NI overlap (§3)
    ("asset_growth", "growth"),             # asset growth vs scored growth (§3)
    ("shareholder_yield", "value_fcf_yield"),  # total payout vs cash GENERATED (§5; the standalone fcf-yield axis)
    ("shareholder_yield", "share_count"),      # buyback leg is the dollar-twin of dilution (§5)
    ("sue", "momentum"),                       # earnings-surprise drift vs price momentum (§1) — SNAPSHOT-REPLAY only
    ("residual_momentum", "momentum"),         # idiosyncratic vs raw 12-1 momentum (§2) — WILL correlate; the point is it dominates on rank IC
    # PREDICTIVE_SIGNALS §2 price-refinement measurement axes — the load-bearing duplication
    # checks (|corr| >= 0.5 => reject regardless of IC; EV/EBIT precedent).
    ("pct_to_52w_high", "price_vs_200dma"),    # both are close/(trailing ref) — the key 52wk-high dup check
    ("pct_to_52w_high", "rel_strength_6m"),
    ("pct_to_52w_high", "momentum"),
    ("max_daily_return", "momentum"),          # defensive/lottery vs trend (expect low corr)
    ("vol_scaled_momentum", "residual_momentum"),  # cousins — the key vol-scaled dup check
    ("vol_scaled_momentum", "price_vs_200dma"),
    ("vol_scaled_momentum", "momentum"),
]
_COLLINEARITY_REDUNDANT = 0.5   # |corr| at/above this => the candidate is redundant


def _collinearity(diag_obs) -> dict[str, float]:
    """corr(a, b) for each diagnostic pair over co-present names, keyed 'a~b'. Pairs
    with no co-present data (cross_signal_xs_corr -> None) are skipped."""
    out: dict[str, float] = {}
    for a, b in _COLLINEARITY_PAIRS:
        c = cross_signal_xs_corr(diag_obs, a, b)
        if c is not None:
            out[f"{a}~{b}"] = c
    return out


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="shortlist-backtest",
        description="Validate screener scores against forward returns.")
    ap.add_argument("--universe", default="largecap",
                    help="'largecap' (bundled) or comma-separated tickers")
    ap.add_argument("--tickers", help="alias for an ad-hoc --universe CSV")
    ap.add_argument("--horizons", default="3",
                    help="comma-separated months, e.g. 1,3,6,12")
    ap.add_argument("--buckets", type=int, default=5)
    ap.add_argument("--return-mode", dest="return_mode",
                    choices=["excess", "raw"], default="excess")
    ap.add_argument("--source", choices=["momentum", "snapshot", "xbrl"],
                    default="momentum")
    ap.add_argument("--xbrl-cache-dir", dest="xbrl_cache_dir",
                    default=".cache/sec_xbrl",
                    help="disk cache for companyfacts + the SEC ticker map")
    ap.add_argument("--step-months", dest="step_months", type=int, default=0,
                    help="grid spacing; 0 = non-overlapping (= max horizon)")
    ap.add_argument("--start", help="grid start YYYY-MM-DD (default ~ earliest usable)")
    ap.add_argument("--end", help="grid end YYYY-MM-DD (default today)")
    ap.add_argument("--config", default=str(Path(__file__).parents[3] / "config.yaml"))
    ap.add_argument("--cache-dir", dest="cache_dir", default=".cache/yahoo")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--csv", help="write per-signal rows to this CSV path")
    ap.add_argument("--fit", action="store_true",
                    help="fit fundamental weights walk-forward and print a proposal "
                         "(requires --source xbrl); never writes config.yaml")
    ap.add_argument("--fit-horizon", dest="fit_horizon", type=int,
                    help="forward-return horizon (months) to fit at; required with --fit")
    ap.add_argument("--fit-axes", dest="fit_axes",
                    default=",".join(_FUNDAMENTAL_AXES),
                    help="comma-separated axes to fit (subset of the fundamentals)")
    ap.add_argument("--n-folds", dest="n_folds", type=int, default=6,
                    help="walk-forward folds; >=6 needed to reach the 5-OOS-fold gate")
    ap.add_argument("--shrink", type=float, default=0.5,
                    help="shrinkage toward the prior (0..1)")
    return ap


async def _load_companyfacts(tickers, cache_dir, month):
    """WARM the companyfacts disk cache for the universe (one fetch per ticker,
    written to `CIK{cik}-{month}.json`) WITHOUT retaining the payloads in memory —
    the source reads them back lazily one at a time (memory-bounded). Returns
    `(resolved_tickers, cik_index)`. Keyless; SEC fair-access requires a descriptive
    User-Agent carrying a contact email. IFRS 20-F foreign issuers have no us-gaap
    facts and degrade to a skip."""
    identity = os.environ.get("SEC_IDENTITY")
    if not identity:
        raise RuntimeError(
            "SEC_IDENTITY (a contact email) is required by the SEC for the XBRL "
            "source — set it in .env, e.g. SEC_IDENTITY='you@example.com'.")
    resolved: list[str] = []
    async with httpx.AsyncClient(headers={"User-Agent": identity},
                                 timeout=30.0) as client:
        try:
            index = await fetch_cik_index(client, cache_dir=cache_dir, month=month)
        except Exception as e:
            print(f"warn: SEC ticker map fetch failed: {type(e).__name__}",
                  file=sys.stderr)
            return [], {}
        for tk in tickers:
            cik = cik_for(tk, index)
            if cik is None:
                print(f"warn: {tk} not in SEC ticker map", file=sys.stderr)
                continue
            try:
                cf = await fetch_companyfacts(cik, client, cache_dir=cache_dir,
                                              month=month)
                if cf is not None:
                    resolved.append(tk)        # disk cache now warm; cf is NOT retained
                else:
                    print(f"warn: {tk} has no us-gaap companyfacts "
                          f"(IFRS/20-F foreign issuer or recent spin-off)",
                          file=sys.stderr)
            except Exception as e:
                print(f"warn: {tk} companyfacts fetch failed: {type(e).__name__}",
                      file=sys.stderr)
    return resolved, index


async def _load_histories(tickers, cache_dir, today):
    async with httpx.AsyncClient(headers={"User-Agent": _UA}) as client:
        try:
            spy = await fetch_history("SPY", client, cache_dir=cache_dir, today=today)
        except Exception as e:               # empty spy => main()'s `not spy.dates` clean exit
            print(f"warn: SPY benchmark fetch failed: {type(e).__name__}",
                  file=sys.stderr)
            spy = PriceHistory("SPY", [], [])
        hists = {}
        for tk in tickers:
            try:
                hists[tk] = await fetch_history(tk, client, cache_dir=cache_dir,
                                                today=today)
            except Exception as e:               # keep going; report coverage
                print(f"warn: {tk} price fetch failed: {type(e).__name__}",
                      file=sys.stderr)
    return hists, spy


def _grid_start(earliest: date) -> date:
    # need >= 200 trading days before the first signal; ~14 calendar months
    # comfortably clears 200 trading days (11 months ~= 160, too few).
    return _add_months(earliest, 14)


def _write_csv(report, path):
    import csv

    d = report_to_dict(report)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["signal", "horizon", "ts_ic_mean", "ts_t", "xs_ic_mean",
                    "xs_t", "hit_rate", "spread", "n_obs", "breadth"])
        for s in d["signals"]:
            ts, xs = s["ts_ic"], s["xs_ic"]
            w.writerow([s["signal"], s["horizon"],
                        ts["mean"] if ts else "", ts["t_stat"] if ts else "",
                        xs["mean"] if xs else "", xs["t_stat"] if xs else "",
                        ts["hit_rate"] if ts else "",
                        s["spread"]["spread"] if s["spread"] else "",
                        s["n_obs"], s["breadth"]])


_FUNDAMENTAL_AXES = ("quality", "moat", "growth", "value")


def _fit_prior_from_config(config: dict, fit_axes: list[str]) -> tuple[dict, float]:
    """Prior = config weights filtered to EXACTLY fit_axes (never the full 7-axis block,
    which would let momentum/insider/risk contaminate the fundamental fit). Returns
    (prior, S_f) where S_f is the fundamental block's current ratio weight."""
    weights = config["weights"]
    prior = {a: weights[a] for a in fit_axes}
    return prior, sum(prior.values())


def _run_fit(args, src, hists, spy, start, end, config) -> int:
    from .fit import FitGuardError, fit_weights
    from .fit_data import build_fit_rows
    from .metrics import spearman_ic
    from .report import evaluate_gate, fit_report_to_dict, render_fit_report

    fit_axes = [a.strip() for a in args.fit_axes.split(",") if a.strip()]
    bad = [a for a in fit_axes if a not in _FUNDAMENTAL_AXES]
    if bad:
        print(f"--fit-axes must be a subset of {_FUNDAMENTAL_AXES}; got {bad}",
              file=sys.stderr)
        return 2
    prior, s_f = _fit_prior_from_config(config, fit_axes)
    rows = build_fit_rows(src, sorted(hists), hists, spy, start=start, end=end,
                          horizon=args.fit_horizon, axes=fit_axes,
                          return_mode=args.return_mode)
    try:
        result = fit_weights(rows, prior, min_periods=36, shrink=args.shrink,
                             n_folds=args.n_folds,
                             min_period_gap_days=args.fit_horizon * 28)
    except FitGuardError as e:
        print(str(e), file=sys.stderr)
        print("evidence insufficient — do not change config", file=sys.stderr)
        return 0
    axis_ic = {a: spearman_ic([r[1][a] for r in rows], [r[2] for r in rows])
               for a in fit_axes}
    verdict = evaluate_gate(result)
    if args.json:
        print(json.dumps(fit_report_to_dict(result, prior=prior, s_f=s_f,
                                             horizon=args.fit_horizon, axes=fit_axes,
                                             axis_ic=axis_ic, verdict=verdict), indent=2))
    else:
        print(render_fit_report(result, prior=prior, s_f=s_f, horizon=args.fit_horizon,
                                axes=fit_axes, axis_ic=axis_ic, verdict=verdict),
              file=sys.stderr)
    return 0


def main(argv=None) -> int:
    load_env()  # pick up SEC_IDENTITY / API keys from a .env file if present
    args = build_arg_parser().parse_args(argv)
    # Flag-combination validation first — must NOT depend on SEC_IDENTITY, or the SEC
    # guard below could return 2 first and a --fit-horizon test would pass for the wrong reason.
    if args.fit and args.source != "xbrl":
        print("--fit requires --source xbrl (fundamental axes come from XBRL)",
              file=sys.stderr)
        return 2
    if args.fit and args.fit_horizon is None:
        print("--fit requires --fit-horizon (months); fitted ratios are horizon-conditional",
              file=sys.stderr)
        return 2
    if args.source == "snapshot":
        print("snapshot source is GATED: no organic point-in-time snapshot history "
              "exists yet (needs >= 24 daily captures). Use --source momentum.",
              file=sys.stderr)
        return 2

    if args.source == "xbrl" and not os.environ.get("SEC_IDENTITY"):
        print("SEC_IDENTITY (a contact email) is required for --source xbrl — "
              "set it in .env, e.g. SEC_IDENTITY='you@example.com'.", file=sys.stderr)
        return 2

    tickers = load_universe(args.tickers or args.universe)
    try:
        horizons = [int(h) for h in args.horizons.split(",") if h.strip()]
    except ValueError:
        print("--horizons must be comma-separated integer months, e.g. 1,3,6,12",
              file=sys.stderr)
        return 2
    if not horizons:
        print("--horizons must contain at least one integer month", file=sys.stderr)
        return 2
    if any(h < 1 for h in horizons):
        print("--horizons must be positive integer months (>= 1)", file=sys.stderr)
        return 2
    today = datetime.now(tz=timezone.utc).date().isoformat()
    config = yaml.safe_load(Path(args.config).read_text())
    thresholds = config["thresholds"]

    hists, spy = asyncio.run(_load_histories(tickers, args.cache_dir, today))
    hists = {k: v for k, v in hists.items() if v.dates}
    if not hists or not spy.dates:
        print("no price history available", file=sys.stderr)
        return 1

    earliest = min(min(h.dates) for h in hists.values())
    start = date.fromisoformat(args.start) if args.start else _grid_start(earliest)
    end = date.fromisoformat(args.end) if args.end else date.fromisoformat(today)
    if start > end:
        print("--start must be <= --end", file=sys.stderr)
        return 2

    if args.source == "xbrl":
        month = today[:7]   # YYYY-MM — companyfacts is month-cached
        resolved, cik_index = asyncio.run(
            _load_companyfacts(tickers, args.xbrl_cache_dir, month))
        if not resolved:
            print("no companyfacts available for the universe", file=sys.stderr)
            return 1
        # Lazy loader: read one ticker's (already-warmed) companyfacts from disk on
        # demand. With the engine's ticker-major iteration, each loads once -> RAM is
        # bounded to the source's small LRU instead of the whole universe.
        _cdir, _month = args.xbrl_cache_dir, month

        def _fact_loader(tk, _idx=cik_index):
            return read_companyfacts_cache(
                cik_for(tk, _idx), cache_dir=_cdir, month=_month)

        src = XbrlSignalSource(None, hists, thresholds, fact_loader=_fact_loader)
    else:
        src = MomentumSignalSource(hists, spy, thresholds, min_history=200)
    if args.fit:
        return _run_fit(args, src, hists, spy, start, end, config)
    report = run_backtest([src], hists, spy, start=start, end=end,
                          horizons=horizons, step_months=(args.step_months or None),
                          n_buckets=args.buckets, return_mode=args.return_mode,
                          price_asof=date.fromisoformat(today))

    # Collinearity diagnostics: does a candidate standalone axis duplicate an
    # already-scored one? A high cross-sectional rank corr (>~0.5) means the candidate
    # adds a CORRELATED bet, not new signal, and would dilute the composite rather than
    # improve it (the EV/EBIT-vs-fcf_yield precedent: corr 0.72 -> don't-ship). Runs on
    # both the XBRL and momentum paths: the momentum source's candidate axes
    # (residual_momentum and the §2 price-refinement axes pct_to_52w_high /
    # max_daily_return / vol_scaled_momentum) are duplication-checked against the scored
    # momentum sub-score and its legs (price_vs_200dma / rel_strength_6m) on the
    # live-price path, exactly as ebit_ev_yield was checked on the XBRL path.
    # Printed to stderr so --json stays clean.
    collinearity: dict[str, float] = {}
    if args.source in ("xbrl", "momentum"):
        diag_grid = observation_grid(start, end, args.step_months or horizons[0])
        diag_obs = collect_observations(src, sorted(hists.keys()), diag_grid)
        collinearity = _collinearity(diag_obs)
        for pair, c in collinearity.items():
            warn = "  <-- >~0.5: largely duplicates, would dilute not add" if abs(c) >= _COLLINEARITY_REDUNDANT else ""
            print(f"Leg collinearity  corr({pair}) = {c:+.3f}{warn}", file=sys.stderr)

    if args.csv:
        _write_csv(report, args.csv)
    if args.json:
        d = report_to_dict(report)
        if collinearity:
            d["collinearity"] = collinearity
        print(json.dumps(d, indent=2))
    else:
        print(render_table(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

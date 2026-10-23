"""shortlist-backtest — validate scores against forward returns (rank IC + spreads)."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from ..config import ConfigError, load_config
from ..data.store import capture_days
from ..env import load_env
from .engine import (
    _TRUST_MIN_BREADTH,
    _signal_report,
    collect_observations,
    fwd_return,
    observation_grid,
    run_backtest,
)
from .metrics import aggregate_ic, cross_signal_xs_corr, spearman_ic
from .prices import _UA, PriceHistory, _add_months, fetch_history
from .report import _ic_dict, render_table, report_to_dict
from .residual import residual_rows
from .signals import MomentumSignalSource, SnapshotSignalSource, XbrlSignalSource
from .universe import is_named_universe, load_universe, stale_tickers
from .xbrl import cik_for, fetch_cik_index, fetch_companyfacts, read_companyfacts_cache

_RESIDUALIZE_MIN_NAMES = 10   # deliberately fixed, not a CLI flag

# Minimum store-wide captured days before --source snapshot will run.
_SNAPSHOT_MIN_CAPTURE_DAYS = 24

# Collinearity diagnostic pairs (candidate axis, already-scored axis). A cross-sectional
# rank correlation above _COLLINEARITY_REDUNDANT means the candidate duplicates the
# scored axis. Add a pair when proposing a new standalone axis.
_COLLINEARITY_PAIRS = [
    ("ebit_ev_yield", "value_fcf_yield"),
    ("net_debt_to_ebitda", "growth"),
    ("net_debt_to_ebitda", "value"),
    ("net_debt_to_ebitda", "quality"),
    ("accruals", "piotroski"),              # overlaps Piotroski CFO>NI
    ("asset_growth", "growth"),
    ("shareholder_yield", "value_fcf_yield"),  # total payout vs cash generated
    ("shareholder_yield", "share_count"),      # buyback leg is the dollar-twin of dilution
    ("sue", "momentum"),                       # snapshot replay only
    ("residual_momentum", "momentum"),         # expected to correlate; the test is rank IC
    # Price-refinement axes: |corr| >= 0.5 rejects the candidate regardless of IC.
    ("pct_to_52w_high", "price_vs_200dma"),    # both are close / trailing reference
    ("pct_to_52w_high", "rel_strength_6m"),
    ("pct_to_52w_high", "momentum"),
    ("max_daily_return", "momentum"),          # lottery vs trend; expect low corr
    ("vol_scaled_momentum", "residual_momentum"),
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
                    help="'largecap'/'smallmid' (bundled) or comma-separated tickers")
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
    ap.add_argument("--store-root", dest="store_root", default="snapshots",
                    help="accumulated point-in-time snapshot store (--source snapshot); "
                         "same default as shortlist-accumulate --root")
    ap.add_argument("--step-months", dest="step_months", type=int, default=0,
                    help="grid spacing; 0 = non-overlapping (= max horizon)")
    ap.add_argument("--residualize", dest="residualize",
                    help="TARGET~CTRL1,CTRL2 -- measure TARGET's partial rank IC after "
                         "removing linear exposure to CTRL1,CTRL2 "
                         "(docs/BACKTEST.md). Incompatible with an "
                         "explicit --step-months override (the residual grid must track "
                         "each horizon's own non-overlapping step).")
    ap.add_argument("--start", help="grid start YYYY-MM-DD (default ~ earliest usable)")
    ap.add_argument("--end", help="grid end YYYY-MM-DD (default today)")
    ap.add_argument("--config", default=str(Path(__file__).parents[3] / "config.yaml"))
    ap.add_argument("--cache-dir", dest="cache_dir", default=".cache/yahoo")
    ap.add_argument("--allow-stale-universe", action="store_true",
                    help="proceed even if universe symbols are absent from SEC's "
                         "current ticker map (renamed/delisted names contribute nothing)")
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


def _known_symbols(cache_dir: str) -> dict[str, str]:
    """{TICKER -> CIK} from SEC's company_tickers.json, month-cached on disk by
    `fetch_company_tickers_raw`, so this costs one request per month rather than one
    per run. Returns {} on ANY failure — the freshness check must abstain rather than
    block a backtest because SEC is unreachable (`stale_tickers` treats {} as
    "could not check", not "everything is dead")."""
    from .xbrl import build_cik_index, fetch_company_tickers_raw

    async def go():
        async with httpx.AsyncClient(headers={"User-Agent": _UA}, timeout=30.0) as c:
            raw = await fetch_company_tickers_raw(
                c, cache_dir=cache_dir, month=date.today().strftime("%Y-%m"))
        return build_cik_index(raw)
    try:
        return asyncio.run(go())
    except Exception:
        return {}


async def _load_companyfacts(tickers, cache_dir, month) -> tuple[list[str], dict[str, str]]:
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


async def _load_histories(tickers, cache_dir, today) -> tuple[dict[str, PriceHistory], PriceHistory]:
    # SERIAL BY DESIGN. Concurrency is not a known trigger for the Yahoo WAF block (that
    # block is a header-shape fingerprint on the screener endpoint, and this calls the
    # chart endpoint). Serial is the conservative default for an unofficial endpoint.
    # If wall-clock becomes a bottleneck, bound a gather with asyncio.Semaphore(3-5).
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


def _write_csv(report, path) -> None:
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
    from .report import evaluate_gate, fit_report_to_dict, render_fit_report

    fit_axes = [a.strip() for a in args.fit_axes.split(",") if a.strip()]
    bad = [a for a in fit_axes if a not in _FUNDAMENTAL_AXES]
    if bad:
        print(f"--fit-axes must be a subset of {_FUNDAMENTAL_AXES}; got {bad}",
              file=sys.stderr)
        return 2
    try:
        prior, s_f = _fit_prior_from_config(config, fit_axes)
    except (KeyError, TypeError) as e:
        print(f"config {args.config}: 'weights' block is missing/invalid for fit "
              f"axes {fit_axes}: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
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


def _parse_residualize(spec: str) -> tuple[str, list[str]]:
    """Parse `TARGET~CTRL1,CTRL2`. Raises ValueError on a malformed spec; main()
    turns that into an argparse `ap.error` (exit code 2), never a raw traceback."""
    parts = spec.split("~")
    if len(parts) != 2:
        raise ValueError(
            f"--residualize must have exactly one '~' (TARGET~CTRL1,CTRL2); got {spec!r}")
    target, ctrl_str = parts
    target = target.strip()
    controls = [c.strip() for c in ctrl_str.split(",") if c.strip()]
    if not target:
        raise ValueError(f"--residualize target is empty in {spec!r}")
    if not controls:
        raise ValueError(f"--residualize needs at least one control in {spec!r}")
    return target, controls


def _raw_target_intersection(observations, target, controls) -> dict[date, dict[str, float]]:
    """Raw `target` values restricted to the SAME co-presence set `residual_rows`
    uses (target AND every control present) — the paired `_rawx` baseline.
    Independent of `residual_rows`' regression floor/singular skips, which only
    gate the residual computation, not this raw baseline."""
    out: dict = defaultdict(dict)
    for obs in observations:
        sigs = obs.signals
        if sigs.get(target) is None:
            continue
        if any(sigs.get(c) is None for c in controls):
            continue
        out[obs.as_of][obs.ticker] = sigs[target]
    return dict(out)


def _overlap_fraction(observations, target, raw_intersection) -> Optional[float]:
    """Co-present names / names-with-target-present, averaged over dates where the
    target is present at all."""
    target_present: dict = defaultdict(int)
    for obs in observations:
        if obs.signals.get(target) is not None:
            target_present[obs.as_of] += 1
    fracs = []
    for d, n_present in target_present.items():
        if n_present <= 0:
            continue
        fracs.append(len(raw_intersection.get(d, {})) / n_present)
    return (sum(fracs) / len(fracs)) if fracs else None


def _grid_join(grid, rows_by_date, hists, spy, horizon, return_mode) -> list[tuple[date, str, float, float]]:
    """Join a `{date: {ticker: value}}` map to a horizon's OWN step=h grid dates +
    forward returns, via the same `fwd_return` the engine uses — the per-horizon
    join that keeps each horizon's aggregation confined to its own non-overlapping
    grid, never the finer union grid."""
    out = []
    for t in grid:
        vals = rows_by_date.get(t)
        if not vals:
            continue
        for tk, sv in vals.items():
            hist = hists.get(tk)
            if hist is None:
                continue
            fr = fwd_return(hist, spy, t, horizon, return_mode)
            if fr is None:
                continue
            out.append((t, tk, sv, fr))
    return out


def _per_date_ic(rows) -> dict[date, float]:
    """Per-date Spearman IC with NO breadth floor (unlike `_signal_report`'s xs_ic,
    which suppresses below `xs_min_breadth`) — used only for the paired per-date
    IC-difference diagnostic, which pairs on whatever dates both series share."""
    by_date: dict = defaultdict(list)
    for t, _tk, sv, fr in rows:
        by_date[t].append((sv, fr))
    out = {}
    for t, pairs in by_date.items():
        ic = spearman_ic([p[0] for p in pairs], [p[1] for p in pairs])
        if ic is not None:
            out[t] = ic
    return out


def run_residualize(src, hists, spy, *, start: date, end: date, horizons: list[int],
                    target: str, controls: list[str], return_mode: str = "excess",
                    n_buckets: int = 5) -> tuple[list, dict]:
    """Compute residuals once on a union-grid pass, then join them per horizon to that
    horizon's own step=h grid. Emits three extra SignalReports: `<target>_resid` (rank,
    primary), `<target>_resid_level` (secondary) and `<target>_rawx` (paired raw baseline).
    Returns `(extra_reports, residualized_json)`. Pure given its inputs."""
    union_dates = sorted(set().union(*(observation_grid(start, end, h) for h in horizons)))
    union_obs = collect_observations(src, sorted(hists.keys()), union_dates)

    rows_rank, diag_rank = residual_rows(
        union_obs, target, controls, min_names=_RESIDUALIZE_MIN_NAMES, method="rank")
    rows_level, diag_level = residual_rows(
        union_obs, target, controls, min_names=_RESIDUALIZE_MIN_NAMES, method="level")
    raw_intersection = _raw_target_intersection(union_obs, target, controls)
    overlap_fraction = _overlap_fraction(union_obs, target, raw_intersection)

    extra_reports = []
    paired_ic_diff: dict = {}
    for h in horizons:
        grid_h = observation_grid(start, end, h)
        resid_rows_h = _grid_join(grid_h, rows_rank, hists, spy, h, return_mode)
        level_rows_h = _grid_join(grid_h, rows_level, hists, spy, h, return_mode)
        rawx_rows_h = _grid_join(grid_h, raw_intersection, hists, spy, h, return_mode)

        extra_reports.append(_signal_report(
            f"{target}_resid", h, resid_rows_h,
            xs_min_breadth=_TRUST_MIN_BREADTH, n_buckets=n_buckets))
        extra_reports.append(_signal_report(
            f"{target}_resid_level", h, level_rows_h,
            xs_min_breadth=_TRUST_MIN_BREADTH, n_buckets=n_buckets))
        extra_reports.append(_signal_report(
            f"{target}_rawx", h, rawx_rows_h,
            xs_min_breadth=_TRUST_MIN_BREADTH, n_buckets=n_buckets))

        # Paired over dates where both the rawx and residual IC exist on this horizon's
        # grid, never two independently sampled numbers.
        resid_ic_by_date = _per_date_ic(resid_rows_h)
        rawx_ic_by_date = _per_date_ic(rawx_rows_h)
        common = sorted(set(resid_ic_by_date) & set(rawx_ic_by_date))
        diffs = [rawx_ic_by_date[t] - resid_ic_by_date[t] for t in common]
        paired_ic_diff[str(h)] = _ic_dict(aggregate_ic(diffs)) if diffs else None

    residualized_json = {
        "target": target,
        "controls": controls,
        "overlap_fraction": overlap_fraction,
        "diagnostics": {"rank": diag_rank, "level": diag_level},
        "paired_ic_diff": paired_ic_diff,
    }
    return extra_reports, residualized_json


def main(argv=None) -> int:  # noqa: C901 — argparse surface + flat subcommand dispatch
    load_env()  # pick up SEC_IDENTITY / API keys from a .env file if present
    ap = build_arg_parser()
    args = ap.parse_args(argv)
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
        # Activates automatically off real store history. The stub this replaces
        # asserted emptiness unconditionally and never inspected the store, so it
        # kept refusing 23 days after the store passed its own stated bar. Report
        # the count found: a refusal has to be falsifiable.
        n_days = len(capture_days(args.store_root))
        if n_days < _SNAPSHOT_MIN_CAPTURE_DAYS:
            print(f"--source snapshot needs >= {_SNAPSHOT_MIN_CAPTURE_DAYS} captured days "
                  f"in the store; {args.store_root!r} has {n_days}. Run "
                  f"shortlist-accumulate, point --store-root at the real store, or use "
                  f"--source momentum.", file=sys.stderr)
            return 2
    if args.fit and args.fit_horizon < 1:
        # A 0-month horizon would loop observation_grid forever.
        print("--fit-horizon must be a positive integer month (>= 1)", file=sys.stderr)
        return 2

    residualize: tuple[str, list[str]] | None = None
    if args.residualize:
        # An explicit --step-months puts the residuals on a different grid than the
        # per-horizon non-overlapping step. The default 0 is falsy.
        if args.step_months:
            ap.error("--residualize cannot be combined with an explicit --step-months "
                     "override (the residual grid must track each horizon's own "
                     "non-overlapping step)")
        try:
            target, controls = _parse_residualize(args.residualize)
        except ValueError as e:
            ap.error(str(e))
        else:
            if args.source == "xbrl":
                known = set(XbrlSignalSource._AXES)
                bad = [a for a in (target, *controls) if a not in known]
                if bad:
                    ap.error(f"--residualize names must be known --source xbrl axes "
                             f"(unknown: {bad}; known: {sorted(known)})")
            residualize = (target, controls)

    if args.source == "xbrl" and not os.environ.get("SEC_IDENTITY"):
        print("SEC_IDENTITY (a contact email) is required for --source xbrl — "
              "set it in .env, e.g. SEC_IDENTITY='you@example.com'.", file=sys.stderr)
        return 2

    tickers = load_universe(args.tickers or args.universe)
    _spec = args.tickers or args.universe
    if is_named_universe(_spec) and not args.allow_stale_universe:
        dead = stale_tickers(tickers, _known_symbols(args.cache_dir))
        if dead:
            print(
                f"universe contains {len(dead)} symbol(s) absent from SEC's current "
                f"ticker map: {', '.join(dead)}\n"
                "Each is renamed or delisted, so it yields no data and silently shrinks "
                "the cross-section — which is why this aborts at measurement time rather "
                "than producing a quietly narrower result.\n"
                "Fix the universe file (a rename keeps the same CIK), or re-run with "
                "--allow-stale-universe to proceed anyway.", file=sys.stderr)
            return 2
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
    for flag, value in (("--start", args.start), ("--end", args.end)):
        if value:
            try:
                date.fromisoformat(value)
            except ValueError:
                print(f"{flag} must be an ISO date (YYYY-MM-DD); got {value!r}",
                      file=sys.stderr)
                return 2
    today = datetime.now(tz=timezone.utc).date().isoformat()
    # 'weights' is read only by the --fit path, which guards its own missing key.
    try:
        config = load_config(args.config, required_keys=("thresholds",))
    except ConfigError as e:
        print(f"shortlist-backtest: {e}", file=sys.stderr)
        return 2
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

        def _fact_loader(tk, _idx=cik_index) -> Optional[dict]:
            return read_companyfacts_cache(
                cik_for(tk, _idx), cache_dir=_cdir, month=_month)

        src = XbrlSignalSource(None, hists, thresholds, fact_loader=_fact_loader)
    elif args.source == "snapshot":
        # Forward returns still come from `hists`: the store's span bounds the
        # observation grid only. Without this branch, `snapshot` would fall through to
        # the momentum source.
        src = SnapshotSignalSource(args.store_root, config)
    else:
        src = MomentumSignalSource(hists, spy, thresholds, min_history=200)
    if args.fit:
        return _run_fit(args, src, hists, spy, start, end, config)
    report = run_backtest([src], hists, spy, start=start, end=end,
                          horizons=horizons, step_months=(args.step_months or None),
                          n_buckets=args.buckets, return_mode=args.return_mode,
                          price_asof=date.fromisoformat(today))

    residualized_json = None
    if residualize:
        res_target, res_controls = residualize
        extra_reports, residualized_json = run_residualize(
            src, hists, spy, start=start, end=end, horizons=horizons,
            target=res_target, controls=res_controls,
            return_mode=args.return_mode, n_buckets=args.buckets)
        report.reports.extend(extra_reports)

    # Collinearity diagnostics for candidate axes (XBRL and momentum paths). Printed to
    # stderr so --json stays clean.
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
        if residualized_json is not None:
            d["residualized"] = residualized_json
        print(json.dumps(d, indent=2))
    else:
        print(render_table(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

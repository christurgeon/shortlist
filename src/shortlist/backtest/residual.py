"""Pure per-date partial residualization for the residualized-IC measurement
(docs/superpowers/specs/2026-07-05-leverage-residualized-ic-design.md, Method
items 2/3). Regresses a target signal on one or more control signals,
date by date, and keeps the residual — the part of the target NOT explained by
the controls — so a downstream IC computation can ask whether the target is
predictive INCREMENTAL to the controls, not merely correlated with them.

Rank convention: matches `metrics.py:spearman_ic`, which ranks via
`metrics.rank` — 1-based, AVERAGE-tie handling (not ordinal/first-occurrence).
`method="rank"` reuses that exact function so ties are broken identically to
every other rank-IC computation in this codebase.

Reuses `backtest/_ols.py:ols`/`_residuals` (stdlib Gaussian-elimination OLS,
already used for FF3 alpha) rather than reimplementing a solver.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from statistics import pstdev

from ._ols import _residuals, ols
from .metrics import rank
from .signals import Observation


def residual_rows(
    observations: list[Observation],
    target: str,
    controls: list[str],
    *,
    min_names: int = 10,
    method: str = "rank",
) -> tuple[dict[date, dict[str, float]], dict]:
    """Per-date OLS-residualize `target` on `controls`.

    For each `as_of` date, over the tickers where `target` AND every entry in
    `controls` is present (not None): method="rank" rank-transforms target and
    each control WITHIN THE DATE first (average-tie ranks, `metrics.rank` —
    matching `spearman_ic`'s convention), then fits
    `target_ranks ~ 1 + control_ranks` via `ols`/`_residuals`; method="level"
    fits the same regression on the raw score levels, no rank transform.

    A date is skipped (never raises) when:
      - fewer than `min_names` tickers have every signal present
        (counted in `skipped_floor`), or
      - the regression design is singular, e.g. a duplicated control column
        (`ols` raises `ValueError`; counted in `skipped_singular`).

    Returns `(rows_by_date, diagnostics)`:
      - `rows_by_date`: `{as_of: {ticker: residual}}` for every date that fit.
      - `diagnostics`: `{"skipped_floor", "skipped_singular", "n_dates"
        (dates that fit), "mean_r2" (mean per-date R^2 of the control
        regression, computed in the SAME space the method uses — rank or
        level; 0.0 if no date fit), "beta_std" (`{control: population std,
        over the fitted dates, of that control's OLS coefficient}`, 0.0 per
        control if no date fit)}`.

    Pure, deterministic, stdlib-only; never raises on well-typed input.
    """
    by_date: dict[date, list[Observation]] = defaultdict(list)
    for obs in observations:
        sigs = obs.signals
        if sigs.get(target) is None:
            continue
        if any(sigs.get(c) is None for c in controls):
            continue
        by_date[obs.as_of].append(obs)

    rows_by_date: dict[date, dict[str, float]] = {}
    skipped_floor = 0
    skipped_singular = 0
    r2s: list[float] = []
    betas_by_control: dict[str, list[float]] = {c: [] for c in controls}

    for as_of in sorted(by_date):
        obs_list = by_date[as_of]
        if len(obs_list) < min_names:
            skipped_floor += 1
            continue

        tickers = [o.ticker for o in obs_list]
        y_raw = [o.signals[target] for o in obs_list]
        x_raw = [[o.signals[c] for c in controls] for o in obs_list]

        if method == "rank":
            y = rank(y_raw)
            cols = list(zip(*x_raw, strict=False)) if controls else []
            ranked_cols = [rank(list(col)) for col in cols]
            x = [list(row) for row in zip(*ranked_cols, strict=False)] if ranked_cols else [[] for _ in y]
        else:
            y = y_raw
            x = x_raw

        try:
            b = ols(y, x)
        except ValueError:
            skipped_singular += 1
            continue

        resid = _residuals(y, x, b)
        rows_by_date[as_of] = dict(zip(tickers, resid, strict=True))

        mean_y = sum(y) / len(y)
        ss_tot = sum((v - mean_y) ** 2 for v in y)
        ss_res = sum(r * r for r in resid)
        # ss_tot == 0 (all-equal y, only possible in method="level" with a degenerate
        # column) -> r2 defined as 0.0 by convention (no variance to explain), never a
        # ZeroDivisionError.
        r2s.append((1.0 - ss_res / ss_tot) if ss_tot > 0 else 0.0)

        for c, coef in zip(controls, b[1:], strict=True):
            betas_by_control[c].append(coef)

    diagnostics = {
        "skipped_floor": skipped_floor,
        "skipped_singular": skipped_singular,
        "n_dates": len(rows_by_date),
        "mean_r2": (sum(r2s) / len(r2s)) if r2s else 0.0,
        "beta_std": {c: (pstdev(vals) if vals else 0.0) for c, vals in betas_by_control.items()},
    }
    return rows_by_date, diagnostics

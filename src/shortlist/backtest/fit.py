"""Walk-forward composite-weight fitting with shrinkage toward a prior.

GUARDED: refuses to fit below period/breadth floors (no fitting six weights from a
handful of observations). Phase-2 path — activated only when point-in-time
multi-axis history accumulates. Deterministic (no RNG).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from .metrics import aggregate_ic, spearman_ic


class FitGuardError(RuntimeError):
    pass


@dataclass(frozen=True)
class FitResult:
    weights: dict[str, float]          # SHIPPED: shrunk-final, normalized within the fitted axes
    oos_ic: Optional[float]            # un-shrunk fitted aggregate OOS IC (optimistic ceiling)
    in_sample_ic: Optional[float]      # overfitting DIAGNOSTIC only — never a gate leg
    n_periods: int
    fitted_weights: dict[str, float] = field(default_factory=dict)  # pre-shrink avg, normalized
    prior_oos_ic: Optional[float] = None      # prior's aggregate OOS IC on the same folds
    shrunk_oos_ic: Optional[float] = None     # shipped (shrunk) policy's aggregate OOS IC
    n_oos_folds: int = 0                       # realized, populated paired folds
    fold_diffs: list[float] = field(default_factory=list)  # per-fold (shrunk - prior) OOS IC


def _composite(sub: dict[str, float], w: dict[str, float]) -> float:
    num = sum(sub[k] * w[k] for k in w if k in sub)
    den = sum(w[k] for k in w if k in sub)
    return num / den if den else 0.0


def _ic_for_weights(rows, w) -> Optional[float]:
    by_period: dict = defaultdict(list)
    for p, sub, fwd in rows:
        by_period[p].append((_composite(sub, w), fwd))
    ics = []
    for pairs in by_period.values():
        ic = spearman_ic([c for c, _ in pairs], [f for _, f in pairs])
        if ic is not None:
            ics.append(ic)
    agg = aggregate_ic(ics)
    return agg.mean if agg else None


def _normalize(w: dict[str, float]) -> dict[str, float]:
    s = sum(max(0.0, v) for v in w.values())
    return {k: (max(0.0, v) / s if s else 0.0) for k, v in w.items()}


def _coordinate_ascent(train, prior) -> dict[str, float]:
    """Deterministic coordinate ascent on weights maximizing in-sample IC."""
    w = dict(prior)
    best = _ic_for_weights(train, w)
    if best is None:
        best = -1.0
    for _ in range(20):
        improved = False
        for k in list(w):
            for delta in (1.25, 0.8):
                cand = _normalize({**w, k: w[k] * delta})
                ic = _ic_for_weights(train, cand)
                if ic is not None and ic > best + 1e-9:
                    w, best, improved = cand, ic, True
        if not improved:
            break
    return w


def fit_weights(rows, prior: dict[str, float], *, min_periods: int = 24,
                shrink: float = 0.5, n_folds: int = 4,
                min_period_gap_days: Optional[int] = None) -> FitResult:
    """rows: list of (period_id, {axis: subscore}, fwd_return). Walk-forward over
    contiguous period folds; fit on train, score OOS; shrink final toward prior.

    Per fold we score the prior, the un-shrunk fit, and the SHIPPED shrunk fit on the
    SAME held-out rows, kept paired (a fold counts only if all three ICs exist), so the
    report can show a paired fitted-vs-prior OOS comparison of the weights that actually
    ship. NOTE: oos_ic/prior_oos_ic/shrunk_oos_ic are all aggregated over the SAME paired
    folds (a fold contributing to one contributes to all three) — oos_ic is no longer the
    independently-None-filtered series the original computed. min_period_gap_days (opt-in;
    requires date period_ids) raises if periods are spaced closer than the gap — a guard
    against an accidentally-dense, overlapping grid."""
    periods = sorted({p for p, _, _ in rows})
    if len(periods) < min_periods:
        raise FitGuardError(
            f"need >= {min_periods} periods to fit weights, have {len(periods)} "
            f"— fitting is gated until point-in-time multi-axis history accumulates")
    if min_period_gap_days is not None:
        for a, b in zip(periods, periods[1:], strict=False):
            if (b - a).days < min_period_gap_days:
                raise FitGuardError(
                    f"period spacing {(b - a).days}d < required {min_period_gap_days}d "
                    f"— grid is denser than the horizon; forward-return windows overlap "
                    f"and would inflate OOS IC")
    prior = _normalize(prior)
    fold_size = max(1, len(periods) // n_folds)
    oos_ics, prior_oos_ics, shrunk_oos_ics, fold_diffs, fitted = [], [], [], [], []
    for f in range(1, n_folds):
        train_periods = set(periods[: f * fold_size])
        test_periods = set(periods[f * fold_size: (f + 1) * fold_size])
        if not test_periods:
            continue
        train = [r for r in rows if r[0] in train_periods]
        test = [r for r in rows if r[0] in test_periods]
        w = _coordinate_ascent(train, prior)
        w_shrunk = _normalize({k: shrink * w[k] + (1 - shrink) * prior[k] for k in prior})
        fitted.append(w)
        ic = _ic_for_weights(test, w)
        ic_prior = _ic_for_weights(test, prior)
        ic_shrunk = _ic_for_weights(test, w_shrunk)
        if None in (ic, ic_prior, ic_shrunk):
            continue                              # unpaired fold — exclude from OOS stats
        oos_ics.append(ic)
        prior_oos_ics.append(ic_prior)
        shrunk_oos_ics.append(ic_shrunk)
        fold_diffs.append(ic_shrunk - ic_prior)
    if fitted:
        avg = {k: sum(w[k] for w in fitted) / len(fitted) for k in prior}
    else:
        avg = dict(prior)
    final = _normalize({k: shrink * avg[k] + (1 - shrink) * prior[k] for k in prior})
    oos = aggregate_ic(oos_ics).mean if oos_ics else None
    prior_oos = aggregate_ic(prior_oos_ics).mean if prior_oos_ics else None
    shrunk_oos = aggregate_ic(shrunk_oos_ics).mean if shrunk_oos_ics else None
    insample = _ic_for_weights(rows, _coordinate_ascent(rows, prior))
    return FitResult(weights=final, oos_ic=oos, in_sample_ic=insample,
                     n_periods=len(periods), fitted_weights=_normalize(avg),
                     prior_oos_ic=prior_oos, shrunk_oos_ic=shrunk_oos,
                     n_oos_folds=len(fold_diffs), fold_diffs=fold_diffs)

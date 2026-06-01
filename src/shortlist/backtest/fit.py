"""Walk-forward composite-weight fitting with shrinkage toward a prior.

GUARDED: refuses to fit below period/breadth floors (no fitting six weights from a
handful of observations). Phase-2 path — activated only when point-in-time
multi-axis history accumulates. Deterministic (no RNG).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from .metrics import spearman_ic, aggregate_ic


class FitGuardError(RuntimeError):
    pass


@dataclass(frozen=True)
class FitResult:
    weights: dict[str, float]
    oos_ic: Optional[float]
    in_sample_ic: Optional[float]
    n_periods: int


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
                shrink: float = 0.5, n_folds: int = 4) -> FitResult:
    """rows: list of (period_id, {axis: subscore}, fwd_return). Walk-forward over
    contiguous period folds; fit on train, score OOS; shrink final toward prior."""
    periods = sorted({p for p, _, _ in rows})
    if len(periods) < min_periods:
        raise FitGuardError(
            f"need >= {min_periods} periods to fit weights, have {len(periods)} "
            f"— fitting is gated until point-in-time multi-axis history accumulates")
    prior = _normalize(prior)
    fold_size = max(1, len(periods) // n_folds)
    oos_ics, fitted = [], []
    for f in range(1, n_folds):
        train_periods = set(periods[: f * fold_size])
        test_periods = set(periods[f * fold_size: (f + 1) * fold_size])
        if not test_periods:
            continue
        train = [r for r in rows if r[0] in train_periods]
        test = [r for r in rows if r[0] in test_periods]
        w = _coordinate_ascent(train, prior)
        ic = _ic_for_weights(test, w)
        if ic is not None:
            oos_ics.append(ic)
        fitted.append(w)
    if fitted:
        avg = {k: sum(w[k] for w in fitted) / len(fitted) for k in prior}
    else:
        avg = dict(prior)
    final = _normalize({k: shrink * avg[k] + (1 - shrink) * prior[k] for k in prior})
    oos = aggregate_ic(oos_ics).mean if oos_ics else None
    insample = _ic_for_weights(rows, _coordinate_ascent(rows, prior))
    return FitResult(weights=final, oos_ic=oos, in_sample_ic=insample,
                     n_periods=len(periods))

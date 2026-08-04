"""Pure-stdlib statistics for the backtest: rank IC and quantile spreads.

No numpy/scipy — Spearman is Pearson-on-ranks, ranks use average tie handling.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from statistics import mean, stdev
from typing import Optional

from .signals import Observation


def rank(xs: list[float]) -> list[float]:
    """1-based ranks with average tie handling (required for correct Spearman)."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0           # average of 1-based positions i..j
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(a: list[float], b: list[float]) -> Optional[float]:
    n = len(a)
    if n < 3:
        return None
    ma, mb = mean(a), mean(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va == 0 or vb == 0:
        return None
    return cov / math.sqrt(va * vb)


def spearman_ic(signal: list[Optional[float]],
                fwd: list[Optional[float]]) -> Optional[float]:
    """Spearman rank correlation between signal and forward return.
    Drops index-aligned pairs where either is None. None if < 3 usable pairs."""
    pairs = [(s, f) for s, f in zip(signal, fwd, strict=True) if s is not None and f is not None]
    if len(pairs) < 3:
        return None
    s_vals, f_vals = zip(*pairs, strict=True)
    ic = _pearson(rank(list(s_vals)), rank(list(f_vals)))
    return round(ic, 10) if ic is not None else None


def cross_signal_xs_corr(observations: list[Observation], sig_a: str,
                         sig_b: str) -> Optional[float]:
    """Mean per-date Spearman rank correlation between two emitted signals over the
    names where BOTH are present. Diagnoses leg collinearity (e.g. ebit_ev_yield vs
    fcf_yield): a high value means a new leg duplicates an existing one and would
    dilute, not add to, an unweighted value average. None if no date has >= 3
    co-present pairs (spearman_ic itself returns None below 3 usable pairs)."""
    by_date: dict[date, tuple[list[float], list[float]]] = defaultdict(
        lambda: ([], []))
    for obs in observations:
        sigs = obs.signals
        if sig_a in sigs and sig_b in sigs:
            a, b = by_date[obs.as_of]
            a.append(sigs[sig_a])
            b.append(sigs[sig_b])
    cors = [spearman_ic(a, b) for a, b in by_date.values()]
    cors = [c for c in cors if c is not None]
    return round(mean(cors), 4) if cors else None


@dataclass(frozen=True)
class ICStats:
    mean: float
    std: float
    icir: Optional[float]
    t_stat: Optional[float]
    hit_rate: float
    n: int


def aggregate_ic(ics: list[float]) -> Optional[ICStats]:
    """Aggregate a series of per-period (or per-name) ICs into mean/std/ICIR/t/hit."""
    vals = [c for c in ics if c is not None]
    if not vals:
        return None
    m = mean(vals)
    sd = stdev(vals) if len(vals) > 1 else 0.0   # SAMPLE stdev: valid one-sample t
    icir = (m / sd) if sd > 0 else None
    t_stat = (icir * math.sqrt(len(vals))) if icir is not None else None
    hit = sum(1 for v in vals if v > 0) / len(vals)
    return ICStats(mean=m, std=sd, icir=icir, t_stat=t_stat, hit_rate=hit, n=len(vals))


@dataclass(frozen=True)
class QuantileResult:
    bucket_means: list[float]
    spread: float
    monotonic: bool
    n_buckets: int
    n: int


def quantile_spread(pairs: list[tuple[float, float]], n_buckets: int = 5) -> Optional[QuantileResult]:
    """Equal-weighted mean forward return per signal bucket; top-minus-bottom spread.
    Auto-collapses n_buckets so each bucket has >= 2 names; None if too few obs."""
    clean = [(s, f) for s, f in pairs if s is not None and f is not None]
    if len(clean) < 4:
        return None
    nb = n_buckets
    while nb > 2 and len(clean) // nb < 2:
        nb -= 1
    # Tie-break on the forward return so tied signal values bucket deterministically,
    # independent of row emission order (date-major vs ticker-major) — a stable sort on
    # the signal alone would otherwise let upstream order shift bucket boundaries.
    clean.sort(key=lambda p: (p[0], p[1]))
    size = len(clean) / nb
    bucket_means: list[float] = []
    for b in range(nb):
        lo = int(round(b * size))
        hi = int(round((b + 1) * size)) if b < nb - 1 else len(clean)
        rets = [f for _, f in clean[lo:hi]]
        # The `else 0.0` is UNREACHABLE, not a fabricated imputation — verified
        # 2026-06-26, re-verified 2026-08-04 (incl. a brute-force search for an empty
        # bucket). `len(clean) < 4` returns early above, and the collapse loop exits
        # with either `nb == 2` or `len(clean) // nb >= 2`, so
        # `size >= 2` always; each bucket then spans `round((b+1)*size) - round(b*size)
        # >= size - 1 >= 1` rows (the last runs to `len(clean)`). Kept as a belt-and-
        # suspenders guard — removing it changes no output. Do not "fix" it into an
        # abstention on the theory that an empty bucket silently scores 0.
        bucket_means.append(mean(rets) if rets else 0.0)
    spread = bucket_means[-1] - bucket_means[0]
    monotonic = all(bucket_means[i] <= bucket_means[i + 1] for i in range(nb - 1)) or \
                all(bucket_means[i] >= bucket_means[i + 1] for i in range(nb - 1))
    return QuantileResult(bucket_means=bucket_means, spread=spread,
                          monotonic=monotonic, n_buckets=nb, n=len(clean))

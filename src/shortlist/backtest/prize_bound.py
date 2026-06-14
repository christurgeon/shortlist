"""Stage 0 'bound the prize' gate for the multi-horizon momentum work.

Decides — before any measurement harness is built — whether swapping the momentum
sub-score leg can move the composite shortlist at momentum's ~0.08 weight. Re-blends
each real ScoreCard's composite with the momentum leg replaced, for a candidate-
independent maximum-churn weight bound (A) and for each candidate (B), then reports
top-N overlap + Kendall tau vs the real ranking. See
docs/superpowers/specs/2026-06-14-multi-horizon-momentum-design.md.
"""
from __future__ import annotations

from typing import Optional

# Composite components in weight order; risk is added separately when present.
_COMPONENTS = ("quality", "moat", "growth", "value", "momentum", "insider")


def composite_with(card, momentum: Optional[float], weights: dict, config: dict = None) -> float:
    """Recompute a card's composite with its momentum sub-score replaced by `momentum`.

    Faithful replica of scoring.score()'s composite: a normalized weighted average over
    present (non-None) components plus the risk tilt when present, rounded to 1 decimal.
    When `momentum == card.momentum` this reproduces card.composite exactly (pinned by
    test), which is the guarantee that swapped values are trustworthy.

    If `config` is provided and the card has metrics, recomputes subscores from the metrics
    to avoid rounding errors (uses raw subscore values like the real scorer does).
    Otherwise, uses the card's rounded subscore values (less precise but works with minimal data)."""
    # If we have both config and metrics, recompute subscores from the metrics to get
    # the exact same composite as the real scorer (which uses raw, unrounded values).
    if config is not None and card.metrics is not None:
        from shortlist.scoring import (
            _quality_legs, _moat_legs, _growth_legs, _momentum_legs, _value_legs,
            _eval_subscore, resolve_bucket, insider_score, risk_score
        )

        m = card.metrics
        bucket = resolve_bucket(m.sic, config)
        t = config["thresholds"]
        w = weights

        def sub(name, legs):
            s, _ = _eval_subscore(name, bucket, legs, t, config)
            return s

        q = sub("quality", _quality_legs(m, config))
        mo = sub("moat", _moat_legs(m))
        gr = sub("growth", _growth_legs(m, config))
        mom = sub("momentum", _momentum_legs(m))
        val = sub("value", _value_legs(m))
        ins = insider_score(m, t, config)

        # Replace momentum with the new value
        mom = momentum

        # Recompute composite from raw subscores
        parts = [(s, w[name]) for name, s in [
            ("quality", q), ("moat", mo), ("growth", gr),
            ("momentum", mom), ("value", val), ("insider", ins)
        ] if s is not None]

        risk_on = ("risk" in w) and ("realized_vol" in t) and ("max_drawdown" in t)
        ri = risk_score(m, t) if risk_on else None
        if ri is not None:
            parts.append((ri, w["risk"]))

        den = sum(weight for _, weight in parts)
        if not den:
            return 0.0
        return round(sum(s * weight for s, weight in parts) / den, 1)

    # Fallback: use rounded subscore values from the card
    subs = {k: getattr(card, k, None) for k in _COMPONENTS}
    subs["momentum"] = momentum
    parts = [(s, weights[k]) for k, s in subs.items() if s is not None]
    risk = getattr(card, "risk", None)
    if risk is not None:
        parts.append((risk, weights["risk"]))
    den = sum(w for _, w in parts)
    if not den:
        return 0.0
    return round(sum(s * w for s, w in parts) / den, 1)


def to_rank_scores(values: list[float]) -> list[float]:
    """Map values to cross-sectional percentile scores in [0, 100], ties averaged.
    A single value maps to 50.0; the min maps to 0.0 and the max to 100.0."""
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [50.0]
    order = sorted(range(n), key=lambda i: values[i])
    avg_rank = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        r = (i + j) / 2.0           # 0-based average rank of the tie group
        for k in range(i, j + 1):
            avg_rank[order[k]] = r
        i = j + 1
    return [100.0 * r / (n - 1) for r in avg_rank]


def ranking_from(scores: dict[str, float]) -> list[str]:
    """Tickers ordered by composite descending (ties broken by ticker for determinism)."""
    return [t for t, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))]


def topn_overlap(rank_a: list[str], rank_b: list[str], n: int) -> float:
    """Fraction of the top-n set shared between two rankings (1.0 == identical top-n)."""
    n = min(n, len(rank_a), len(rank_b))
    if n == 0:
        return 1.0
    return len(set(rank_a[:n]) & set(rank_b[:n])) / n


def kendall_tau(rank_a: list[str], rank_b: list[str]) -> float:
    """Kendall rank correlation over the items common to both rankings. O(n^2), fine
    for <=100 names. +1 identical order, -1 reversed, 0 independent."""
    common = [t for t in rank_a if t in set(rank_b)]
    pos_b = {t: i for i, t in enumerate(rank_b)}
    pos_a = {t: i for i, t in enumerate(common)}
    items = common
    m = len(items)
    if m < 2:
        return 1.0
    concordant = discordant = 0
    for i in range(m):
        for j in range(i + 1, m):
            ti, tj = items[i], items[j]
            da = pos_a[ti] - pos_a[tj]
            db = pos_b[ti] - pos_b[tj]
            if da * db > 0:
                concordant += 1
            else:
                discordant += 1
    total = m * (m - 1) / 2
    return (concordant - discordant) / total

"""Stage 0 'bound the prize' gate for the multi-horizon momentum work.

Decides — before any measurement harness is built — whether swapping the momentum
sub-score leg can move the composite shortlist at momentum's ~0.08 weight. Re-blends
each real ScoreCard's composite with the momentum leg replaced, for a candidate-
independent maximum-churn weight bound (A) and for each candidate (B), then reports
top-N overlap + Kendall tau vs the real ranking. See
docs/superpowers/specs/2026-06-14-multi-horizon-momentum-design.md.
"""
from __future__ import annotations

import random as _random
from typing import Optional

# Composite components in weight order; risk is added separately when present.
_COMPONENTS = ("quality", "moat", "growth", "value", "momentum", "insider")


def composite_with(card, momentum: Optional[float], weights: dict, config: Optional[dict] = None) -> float:
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
            _eval_subscore,
            _growth_legs,
            _moat_legs,
            _momentum_legs,
            _quality_legs,
            _value_legs,
            insider_score,
            resolve_bucket,
            risk_score,
        )

        m = card.metrics
        bucket = resolve_bucket(m.sic, config)
        t = config["thresholds"]
        w = weights

        def sub(name, legs) -> Optional[float]:
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


# Pre-registered STOP thresholds (band-free, rank-based churn). See spec Stage 0.
_STOP_OVERLAP = 0.9       # top-N set overlap at/above this == "did not move"
_STOP_TAU = 0.95          # full-basket Kendall tau at/above this == "did not move"
_MC_TRIALS = 200          # random-rank Monte-Carlo trials for the weight bound


def _churn(base_rank: list[str], scores: dict[str, float], top_ns) -> dict:
    new_rank = ranking_from(scores)
    return {
        "kendall_tau": round(kendall_tau(base_rank, new_rank), 4),
        "topn_overlap": {n: round(topn_overlap(base_rank, new_rank, n), 4) for n in top_ns},
    }


def _effective_momentum_weight(cards, weights) -> dict:
    vals = []
    for c in cards:
        present = [weights[k] for k in _COMPONENTS if getattr(c, k, None) is not None]
        if getattr(c, "risk", None) is not None:
            present.append(weights["risk"])
        den = sum(present)
        if den and getattr(c, "momentum", None) is not None:
            vals.append(weights["momentum"] / den)
    vals.sort()
    if not vals:
        return {"min": 0.0, "median": 0.0, "max": 0.0}
    mid = vals[len(vals) // 2]
    return {"min": round(vals[0], 4), "median": round(mid, 4), "max": round(vals[-1], 4)}


def prize_bound(cards, candidate_values: dict, weights: dict, config: dict, *,
                top_ns=(5, 10, 20), seed: int = 0) -> dict:
    """Stage 0 verdict. `cards`: real ScoreCards (full composites). `candidate_values`:
    {candidate_name: {ticker: raw_value}}. `config` is threaded to composite_with so it
    re-blends from the cards' UNROUNDED sub-scores (faithful to the real composite).
    Returns weight-bound churn, per-candidate churn, effective-weight distribution, and
    a pre-registered verdict string.

    Everything is rank-based to isolate the leg's cross-sectional ORDERING effect from
    scale: the baseline composite uses momentum = rank-mapped(incumbent momentum), and
    every comparison (reverse, random, candidates) replaces momentum with another
    rank-mapped [0,100] leg. A candidate whose ordering equals the incumbent therefore
    churns nothing (tau == 1.0)."""
    cards = [c for c in cards if c.composite is not None]
    card_by = {c.ticker: c for c in cards}
    tickers = list(card_by)

    def composites(score_by_ticker: dict) -> dict:
        return {t: composite_with(card_by[t], score_by_ticker[t], weights, config)
                for t in score_by_ticker}

    inc_scores = dict(zip(tickers,
                          to_rank_scores([(card_by[t].momentum or 0.0) for t in tickers]), strict=False))
    base_rank = ranking_from(composites(inc_scores))

    # (A) weight bound: rank-reverse of incumbent (max-decorrelated legal leg) plus a
    #     random-rank Monte-Carlo band; keep the WORST (lowest tau) churn vs base_rank.
    bound = _churn(base_rank, composites({t: 100.0 - inc_scores[t] for t in tickers}), top_ns)
    rng = _random.Random(seed)
    for _ in range(_MC_TRIALS):
        perm = to_rank_scores([rng.random() for _ in tickers])
        sc = {t: perm[i] for i, t in enumerate(tickers)}
        trial = _churn(base_rank, composites(sc), top_ns)
        if trial["kendall_tau"] < bound["kendall_tau"]:
            bound = trial

    # (B) per-candidate churn over the candidate's common universe, each vs a
    #     rank-mapped-incumbent baseline restricted to that same universe.
    candidates = {}
    for name, vals in candidate_values.items():
        ct = [t for t in tickers if t in vals]
        cand = dict(zip(ct, to_rank_scores([vals[t] for t in ct]), strict=False))
        inc = dict(zip(ct, to_rank_scores([(card_by[t].momentum or 0.0) for t in ct]), strict=False))
        cbase = ranking_from({t: composite_with(card_by[t], inc[t], weights, config) for t in ct})
        cnew = {t: composite_with(card_by[t], cand[t], weights, config) for t in ct}
        candidates[name] = _churn(cbase, cnew, top_ns)
        candidates[name]["n"] = len(ct)

    return {
        "n_cards": len(cards),
        "weight_bound": bound,
        "candidates": candidates,
        "effective_momentum_weight": _effective_momentum_weight(cards, weights),
        "verdict": _verdict(bound, candidates, top_ns),
    }


def _verdict(bound: dict, candidates: dict, top_ns) -> str:
    n = max(top_ns)
    def inert(churn: dict) -> bool:
        return churn["kendall_tau"] >= _STOP_TAU and churn["topn_overlap"][n] >= _STOP_OVERLAP
    if inert(bound):
        return "STOP_WEIGHT_INERT"          # even the max-disruptive leg can't move it
    if candidates and all(inert(c) for c in candidates.values()):
        return "STOP_COLLINEAR"             # weight has headroom, candidates too collinear
    return "PROCEED"


def run_live(config: dict, *, universe_file: str = None) -> dict:
    """Assemble real cards (cache-backed screen) + candidate closes (fetch_history),
    then call prize_bound. Yahoo-dependent (won't run on a Yahoo-blocked host).

    Caveat: prize_bound's verdict does not down-weight candidates with tiny coverage
    (a candidate present for only a few names trivially shows tau==1.0). The per-
    candidate 'n' field is reported so a reader can judge coverage before trusting a
    STOP_COLLINEAR verdict."""
    import asyncio
    from datetime import date

    import httpx

    from ..data.sources import mom_6m, mom_12_1
    from ..screen import run_harness
    from .prices import _UA, fetch_history
    from .universe import load_universe

    tickers = load_universe(universe_file or "largecap")
    sources = config.get("harness_sources",
                         ["yahoo", "fmp", "finnhub", "edgar", "finra", "wsb"])
    cards = [c for c in run_harness(tickers, sources, config) if c.composite is not None]
    scored = [c.ticker for c in cards]
    today = date.today().isoformat()

    async def _closes() -> dict:
        out = {}
        async with httpx.AsyncClient(headers={"User-Agent": _UA}) as client:
            for tk in scored:
                try:
                    h = await fetch_history(tk, client, cache_dir=".cache/yahoo", today=today)
                    out[tk] = h.closes
                except Exception as e:   # one bad symbol never aborts the gate
                    print(f"warn: closes fetch failed for {tk}: {type(e).__name__}")
        return out

    closes = asyncio.run(_closes())
    cand = {
        "mom_6m": {t: v for t in closes if (v := mom_6m(closes[t])) is not None},
        "mom_12_1": {t: v for t in closes if (v := mom_12_1(closes[t])) is not None},
    }
    return prize_bound(cards, cand, config["weights"], config)


def _main() -> int:
    import json
    from pathlib import Path

    import yaml

    from ..env import load_env
    load_env()
    config = yaml.safe_load(Path("config.yaml").read_text())
    result = run_live(config)
    print(json.dumps(result, indent=2))
    print(f"\nVERDICT: {result['verdict']}  (n_cards={result['n_cards']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

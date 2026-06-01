from __future__ import annotations

from statistics import mean
from typing import Optional

from .models import ScoreCard, StockMetrics


def _norm(value: Optional[float], lo: float, hi: float) -> Optional[float]:
    """Linearly map value into 0..100 over [lo, hi], clamped. None passes through."""
    if value is None:
        return None
    if hi == lo:
        return 50.0
    pct = (value - lo) / (hi - lo)
    return max(0.0, min(1.0, pct)) * 100.0


def _avg(components: list[Optional[float]]) -> Optional[float]:
    present = [c for c in components if c is not None]
    return mean(present) if present else None


def quality_score(m: StockMetrics, t: dict) -> Optional[float]:
    return _avg([
        _norm(m.roe, *t["roe"]),
        _norm(m.net_margin, *t["net_margin"]),
        _norm(m.interest_coverage, *t["interest_coverage"]),
        # Debt is inverted: less leverage scores higher.
        _norm(m.debt_to_equity, *t["debt_to_equity"]),
    ])


def moat_score(m: StockMetrics, t: dict) -> Optional[float]:
    # High, stable gross margins + persistent excess returns on capital are the
    # cleanest quantitative fingerprints of a durable moat.
    return _avg([
        _norm(m.gross_margin, *t["gross_margin"]),
        _norm(m.gross_margin_stability, *t["gross_margin_stability"]),
        _norm(m.roic_5y_avg if m.roic_5y_avg is not None else m.roic, *t["roic"]),
    ])


def growth_score(m: StockMetrics, t: dict) -> Optional[float]:
    # Fundamental compounding: pair growth RATE (revenue/FCF/earnings CAGR) with
    # CONSISTENCY (persistence) so one spike year can't masquerade as a trend.
    # Distinct from momentum (price-based) and PEG (value conditioned on growth).
    return _avg([
        _norm(m.revenue_cagr, *t["revenue_cagr"]),
        _norm(m.fcf_cagr, *t["fcf_cagr"]),
        _norm(m.eps_cagr, *t["eps_cagr"]),
        _norm(m.revenue_growth_persistence, *t["revenue_growth_persistence"]),
    ])


def momentum_score(m: StockMetrics, t: dict) -> Optional[float]:
    return _avg([
        _norm(m.price_vs_200dma, *t["price_vs_200dma"]),
        _norm(m.rel_strength_6m, *t["rel_strength_6m"]),
        _norm(m.eps_revision, *t["eps_revision"]),
    ])


def value_score(m: StockMetrics, t: dict) -> Optional[float]:
    return _avg([
        _norm(m.upside_to_target(), *t["upside_to_target"]),
        _norm(m.fcf_yield, *t["fcf_yield"]),
        _norm(m.pe_vs_history(), *t["pe_vs_history"]),
        _norm(m.peg, *t["peg"]),
    ])


def insider_score(m: StockMetrics, t: dict) -> Optional[float]:
    # "Not too much insider trading" = penalize net selling. A clean/buying
    # insider picture scores high; heavy disposition drags the score down.
    sentiment = _norm(m.insider_sentiment, *t["insider_sentiment"])
    net = None
    if m.insider_net_6m is not None and m.market_cap:
        # Scale net flow by market cap so a $5M sale at a $10B co isn't punished
        # like a $5M sale at a $1B co.
        ratio = m.insider_net_6m / m.market_cap
        net = _norm(ratio, *t["insider_net_ratio"])
    return _avg([sentiment, net])


def check_gates(m: StockMetrics, g: dict) -> list[str]:
    tripped: list[str] = []
    if m.fcf_positive is False:
        tripped.append("negative_fcf")
    if m.market_cap is not None and m.market_cap < g["min_market_cap"]:
        tripped.append("below_min_mktcap")
    if m.debt_to_equity is not None and m.debt_to_equity > g["max_debt_to_equity"]:
        tripped.append("over_leveraged")
    if m.insider_sentiment is not None and m.insider_sentiment < g["min_insider_sentiment"]:
        tripped.append("heavy_insider_selling")
    return tripped


def score(m: StockMetrics, config: dict) -> ScoreCard:
    t = config["thresholds"]
    w = config["weights"]

    q = quality_score(m, t)
    mo = moat_score(m, t)
    gr = growth_score(m, t)
    mom = momentum_score(m, t)
    val = value_score(m, t)
    # Chris's brief: momentum OR deep undervaluation. Take the stronger axis so a
    # name can qualify on either, rather than being averaged down by the weaker one.
    opp = _avg([max((x for x in (mom, val) if x is not None), default=None)])
    ins = insider_score(m, t)

    parts = {
        "quality": (q, w["quality"]),
        "moat": (mo, w["moat"]),
        "growth": (gr, w["growth"]),
        "opportunity": (opp, w["opportunity"]),
        "insider": (ins, w["insider"]),
    }
    num = sum(s * weight for s, weight in parts.values() if s is not None)
    den = sum(weight for s, weight in parts.values() if s is not None)
    composite = round(num / den, 1) if den else 0.0

    return ScoreCard(
        ticker=m.ticker,
        composite=composite,
        quality=_round(q), moat=_round(mo), growth=_round(gr), momentum=_round(mom),
        value=_round(val), opportunity=_round(opp), insider=_round(ins),
        gates=check_gates(m, config["gates"]),
        metrics=m,
    )


def _round(x: Optional[float]) -> Optional[float]:
    return round(x, 1) if x is not None else None

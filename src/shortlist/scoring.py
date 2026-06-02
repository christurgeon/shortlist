from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Optional

from .models import ScoreCard, StockMetrics
from .sectors import gate_applicable, leg_applicable, resolve_bucket


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


def insider_score(m: StockMetrics, t: dict, config: Optional[dict] = None) -> Optional[float]:
    # "Not too much insider trading" = penalize net selling. A clean/buying insider
    # picture scores high; heavy disposition drags it down. With the conviction block
    # present, fold in cluster + role-weighted buy pressure and forgive detected-10b5-1
    # planned sells. Absent the block (or the Form-4 inputs) this is byte-identical to
    # the pre-conviction scorer.
    cv = ((config or {}).get("insider") or {}).get("conviction")
    on = bool(cv) and cv.get("enabled", True)

    sentiment = _norm(m.insider_sentiment, *t["insider_sentiment"])

    flow = None
    if m.insider_net_6m is not None and m.market_cap:
        net_dollar = m.insider_net_6m
        if on and getattr(m, "insider_planned_sell_value", None):
            # add back a fraction of the detected-planned sell $ (sells made net more negative)
            net_dollar += cv["planned_sell_discount"] * m.insider_planned_sell_value
        flow = _norm(net_dollar / m.market_cap, *t["insider_net_ratio"])

    if not on:
        return _avg([sentiment, flow])

    cluster = role_press = None
    if getattr(m, "insider_distinct_buyers", None) is not None:
        cluster = _norm(float(m.insider_distinct_buyers), *t["insider_cluster"])
    if getattr(m, "insider_role_weighted_buy_value", None) is not None and m.market_cap:
        role_press = _norm(m.insider_role_weighted_buy_value / m.market_cap,
                           *t["insider_role_buy_ratio"])
    conviction = _avg([cluster, role_press])

    return _avg([sentiment, flow, conviction])


def risk_score(m: StockMetrics, t: dict) -> Optional[float]:
    # Sector-neutral (like insider_score): realized volatility and max drawdown are
    # well-defined for every sector, so risk is never masked. Both legs are inverted
    # (bands with safer -> higher): low vol and a shallow drawdown score high.
    return _avg([
        _norm(m.realized_vol, *t["realized_vol"]),
        _norm(m.max_drawdown, *t["max_drawdown"]),
    ])


# --- Sector-aware abstention -------------------------------------------------
# The legacy *_score helpers above are kept verbatim (imported by tests and called
# by the backtest). The new score() routes through the leg machinery below so it
# can mask structurally-inapplicable legs and abstain explicitly. For the 'unknown'
# bucket the machinery is bit-identical to _avg: any present leg scores, mean over
# present _norm values, no masking.


# Defaults so a minimal config (e.g. unit-test fixtures, the backtest's threshold-
# only dict) still scores: absent validity/sectors -> unknown bucket, no masking,
# any-present-leg scores. Keeps score() backward-compatible.
_DEFAULT_VALIDITY = {
    "min_valid_leg_fraction": 0.5,
    "unknown_min_present_legs": 1,
    "min_scored_weight": 0.34,
}


def _validity(config: dict) -> dict:
    return {**_DEFAULT_VALIDITY, **(config.get("validity") or {})}


@dataclass
class _Leg:
    name: str               # canonical name (matches masked_legs + threshold key)
    value: Optional[float]
    tkey: str               # key into config['thresholds']


def _eval_subscore(name: str, bucket: str, legs: list[_Leg], t: dict,
                   config: dict) -> tuple[Optional[float], list[dict]]:
    """Return (sub-score or None, abstentions). Replaces silent-drop with an
    explicit applicable/present partition; the floor is bucket-gated so 'unknown'
    stays a bit-identical no-op."""
    abst: list[dict] = []
    applicable: list[_Leg] = []
    for lg in legs:
        if leg_applicable(bucket, lg.name, config):
            applicable.append(lg)
        else:
            abst.append({"field": lg.name, "reason": "inapplicable", "scope": "leg"})
    if not applicable:
        abst.append({"field": name, "reason": "inapplicable", "scope": "subscore"})
        return None, abst

    present = [lg for lg in applicable if lg.value is not None]
    # Per-leg MISSING is intentionally NOT recorded — it overlaps coverage.py and
    # would make the abstentions block noisy. We record inapplicable legs (the new
    # masking signal) and whole-subscore abstention (inapplicable or too-thin).

    v = _validity(config)
    if bucket == "unknown":
        ok = len(present) >= v["unknown_min_present_legs"]
    else:
        ok = bool(present) and (len(present) / len(applicable)) >= v["min_valid_leg_fraction"]
    if not ok:
        abst.append({"field": name, "reason": "missing", "scope": "subscore"})
        return None, abst

    return mean(_norm(lg.value, *t[lg.tkey]) for lg in present), abst


def _quality_legs(m: StockMetrics) -> list[_Leg]:
    return [
        _Leg("roe", m.roe, "roe"),
        _Leg("net_margin", m.net_margin, "net_margin"),
        _Leg("interest_coverage", m.interest_coverage, "interest_coverage"),
        _Leg("debt_to_equity", m.debt_to_equity, "debt_to_equity"),
    ]


def _moat_legs(m: StockMetrics) -> list[_Leg]:
    return [
        _Leg("gross_margin", m.gross_margin, "gross_margin"),
        _Leg("gross_margin_stability", m.gross_margin_stability, "gross_margin_stability"),
        _Leg("roic", m.roic_5y_avg if m.roic_5y_avg is not None else m.roic, "roic"),
    ]


def _growth_legs(m: StockMetrics) -> list[_Leg]:
    return [
        _Leg("revenue_cagr", m.revenue_cagr, "revenue_cagr"),
        _Leg("fcf_cagr", m.fcf_cagr, "fcf_cagr"),
        _Leg("eps_cagr", m.eps_cagr, "eps_cagr"),
        _Leg("revenue_growth_persistence", m.revenue_growth_persistence, "revenue_growth_persistence"),
    ]


def _momentum_legs(m: StockMetrics) -> list[_Leg]:
    return [
        _Leg("price_vs_200dma", m.price_vs_200dma, "price_vs_200dma"),
        _Leg("rel_strength_6m", m.rel_strength_6m, "rel_strength_6m"),
        _Leg("eps_revision", m.eps_revision, "eps_revision"),
    ]


def _value_legs(m: StockMetrics) -> list[_Leg]:
    return [
        _Leg("upside_to_target", m.upside_to_target(), "upside_to_target"),
        _Leg("fcf_yield", m.fcf_yield, "fcf_yield"),
        _Leg("pe_vs_history", m.pe_vs_history(), "pe_vs_history"),
        _Leg("peg", m.peg, "peg"),
    ]


def check_gates(m: StockMetrics, g: dict, bucket: str = "unknown",
                config: Optional[dict] = None) -> list[str]:
    """Hard filters. `bucket`/`config` default so legacy 1-pair callers still work;
    gate_applicable short-circuits on bucket=='unknown' before touching config."""
    tripped: list[str] = []
    if m.fcf_positive is False and gate_applicable(bucket, "negative_fcf", config):
        tripped.append("negative_fcf")
    if m.market_cap is not None and m.market_cap < g["min_market_cap"] \
            and gate_applicable(bucket, "below_min_mktcap", config):
        tripped.append("below_min_mktcap")
    if m.debt_to_equity is not None and m.debt_to_equity > g["max_debt_to_equity"] \
            and gate_applicable(bucket, "over_leveraged", config):
        tripped.append("over_leveraged")
    if m.insider_sentiment is not None and m.insider_sentiment < g["min_insider_sentiment"] \
            and gate_applicable(bucket, "heavy_insider_selling", config):
        tripped.append("heavy_insider_selling")
    return tripped


def check_flags(m: StockMetrics, f: dict) -> list[str]:
    """Soft, NON-disqualifying advisories (parallel to check_gates). Fully None-safe:
    returns [] when inputs or config are absent, so the screener engine is a no-op."""
    out: list[str] = []
    cs = f.get("crowded_short") if f else None
    if cs and m.short_pct_outstanding is not None and m.days_to_cover is not None:
        fresh = (m.short_data_age_days is None
                 or m.short_data_age_days <= cs["max_staleness_days"])
        rising_ok = (not cs.get("require_rising")) or (m.short_interest_rising is True)
        if (m.short_pct_outstanding >= cs["min_short_pct_outstanding"]
                and m.days_to_cover >= cs["min_days_to_cover"]
                and rising_ok and fresh):
            out.append("crowded_short")
    # Filing-stream event advisories (set by the harness bridge; None on the screener
    # path, so this is a no-op there). Presence-based — no config thresholds.
    for attr in ("activist_13d", "recent_8k", "passive_13g", "planned_insider_sale_144"):
        if getattr(m, attr, None):
            out.append(attr)
    return out


def score(m: StockMetrics, config: dict) -> ScoreCard:
    t = config["thresholds"]
    w = config["weights"]
    bucket = resolve_bucket(m.sic, config)

    abst: list[dict] = []

    def sub(name, legs):
        s, a = _eval_subscore(name, bucket, legs, t, config)
        abst.extend(a)
        return s

    q = sub("quality", _quality_legs(m))
    mo = sub("moat", _moat_legs(m))
    gr = sub("growth", _growth_legs(m))
    mom = sub("momentum", _momentum_legs(m))
    val = sub("value", _value_legs(m))
    # Chris's brief: momentum OR deep undervaluation. Take the stronger axis so a
    # name can qualify on either, rather than being averaged down by the weaker one.
    pres = [x for x in (mom, val) if x is not None]
    opp = max(pres) if pres else None
    ins = insider_score(m, t, config)  # sector-neutral; never masked

    # A component is INAPPLICABLE iff its sub-score abstained at subscore scope for
    # the 'inapplicable' reason. opportunity is applicable if EITHER momentum or
    # value is applicable (momentum legs are never masked -> opportunity always
    # applicable for v1 buckets).
    inapplicable = {a["field"] for a in abst
                    if a["scope"] == "subscore" and a["reason"] == "inapplicable"}

    def applic(*subs):
        return any(s not in inapplicable for s in subs)

    components = [
        ("quality", q, w["quality"], ("quality",)),
        ("moat", mo, w["moat"], ("moat",)),
        ("growth", gr, w["growth"], ("growth",)),
        ("opportunity", opp, w["opportunity"], ("momentum", "value")),
        ("insider", ins, w["insider"], ("insider",)),
    ]

    # Risk: a composite-only tilt (config-gated). Sector-neutral like insider, but
    # deliberately NOT added to `components` -> it never enters appl_w/pres_w, so
    # confidence/scored/passed stay bit-identical when risk is absent. The five
    # weights are rescaled x0.9 in config, so with risk absent the scalar cancels in
    # num/den and the composite equals the pre-change scorer. See the design spec §3.
    risk_on = ("risk" in w) and ("realized_vol" in t) and ("max_drawdown" in t)
    ri = risk_score(m, t) if risk_on else None

    # Composite: unchanged math over present components, weight redistributed,
    # plus the risk tilt when present.
    parts = [(s, weight) for _, s, weight, _ in components if s is not None]
    if ri is not None:
        parts.append((ri, w["risk"]))
    num = sum(s * weight for s, weight in parts)
    den = sum(weight for _, weight in parts)
    composite = round(num / den, 1) if den else 0.0

    # Confidence over APPLICABLE components; scored is bucket-gated (unknown -> True).
    appl_w = sum(weight for _, _, weight, subs in components if applic(*subs))
    pres_w = sum(weight for _, s, weight, subs in components
                 if applic(*subs) and s is not None)
    confidence = round(pres_w / appl_w, 3) if appl_w else 0.0
    scored = True if bucket == "unknown" else confidence >= _validity(config)["min_scored_weight"]
    # Display-only coverage advisory; config-gated and None-safe (absent block -> False).
    thin_below = (config.get("ranking") or {}).get("thin_below")
    thin = thin_below is not None and confidence < thin_below

    return ScoreCard(
        ticker=m.ticker,
        composite=composite,
        quality=_round(q), moat=_round(mo), growth=_round(gr), momentum=_round(mom),
        value=_round(val), opportunity=_round(opp), insider=_round(ins),
        gates=check_gates(m, config["gates"], bucket, config),
        flags=check_flags(m, config.get("flags") or {}),
        metrics=m,
        sic_bucket=bucket, confidence=confidence, scored=scored, abstentions=abst,
        risk=_round(ri),
        thin=thin,
    )


def _round(x: Optional[float]) -> Optional[float]:
    return round(x, 1) if x is not None else None

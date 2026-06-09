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

    base = _avg([sentiment, flow])
    if not on:
        return base

    # Conviction is a ONE-DIRECTIONAL buy-side tilt. Cluster buys and role-weighted
    # buy pressure are *positive* signals; their absence is the baseline, not a
    # penalty — so conviction may only RAISE the score, never drag it (averaging a
    # near-zero "no-buys" leg into the base would perversely punish the common case,
    # e.g. a lone insider BUY scoring _norm(1; 1,4)=0). The planned-sell forgiveness
    # folded into `flow` above is likewise one-directional (it only softens drag).
    cluster = role_press = None
    if getattr(m, "insider_distinct_buyers", None) is not None:
        cluster = _norm(float(m.insider_distinct_buyers), *t["insider_cluster"])
    if getattr(m, "insider_role_weighted_buy_value", None) is not None and m.market_cap:
        role_press = _norm(m.insider_role_weighted_buy_value / m.market_cap,
                           *t["insider_role_buy_ratio"])
    conviction = _avg([cluster, role_press])
    if conviction is None or base is None:
        return base
    # Pull the score halfway toward the conviction level, but never below the base.
    return max(base, _avg([base, conviction]))


def risk_score(m: StockMetrics, t: dict) -> Optional[float]:
    # Sector-neutral (like insider_score): realized volatility and max drawdown are
    # well-defined for every sector, so risk is never masked. Both legs are inverted
    # (bands with safer -> higher): low vol and a shallow drawdown score high.
    return _avg([
        _norm(m.realized_vol, *t["realized_vol"]),
        _norm(m.max_drawdown, *t["max_drawdown"]),
    ])


def _piotroski_raw_fraction(m: StockMetrics, min_legs: int) -> Optional[float]:
    """won/legs from the Piotroski-lite scalars, or None below the min-legs floor.
    No sector masking here (that is the caller's concern via leg_applicable)."""
    if m.piotroski_f is None or not m.piotroski_f_legs or m.piotroski_f_legs < min_legs:
        return None
    return m.piotroski_f / m.piotroski_f_legs


def piotroski_score(m: StockMetrics, t: dict, min_legs: int = 4) -> Optional[float]:
    """Map the Piotroski-lite fraction through the config band -> 0..100. Sector-
    neutral here (the XbrlSignalSource passes sic=None -> unknown bucket); the
    value_trap refinement applies masking separately. None below the min-legs floor
    or when the band is absent."""
    if "piotroski_f" not in t:
        return None
    frac = _piotroski_raw_fraction(m, min_legs)
    return _norm(frac, *t["piotroski_f"]) if frac is not None else None


def share_count_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Standalone dilution axis: the inverted share-count-CAGR band -> 0..100 (net
    buybacks score higher than net issuance). Exists so the backtest can measure the
    share-count rank IC on its own (like piotroski_score); the PRODUCTION signal is
    the opt-in quality dilution leg + the `dilution` flag, not this. None when the
    band or the signal is absent."""
    if "share_count_cagr" not in t or m.share_count_cagr is None:
        return None
    return _norm(m.share_count_cagr, *t["share_count_cagr"])


def net_debt_to_ebitda_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Standalone leverage axis for the backtest: inverted net-debt/EBITDA band ->
    0..100 (less leverage scores higher; net cash tops the band). Backtest-only,
    like share_count_score; the PRODUCTION signal is the over_leveraged GATE, not
    this. None when the band or the signal is absent."""
    if "net_debt_to_ebitda" not in t or m.net_debt_to_ebitda is None:
        return None
    return _norm(m.net_debt_to_ebitda, *t["net_debt_to_ebitda"])


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


def _dilution_on(config: dict) -> bool:
    """True when the opt-in earnings-quality/dilution scoring block is present and
    enabled. Absent (it ships commented out) -> the dilution leg and the per-share
    eps_cagr swap are skipped, so the scorer is byte-identical to the pre-feature
    version. Mirrors the insider.conviction gating."""
    d = ((config or {}).get("quality") or {}).get("dilution")
    return bool(d) and d.get("enabled", True)


def _quality_legs(m: StockMetrics, config: Optional[dict] = None) -> list[_Leg]:
    legs = [
        _Leg("roe", m.roe, "roe"),
        _Leg("net_margin", m.net_margin, "net_margin"),
        _Leg("interest_coverage", m.interest_coverage, "interest_coverage"),
        _Leg("debt_to_equity", m.debt_to_equity, "debt_to_equity"),
    ]
    # Capital-allocation / dilution leg (inverted band: net buybacks score higher
    # than net issuance). Opt-in; the threshold guard keeps it None-safe if a config
    # enables the block without the band. None signal -> _eval_subscore redistributes.
    if _dilution_on(config) and "share_count_cagr" in ((config or {}).get("thresholds") or {}):
        legs.append(_Leg("share_count_cagr", m.share_count_cagr, "share_count_cagr"))
    return legs


def _moat_legs(m: StockMetrics) -> list[_Leg]:
    return [
        _Leg("gross_margin", m.gross_margin, "gross_margin"),
        _Leg("gross_margin_stability", m.gross_margin_stability, "gross_margin_stability"),
        _Leg("roic", m.roic_5y_avg if m.roic_5y_avg is not None else m.roic, "roic"),
    ]


def _growth_legs(m: StockMetrics, config: Optional[dict] = None) -> list[_Leg]:
    # With the dilution block on, the earnings-growth leg uses the genuine per-share
    # diluted-EPS CAGR (dilution-aware) instead of the net-income proxy, falling back
    # to the proxy when no per-share series exists. Same band/key either way.
    eps = m.eps_cagr
    if _dilution_on(config) and m.eps_cagr_ps is not None:
        eps = m.eps_cagr_ps
    return [
        _Leg("revenue_cagr", m.revenue_cagr, "revenue_cagr"),
        _Leg("fcf_cagr", m.fcf_cagr, "fcf_cagr"),
        _Leg("eps_cagr", eps, "eps_cagr"),
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


def _fcf_excused(m: StockMetrics, fc: dict) -> bool:
    """Negative FCF is excused when growth is strong AND sustained (spec §4)."""
    return (m.revenue_cagr is not None and m.revenue_cagr >= fc["excuse_min_revenue_cagr"]
            and m.revenue_growth_persistence is not None
            and m.revenue_growth_persistence >= fc["excuse_min_persistence"])


def _over_leveraged(m: StockMetrics, g: dict, lv: dict) -> bool:
    """net-debt/EBITDA primary; artifact-guarded, coverage-corroborated D/E fallback.
    See spec §3 (2026-06-08-gate-fixes-design). Fail-OPEN on the equity-distortion
    artifact (D/E <=0 or > ceiling), fail-CLOSED on plausible leverage."""
    max_dte = g["max_debt_to_equity"]
    ebitda_usable = (
        m.ebitda is not None and m.ebitda > 0
        and m.revenue not in (None, 0)
        and (m.ebitda / m.revenue) >= lv["min_ebitda_margin"]
    )
    if ebitda_usable and m.net_debt_to_ebitda is not None:
        return m.net_debt_to_ebitda > lv["max_net_debt_to_ebitda"]
    # Fallback: EBITDA absent / sub-floor / uncomputable.
    dte = m.debt_to_equity
    if dte is None or dte <= 0:
        return False                      # absent or negative-equity artifact
    if dte > lv["dte_artifact_ceiling"]:
        return False                      # explosive thin-equity artifact
    if dte <= max_dte:
        return False                      # under the bar
    ic = m.interest_coverage              # D/E in (max, ceiling] -> plausibly real
    if ic is not None and ic >= lv["min_interest_coverage_for_gate"]:
        return False                      # strong debt service spares it
    return True


def check_gates(m: StockMetrics, g: dict, bucket: str = "unknown",
                config: Optional[dict] = None) -> list[str]:
    """Hard filters. `bucket`/`config` default so legacy 1-pair callers still work;
    gate_applicable short-circuits on bucket=='unknown' before touching config."""
    tripped: list[str] = []
    fc = g.get("fcf")
    fcf_gate_on = bool(fc) and fc.get("enabled", True)
    if (m.fcf_positive is False and gate_applicable(bucket, "negative_fcf", config)
            and (not fcf_gate_on or not _fcf_excused(m, fc))):
        tripped.append("negative_fcf")
    if m.market_cap is not None and m.market_cap < g["min_market_cap"] \
            and gate_applicable(bucket, "below_min_mktcap", config):
        tripped.append("below_min_mktcap")
    lv = g.get("leverage")
    leverage_on = bool(lv) and lv.get("enabled", True)
    if gate_applicable(bucket, "over_leveraged", config):
        if not leverage_on:
            if m.debt_to_equity is not None and m.debt_to_equity > g["max_debt_to_equity"]:
                tripped.append("over_leveraged")
        elif _over_leveraged(m, g, lv):
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
    cb = f.get("insider_cluster_buy") if f else None
    if cb and m.insider_distinct_buyers is not None \
            and m.insider_distinct_buyers >= cb["min_distinct"]:
        out.append("insider_cluster_buy")
    ps = f.get("planned_sale") if f else None
    if ps and m.insider_planned_sell_value is not None \
            and m.insider_planned_sell_value >= ps["min_value"]:
        out.append("planned_sale")
    # Dilution advisory: persistent net share issuance. Soft/None-safe like the
    # others — no-op when the config block is absent; never affects passed/composite.
    dil = f.get("dilution") if f else None
    if dil and m.share_count_cagr is not None and m.share_count_cagr >= dil["min_share_cagr"]:
        out.append("dilution")
    # Cash-burn advisory: ALWAYS visible when FCF is negative (the stage-aware
    # negative_fcf gate may excuse a grower, but the burn is still surfaced).
    burn = f.get("cash_burn") if f else None
    if bool(burn) and burn.get("enabled", True) and m.fcf_positive is False:
        out.append("cash_burn")
    # Social-media hype advisory (WSB via ApeWisdom). Soft/None-safe like the others —
    # no-op when the config block is absent; never affects passed/composite/scored.
    # "Context-aware" by coexistence: renders alongside crowded_short (squeeze) or
    # value_trap (pump caution) — no extra logic needed.
    sh = f.get("social_hype") if f else None
    if sh and m.social_mentions is not None:
        fresh = (m.social_data_age_days is None
                 or m.social_data_age_days <= sh["max_staleness_days"])
        rising_ok = (not sh.get("require_rising")) or (m.social_mentions_rising is True)
        # None delta (e.g. zero/absent prior-day count) passes the velocity gate by
        # design — a brand-new chatter spike has no 24h baseline to measure against.
        delta_ok = (m.social_mention_delta_pct is None
                    or m.social_mention_delta_pct >= sh.get("min_mention_delta_pct", 0.0))
        if (m.social_mentions >= sh["min_mentions"]
                and rising_ok and delta_ok and fresh):
            out.append("social_hype")
    return out


def score(m: StockMetrics, config: dict, macro=None) -> ScoreCard:
    t = config["thresholds"]
    w = config["weights"]
    bucket = resolve_bucket(m.sic, config)

    abst: list[dict] = []

    def sub(name, legs):
        s, a = _eval_subscore(name, bucket, legs, t, config)
        abst.extend(a)
        return s

    q = sub("quality", _quality_legs(m, config))
    mo = sub("moat", _moat_legs(m))
    gr = sub("growth", _growth_legs(m, config))
    mom = sub("momentum", _momentum_legs(m))
    val = sub("value", _value_legs(m))
    # Value-tilt: value and momentum are weighted INDEPENDENTLY in the composite
    # (see spec 2026-06-02-value-tilt-scoring-design). `opp` is retained only as a
    # display-only convenience on the ScoreCard, not as a composite component.
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
        ("momentum", mom, w["momentum"], ("momentum",)),
        ("value", val, w["value"], ("value",)),
        ("insider", ins, w["insider"], ("insider",)),
    ]

    # Risk: a composite-only tilt (config-gated). Sector-neutral like insider, but
    # deliberately NOT added to `components` -> it never enters appl_w/pres_w, so
    # confidence/scored/passed stay bit-identical when risk is absent. The composite
    # is a normalized weighted average (num/den over present parts), so risk's
    # presence/absence only re-normalizes over present components — absolute weight
    # magnitudes are cosmetic and only ratios matter. See docs/ASSESSMENT_GAPS.md.
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

    flags = check_flags(m, config.get("flags") or {})
    # value-trap advisory: cheap (high value) but weak fundamentals. Soft/None-safe
    # like crowded_short — never affects passed/composite/scored. No-op if the
    # config block is absent. When the optional flags.value_trap.piotroski sub-block
    # is present, the Piotroski-lite fraction SUPPRESSES the flag on cheap-but-
    # improving names and CONFIRMS it on cheap-but-deteriorating ones (suppression
    # wins). Byte-identical to the legacy flag when the sub-block is absent.
    vt = (config.get("flags") or {}).get("value_trap")
    if vt and val is not None and val >= vt["min_value_score"]:
        base = ((q is not None and q < vt["max_quality_score"])
                or (gr is not None and gr < vt["max_growth_score"]))
        pio = vt.get("piotroski")
        frac = None
        if pio and leg_applicable(bucket, "piotroski_f", config):
            frac = _piotroski_raw_fraction(m, pio.get("min_legs", 4))
        if pio and frac is not None:
            fire = (base or frac <= pio["confirm_at"]) and not (frac >= pio["suppress_at"])
        else:
            fire = base
        if fire:
            flags.append("value_trap")
        if pio and not leg_applicable(bucket, "piotroski_f", config):
            abst.append({"field": "piotroski_f", "reason": "inapplicable", "scope": "leg"})

    # risk-off regime advisory: soft/None-safe like value_trap. Fires only in a
    # risk-off macro regime AND on an exposed name (leveraged OR cyclical bucket).
    # Never affects passed/composite/scored. No-op when macro is None (overlay
    # disabled / fetch failed) or the config block is absent — keeps score()
    # byte-identical to the pre-feature scorer. NOTE: this signal is intentionally
    # NOT validatable by the XBRL backtest (backtest/signals.py passes no macro/SIC).
    ro = (config.get("flags") or {}).get("risk_off_regime")
    if ro and macro is not None and macro.risk_off:
        dte_ceil = ((config.get("gates") or {}).get("leverage") or {}).get("dte_artifact_ceiling")
        leveraged = (
            (m.net_debt_to_ebitda is not None
             and m.net_debt_to_ebitda > ro["max_net_debt_ebitda"])
            or (m.debt_to_equity is not None
                and 0 < m.debt_to_equity > ro["max_debt_to_equity"]
                and (dte_ceil is None or m.debt_to_equity <= dte_ceil)))
        cyclical = bucket in ro.get("cyclical_buckets", [])
        if leveraged or cyclical:
            flags.append("risk_off_regime")

    return ScoreCard(
        ticker=m.ticker,
        composite=composite,
        quality=_round(q), moat=_round(mo), growth=_round(gr), momentum=_round(mom),
        value=_round(val), opportunity=_round(opp), insider=_round(ins),
        gates=check_gates(m, config["gates"], bucket, config),
        flags=flags,
        metrics=m,
        sic_bucket=bucket, confidence=confidence, scored=scored, abstentions=abst,
        risk=_round(ri),
        thin=thin,
        piotroski_f=m.piotroski_f, piotroski_f_legs=m.piotroski_f_legs,
        share_count_cagr=m.share_count_cagr,
        ebitda=m.ebitda,
        net_debt_to_ebitda=m.net_debt_to_ebitda,
    )


def _round(x: Optional[float]) -> Optional[float]:
    return round(x, 1) if x is not None else None

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Optional

from .models import ScoreCard, StockMetrics
from .sectors import gate_applicable, leg_applicable, resolve_bucket
from .stats import sue as _stats_sue

# Declarative only: emit sites keep their string literals. CI AST-scans them against
# these sets, so a new gate/flag must be declared here AND in bot/glossary.py.
_FILING_STREAM_FLAGS = ("activist_13d", "recent_8k", "passive_13g",
                        "planned_insider_sale_144", "late_filing", "shelf_offering",
                        "sec_comment_letter", "restatement_8k", "auditor_change",
                        "listing_deficiency")
KNOWN_GATES = frozenset({
    "negative_fcf", "below_min_mktcap", "over_leveraged",
    "heavy_insider_selling"})
KNOWN_FLAGS = frozenset({
    "crowded_short", "insider_cluster_buy", "planned_sale", "dilution",
    "cash_burn", "social_hype", "news_spike", "filing_text_change",
    "value_trap", "risk_off_regime", *_FILING_STREAM_FLAGS})

# SUE defaults: fallbacks for the momentum.sue knobs and the backtest-only sue_score.
_SUE_DECAY_TRADING_DAYS = 60
_SUE_SIGMA_FLOOR = 1.0
_SUE_MIN_QUARTERS = 3


def _norm(value: Optional[float], lo: float, hi: float) -> Optional[float]:
    """Linearly map value into 0..100 over [lo, hi], clamped. None passes through."""
    if value is None:
        return None
    if hi == lo:
        return 50.0
    pct = (value - lo) / (hi - lo)
    return max(0.0, min(1.0, pct)) * 100.0


def _scorable_peg(m: StockMetrics) -> Optional[float]:
    """PEG, or None when it is non-positive or P/E is unknown/non-positive. A PEG <= 0
    would clamp to 100 on the inverted band, and a loss-maker with shrinking losses
    produces a positive, cheap-looking PEG from two negatives."""
    if m.peg is None or m.peg <= 0 or m.pe_ttm is None or m.pe_ttm <= 0:
        return None
    return m.peg


def _avg(components: list[Optional[float]]) -> Optional[float]:
    present = [c for c in components if c is not None]
    return mean(present) if present else None


def _flag_block(flags_cfg: Optional[dict], name: str) -> Optional[dict]:
    """The named flag config block, or None when absent, falsy or `enabled: false`.
    A present block defaults to enabled."""
    block = flags_cfg.get(name) if flags_cfg else None
    if not block:
        return None
    return block if block.get("enabled", True) else None


def quality_score(m: StockMetrics, t: dict) -> Optional[float]:
    return _avg([
        _norm(m.roe, *t["roe"]),
        _norm(m.net_margin, *t["net_margin"]),
        _norm(m.interest_coverage, *t["interest_coverage"]),
        _norm(m.debt_to_equity, *t["debt_to_equity"]),
    ])


def moat_score(m: StockMetrics, t: dict) -> Optional[float]:
    return _avg([
        _norm(m.gross_margin, *t["gross_margin"]),
        _norm(m.gross_margin_stability, *t["gross_margin_stability"]),
        _norm(m.roic_5y_avg if m.roic_5y_avg is not None else m.roic, *t["roic"]),
    ])


def growth_score(m: StockMetrics, t: dict) -> Optional[float]:
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
        _norm(_scorable_peg(m), *t["peg"]),
    ])


def insider_score(m: StockMetrics, t: dict, config: Optional[dict] = None) -> Optional[float]:
    # Penalizes net selling. With the conviction block present, also folds in cluster
    # and role-weighted buy pressure and forgives detected 10b5-1 planned sells.
    cv = ((config or {}).get("insider") or {}).get("conviction")
    on = bool(cv) and cv.get("enabled", True)

    sentiment = _norm(m.insider_sentiment, *t["insider_sentiment"])

    flow = None
    if m.insider_net_6m is not None and m.market_cap:
        net_dollar = m.insider_net_6m
        if on and getattr(m, "insider_planned_sell_value", None):
            net_dollar += cv["planned_sell_discount"] * m.insider_planned_sell_value
        flow = _norm(net_dollar / m.market_cap, *t["insider_net_ratio"])

    base = _avg([sentiment, flow])
    if not on:
        return base

    # Conviction may only RAISE the score: absence of cluster buys is the baseline, and
    # averaging a near-zero "no-buys" leg into the base would punish the common case.
    cluster = role_press = None
    if getattr(m, "insider_distinct_buyers", None) is not None:
        cluster = _norm(float(m.insider_distinct_buyers), *t["insider_cluster"])
    if getattr(m, "insider_role_weighted_buy_value", None) is not None and m.market_cap:
        role_press = _norm(m.insider_role_weighted_buy_value / m.market_cap,
                           *t["insider_role_buy_ratio"])
    conviction = _avg([cluster, role_press])
    if conviction is None or base is None:
        return base
    return max(base, _avg([base, conviction]))


def risk_score(m: StockMetrics, t: dict) -> Optional[float]:
    # Sector-neutral: never masked.
    return _avg([
        _norm(m.realized_vol, *t["realized_vol"]),
        _norm(m.max_drawdown, *t["max_drawdown"]),
    ])


def _piotroski_raw_fraction(m: StockMetrics, min_legs: int) -> Optional[float]:
    """won/legs, or None below the min-legs floor. Sector masking is the caller's job."""
    if m.piotroski_f is None or not m.piotroski_f_legs or m.piotroski_f_legs < min_legs:
        return None
    return m.piotroski_f / m.piotroski_f_legs


def piotroski_score(m: StockMetrics, t: dict, min_legs: int = 4) -> Optional[float]:
    """The Piotroski-lite fraction through the config band -> 0..100. Sector-neutral;
    None below the min-legs floor or without the band."""
    if "piotroski_f" not in t:
        return None
    frac = _piotroski_raw_fraction(m, min_legs)
    return _norm(frac, *t["piotroski_f"]) if frac is not None else None


def _band_axis(value: Optional[float], key: str, t: dict) -> Optional[float]:
    """Shared shape of the backtest-only axes below: None when the band or metric is
    absent. Inversion lives in the band ordering [high, low], never here."""
    if key not in t or value is None:
        return None
    return _norm(value, *t[key])


def share_count_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Backtest-only dilution axis (inverted band: buybacks score high)."""
    return _band_axis(m.share_count_cagr, "share_count_cagr", t)


def asset_growth_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Backtest-only asset-growth axis (inverted band)."""
    return _band_axis(m.asset_growth, "asset_growth", t)


def accruals_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Backtest-only accruals axis (inverted band); overlaps the Piotroski CFO>NI leg."""
    return _band_axis(m.accruals, "accruals", t)


def shareholder_yield_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Backtest-only shareholder-yield axis (NOT inverted, unlike asset_growth/accruals)."""
    return _band_axis(m.shareholder_yield, "shareholder_yield", t)


def _sue_value(m: StockMetrics, *, sigma_floor: float = _SUE_SIGMA_FLOOR,
               decay_trading_days: int = _SUE_DECAY_TRADING_DAYS,
               min_quarters: int = _SUE_MIN_QUARTERS) -> Optional[float]:
    """Decayed standardized earnings surprise, or None below `min_quarters` usable
    surprises; stats.sue applies the σ-floor and decay. Decay is anchored on the PAST
    report (`earnings_days_since_last_report`), never days-to-next (look-ahead)."""
    if m.earnings_quarters is None or m.earnings_quarters < min_quarters:
        return None
    return _stats_sue(
        m.earnings_last_surprise_pct, m.earnings_surprise_dispersion,
        m.earnings_days_since_last_report,
        sigma_floor=sigma_floor, decay_trading_days=decay_trading_days)


def sue_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Backtest-only SUE axis using the default decay/σ priors. None when the band, the
    inputs or the σ-guard abstain.

    Not reachable from the price-only or XBRL backtests (no earnings history); it rides
    only the snapshot-replay path (SnapshotSignalSource)."""
    if "sue" not in t:
        return None
    v = _sue_value(m)
    return _norm(v, *t["sue"]) if v is not None else None


def residual_momentum_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Backtest axis for residual momentum (NOT inverted). Reconstructable from prices
    alone, so it rides the live-price MomentumSignalSource."""
    return _band_axis(m.residual_momentum, "residual_momentum", t)


def pct_to_52w_high_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Backtest-only nearness-to-52-week-high axis (NOT inverted). Measured for its
    collinearity vs price_vs_200dma, which shares the close/trailing-reference form."""
    return _band_axis(m.pct_to_52w_high, "pct_to_52w_high", t)


def max_daily_return_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Backtest-only MAX-effect axis (inverted band: a lottery-like spike scores low).
    A flipped band would silently invert the sign; a monotonicity test pins it."""
    return _band_axis(m.max_daily_return, "max_daily_return", t)


def vol_scaled_momentum_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Backtest-only vol-scaled-momentum axis (NOT inverted); measured vs residual_momentum."""
    return _band_axis(m.vol_scaled_momentum, "vol_scaled_momentum", t)


def price_vs_200dma_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Backtest-only: the production leg on its own, for leg-level collinearity."""
    return _band_axis(m.price_vs_200dma, "price_vs_200dma", t)


def rel_strength_6m_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Backtest-only: the production leg on its own, for leg-level collinearity."""
    return _band_axis(m.rel_strength_6m, "rel_strength_6m", t)


def net_debt_to_ebitda_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Backtest-only leverage axis (inverted band); production uses the over_leveraged gate."""
    return _band_axis(m.net_debt_to_ebitda, "net_debt_to_ebitda", t)


def ebit_ev_yield_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Backtest-only EBIT/EV yield axis; no production sub-score reads it."""
    return _band_axis(m.ebit_ev_yield, "ebit_ev_yield", t)


def value_fcf_yield_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Backtest-only: the value axis's fcf_yield leg in isolation. Unguarded like
    value_score, since the value bands are always supplied."""
    return _norm(m.fcf_yield, *t["fcf_yield"])


def value_pe_vs_history_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Backtest-only: the value axis's pe_vs_history leg in isolation."""
    return _norm(m.pe_vs_history(), *t["pe_vs_history"])


def value_plus_evebit_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Backtest-only: value_score plus the EBIT/EV leg, to test whether that leg is
    additive to the average. Equals value_score when the leg or its band is absent."""
    legs = [
        _norm(m.upside_to_target(), *t["upside_to_target"]),
        _norm(m.fcf_yield, *t["fcf_yield"]),
        _norm(m.pe_vs_history(), *t["pe_vs_history"]),
        _norm(_scorable_peg(m), *t["peg"]),
    ]
    if "ebit_ev_yield" in t and m.ebit_ev_yield is not None:
        legs.append(_norm(m.ebit_ev_yield, *t["ebit_ev_yield"]))
    return _avg(legs)


# --- Sector-aware abstention -------------------------------------------------
# The *_score helpers above serve tests and the backtest. score() routes through the
# leg machinery below, which masks inapplicable legs and abstains explicitly; for the
# 'unknown' bucket it equals _avg over present legs.

# Defaults let a minimal config (a threshold-only dict) score as bucket 'unknown'.
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
                   config: dict) -> tuple[Optional[float], list[dict[str, str]]]:
    """(sub-score or None, abstentions). The validity floor is bucket-gated; 'unknown'
    scores on any present leg."""
    abst: list[dict[str, str]] = []
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
    # Per-leg MISSING is deliberately not recorded: coverage.py owns it.

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
    """True when the opt-in quality.dilution block is present and enabled."""
    d = ((config or {}).get("quality") or {}).get("dilution")
    return bool(d) and d.get("enabled", True)


def _earnings_quality_on(config: dict) -> bool:
    """True when quality.earnings_quality is present and enabled."""
    d = ((config or {}).get("quality") or {}).get("earnings_quality")
    return bool(d) and d.get("enabled", True)


def _sue_on(config: dict) -> bool:
    """True when the opt-in momentum.sue block is present and enabled."""
    d = ((config or {}).get("momentum") or {}).get("sue")
    return bool(d) and d.get("enabled", True)


def _sue_leg(m: StockMetrics, config: Optional[dict]) -> Optional[_Leg]:
    """The SUE leg, or None when off or the `sue` band is absent. Uses the same guarded
    math as the backtest sue_score."""
    if not _sue_on(config) or "sue" not in ((config or {}).get("thresholds") or {}):
        return None
    blk = ((config or {}).get("momentum") or {}).get("sue") or {}
    v = _sue_value(
        m,
        sigma_floor=blk.get("sigma_floor", _SUE_SIGMA_FLOOR),
        decay_trading_days=blk.get("decay_trading_days", _SUE_DECAY_TRADING_DAYS),
        min_quarters=blk.get("min_quarters", _SUE_MIN_QUARTERS))
    # A None value is still emitted; _eval_subscore drops it from `present`.
    return _Leg("sue", v, "sue")


def _residual_momentum_on(config: dict) -> bool:
    """True when momentum.residual is present and enabled. Ships enabled, unlike the
    other opt-in blocks (see config.yaml)."""
    d = ((config or {}).get("momentum") or {}).get("residual")
    return bool(d) and d.get("enabled", True)


def _shareholder_yield_on(config: dict) -> bool:
    """True when the opt-in value.shareholder_yield block is present and enabled."""
    d = ((config or {}).get("value") or {}).get("shareholder_yield")
    return bool(d) and d.get("enabled", True)


def _upside_to_target_on(config: dict) -> bool:
    """OPT-OUT, unlike every other gate here: an absent key keeps the leg ON.

    Accepts both `{enabled: false}` and a bare `false`; reading only the dict form would
    leave the leg on silently. Why it is in question: docs/ROADMAP.md."""
    d = ((config or {}).get("value") or {}).get("upside_to_target")
    if isinstance(d, dict):
        return bool(d.get("enabled", True))
    if d is None:                # key absent, or present-but-valueless YAML null
        return True
    return bool(d)


def _quality_legs(m: StockMetrics, config: Optional[dict] = None) -> list[_Leg]:
    legs = [
        _Leg("roe", m.roe, "roe"),
        _Leg("net_margin", m.net_margin, "net_margin"),
        _Leg("interest_coverage", m.interest_coverage, "interest_coverage"),
        _Leg("debt_to_equity", m.debt_to_equity, "debt_to_equity"),
    ]
    # Opt-in legs below require their threshold band to be present.
    if _dilution_on(config) and "share_count_cagr" in ((config or {}).get("thresholds") or {}):
        legs.append(_Leg("share_count_cagr", m.share_count_cagr, "share_count_cagr"))
    # Per-leg switches under quality.earnings_quality default to True when absent.
    if _earnings_quality_on(config):
        thresholds = (config or {}).get("thresholds") or {}
        eq = ((config or {}).get("quality") or {}).get("earnings_quality") or {}
        if eq.get("asset_growth", True) and "asset_growth" in thresholds:
            legs.append(_Leg("asset_growth", m.asset_growth, "asset_growth"))
        if eq.get("accruals", True) and "accruals" in thresholds:
            legs.append(_Leg("accruals", m.accruals, "accruals"))
    return legs


def _moat_legs(m: StockMetrics) -> list[_Leg]:
    return [
        _Leg("gross_margin", m.gross_margin, "gross_margin"),
        _Leg("gross_margin_stability", m.gross_margin_stability, "gross_margin_stability"),
        _Leg("roic", m.roic_5y_avg if m.roic_5y_avg is not None else m.roic, "roic"),
    ]


def _growth_legs(m: StockMetrics, config: Optional[dict] = None) -> list[_Leg]:
    # With dilution on, use per-share EPS CAGR when available, else the net-income proxy.
    eps = m.eps_cagr
    if _dilution_on(config) and m.eps_cagr_ps is not None:
        eps = m.eps_cagr_ps
    return [
        _Leg("revenue_cagr", m.revenue_cagr, "revenue_cagr"),
        _Leg("fcf_cagr", m.fcf_cagr, "fcf_cagr"),
        _Leg("eps_cagr", eps, "eps_cagr"),
        _Leg("revenue_growth_persistence", m.revenue_growth_persistence, "revenue_growth_persistence"),
    ]


def _momentum_legs(m: StockMetrics, config: Optional[dict] = None) -> list[_Leg]:
    legs = [
        _Leg("price_vs_200dma", m.price_vs_200dma, "price_vs_200dma"),
        _Leg("rel_strength_6m", m.rel_strength_6m, "rel_strength_6m"),
        _Leg("eps_revision", m.eps_revision, "eps_revision"),
    ]
    # Momentum legs are never sector-masked.
    sue_leg = _sue_leg(m, config)
    if sue_leg is not None:
        legs.append(sue_leg)
    if _residual_momentum_on(config) and "residual_momentum" in ((config or {}).get("thresholds") or {}):
        legs.append(_Leg("residual_momentum", m.residual_momentum, "residual_momentum"))
    return legs


def _value_legs(m: StockMetrics, config: Optional[dict] = None) -> list[_Leg]:
    legs = [
        _Leg("fcf_yield", m.fcf_yield, "fcf_yield"),
        _Leg("pe_vs_history", m.pe_vs_history(), "pe_vs_history"),
        _Leg("peg", _scorable_peg(m), "peg"),
    ]
    # Opt-out (see _upside_to_target_on). Inserted first to keep the reported leg order.
    if _upside_to_target_on(config):
        legs.insert(0, _Leg("upside_to_target", m.upside_to_target(), "upside_to_target"))
    if _shareholder_yield_on(config) and "shareholder_yield" in ((config or {}).get("thresholds") or {}):
        legs.append(_Leg("shareholder_yield", m.shareholder_yield, "shareholder_yield"))
    return legs


def _fcf_excused(m: StockMetrics, fc: dict) -> bool:
    """Negative FCF is excused when growth is strong AND sustained."""
    return (m.revenue_cagr is not None and m.revenue_cagr >= fc["excuse_min_revenue_cagr"]
            and m.revenue_growth_persistence is not None
            and m.revenue_growth_persistence >= fc["excuse_min_persistence"])


def _over_leveraged(m: StockMetrics, g: dict, lv: dict) -> bool:
    """Net-debt/EBITDA primary, with a D/E fallback. Fails OPEN on the equity-distortion
    artifact (D/E <= 0 or above the ceiling), CLOSED on plausible leverage."""
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
    """Hard filters; returns the tripped gate names."""
    tripped: list[str] = []
    fc = g.get("fcf")
    fcf_gate_on = bool(fc) and fc.get("enabled", True)
    if (m.fcf_positive is False and gate_applicable(bucket, "negative_fcf", config)
            and (not fcf_gate_on or not _fcf_excused(m, fc))):
        tripped.append("negative_fcf")
    # Every gate fails OPEN on a missing input (a null market_cap does not trip this
    # one): a hard filter must not disqualify a name for a data gap. coverage.py marks
    # the failed source.
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
    """Soft advisories (parallel to check_gates). None-safe: [] when inputs or config
    are absent."""
    out: list[str] = []
    cs = _flag_block(f, "crowded_short")
    if cs and m.short_pct_outstanding is not None and m.days_to_cover is not None:
        fresh = (m.short_data_age_days is None
                 or m.short_data_age_days <= cs["max_staleness_days"])
        rising_ok = (not cs.get("require_rising")) or (m.short_interest_rising is True)
        if (m.short_pct_outstanding >= cs["min_short_pct_outstanding"]
                and m.days_to_cover >= cs["min_days_to_cover"]
                and rising_ok and fresh):
            out.append("crowded_short")
    # Presence-based; no config thresholds.
    for attr in _FILING_STREAM_FLAGS:
        if getattr(m, attr, None):
            out.append(attr)
    cb = _flag_block(f, "insider_cluster_buy")
    if cb and m.insider_distinct_buyers is not None \
            and m.insider_distinct_buyers >= cb["min_distinct"]:
        out.append("insider_cluster_buy")
    ps = _flag_block(f, "planned_sale")
    if ps and m.insider_planned_sell_value is not None \
            and m.insider_planned_sell_value >= ps["min_value"]:
        out.append("planned_sale")
    dil = _flag_block(f, "dilution")
    if dil and m.share_count_cagr is not None and m.share_count_cagr >= dil["min_share_cagr"]:
        out.append("dilution")
    # Surfaced even when the negative_fcf gate excuses a grower.
    burn = _flag_block(f, "cash_burn")
    if burn and m.fcf_positive is False:
        out.append("cash_burn")
    sh = _flag_block(f, "social_hype")
    if sh and m.social_mentions is not None:
        fresh = (m.social_data_age_days is None
                 or m.social_data_age_days <= sh["max_staleness_days"])
        rising_ok = (not sh.get("require_rising")) or (m.social_mentions_rising is True)
        # A None delta passes: a brand-new spike has no 24h baseline.
        delta_ok = (m.social_mention_delta_pct is None
                    or m.social_mention_delta_pct >= sh.get("min_mention_delta_pct", 0.0))
        if (m.social_mentions >= sh["min_mentions"]
                and rising_ok and delta_ok and fresh):
            out.append("social_hype")

    ns = _flag_block(f, "news_spike")
    # Suppressed on truncated names: their counts are lower bounds and always noisy.
    if ns and m.news_count_7d is not None and not m.news_truncated:
        fresh = (m.news_data_age_days is None
                 or m.news_data_age_days <= ns["max_staleness_days"])
        rising_ok = (not ns.get("require_rising")) or (m.news_flow_rising is True)
        if m.news_count_7d >= ns["min_count_7d"] and rising_ok and fresh:
            out.append("news_spike")

    ft = _flag_block(f, "filing_text_change")
    if ft and m.filing_text_similarity is not None \
            and m.filing_text_similarity < ft["max_similarity"]:
        out.append("filing_text_change")
    return out


def score(m: StockMetrics, config: dict, macro=None) -> ScoreCard:
    t = config["thresholds"]
    w = config["weights"]
    bucket = resolve_bucket(m.sic, config)

    abst: list[dict[str, str]] = []

    def sub(name: str, legs: list[_Leg]) -> Optional[float]:
        s, a = _eval_subscore(name, bucket, legs, t, config)
        abst.extend(a)
        return s

    q = sub("quality", _quality_legs(m, config))
    mo = sub("moat", _moat_legs(m))
    gr = sub("growth", _growth_legs(m, config))
    mom = sub("momentum", _momentum_legs(m, config))
    val = sub("value", _value_legs(m, config))
    # `opp` is display only; value and momentum are weighted independently.
    pres = [x for x in (mom, val) if x is not None]
    opp = max(pres) if pres else None
    ins = insider_score(m, t, config)  # sector-neutral; never masked

    # A component is inapplicable iff its sub-score abstained as 'inapplicable' at
    # subscore scope.
    inapplicable = {a["field"] for a in abst
                    if a["scope"] == "subscore" and a["reason"] == "inapplicable"}

    def applic(*subs: str) -> bool:
        return any(s not in inapplicable for s in subs)

    components = [
        ("quality", q, w["quality"], ("quality",)),
        ("moat", mo, w["moat"], ("moat",)),
        ("growth", gr, w["growth"], ("growth",)),
        ("momentum", mom, w["momentum"], ("momentum",)),
        ("value", val, w["value"], ("value",)),
        ("insider", ins, w["insider"], ("insider",)),
    ]

    # Risk is a composite-only tilt, deliberately kept out of `components` so it never
    # affects confidence/scored/passed. Ships at weight 0.0, so a risk-only card has
    # den == 0 and posts 0.0 (docs/audits/2026-09-25-risk-tilt-disable.md).
    risk_on = ("risk" in w) and ("realized_vol" in t) and ("max_drawdown" in t)
    ri = risk_score(m, t) if risk_on else None

    # Weighted average over present parts: missing weight is redistributed.
    parts = [(s, weight) for _, s, weight, _ in components if s is not None]
    if ri is not None:
        parts.append((ri, w["risk"]))
    num = sum(s * weight for s, weight in parts)
    den = sum(weight for _, weight in parts)
    composite = round(num / den, 1) if den else 0.0

    # Confidence is over APPLICABLE components; the weight floor is bucket-gated.
    appl_w = sum(weight for _, _, weight, subs in components if applic(*subs))
    pres_w = sum(weight for _, s, weight, subs in components
                 if applic(*subs) and s is not None)
    confidence = round(pres_w / appl_w, 3) if appl_w else 0.0
    _v = _validity(config)
    scored = True if bucket == "unknown" else confidence >= _v["min_scored_weight"]
    # Bucket-independent floor: `unknown` otherwise scores at any confidence, so a card
    # whose components are all null could rank on the risk tilt alone. This counts
    # components, not weight: a legitimate momentum-only name has too low a confidence
    # for any weight threshold to separate it from an empty card.
    _min_components = _v.get("min_composite_components")
    if _min_components is not None:
        n_components = sum(1 for _, s, _, _ in components if s is not None)
        scored = scored and n_components >= _min_components
    thin_below = (config.get("ranking") or {}).get("thin_below")
    thin = thin_below is not None and confidence < thin_below

    flags = check_flags(m, config.get("flags") or {})
    # value_trap: with the optional piotroski sub-block, the fraction suppresses the
    # flag on improving names and confirms it on deteriorating ones; suppression wins.
    vt = _flag_block(config.get("flags") or {}, "value_trap")
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

    # risk_off_regime: risk-off macro AND an exposed (leveraged or cyclical) name. Not
    # validatable by the XBRL backtest, which passes no macro/SIC.
    ro = _flag_block(config.get("flags") or {}, "risk_off_regime")
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

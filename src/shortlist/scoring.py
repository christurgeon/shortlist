from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Optional

from .models import ScoreCard, StockMetrics
from .sectors import gate_applicable, leg_applicable, resolve_bucket
from .stats import sue as _stats_sue

# Names check_gates/check_flags/score can emit, DECLARATIVE ONLY: the append
# sites keep their string literals (the byte-identical guarantee on score()),
# tests/test_scoring_names.py AST-scans the emitters and asserts every
# appended literal is declared here, and tests/bot/test_glossary.py asserts
# each name has an /explain glossary entry. Add a gate/flag -> declare it
# here AND document it in bot/glossary.py, or CI fails.
_FILING_STREAM_FLAGS = ("activist_13d", "recent_8k", "passive_13g",
                        "planned_insider_sale_144")
KNOWN_GATES = frozenset({
    "negative_fcf", "below_min_mktcap", "over_leveraged",
    "heavy_insider_selling"})
KNOWN_FLAGS = frozenset({
    "crowded_short", "insider_cluster_buy", "planned_sale", "dilution",
    "cash_burn", "social_hype", "news_spike", "filing_text_change",
    "value_trap", "risk_off_regime", *_FILING_STREAM_FLAGS})

# SUE leg defaults (PREDICTIVE_SIGNALS §1). Used by the backtest-only sue_score and as
# the momentum.sue config fallbacks: decay over ~60 trading days, abstain when the
# firm's own surprise dispersion is below 1 percentage point (the σ≈0 guard).
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


def _avg(components: list[Optional[float]]) -> Optional[float]:
    present = [c for c in components if c is not None]
    return mean(present) if present else None


def _flag_block(flags_cfg: Optional[dict], name: str) -> Optional[dict]:
    """The named soft-flag config block, or None when it is absent/falsy OR
    explicitly disabled (`enabled: false`). Uniform `enabled:` handling for every
    advisory-flag site — a present block defaults to ON (back-compat: the shipped
    config.yaml never sets `enabled` under these blocks except cash_burn's
    `enabled: true`, so this is byte-identical there and on the block-absent
    paths the invariance tests pin)."""
    block = flags_cfg.get(name) if flags_cfg else None
    if not block:
        return None
    return block if block.get("enabled", True) else None


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


def _band_axis(value: Optional[float], key: str, t: dict) -> Optional[float]:
    """Shared shape of the standalone backtest measurement axes below: None when
    the band `key` is absent from the thresholds or the metric is None, else the
    metric mapped through the band via _norm. Inversion is expressed by the band
    ordering [high, low], never here."""
    if key not in t or value is None:
        return None
    return _norm(value, *t[key])


def share_count_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Standalone dilution axis: the inverted share-count-CAGR band -> 0..100 (net
    buybacks score higher than net issuance). Exists so the backtest can measure the
    share-count rank IC on its own (like piotroski_score); the PRODUCTION signal is
    the opt-in quality dilution leg + the `dilution` flag, not this. None when the
    band or the signal is absent."""
    return _band_axis(m.share_count_cagr, "share_count_cagr", t)


def asset_growth_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Standalone asset-growth axis (Cooper-Gulen-Schill 2008): the INVERTED
    asset-growth band -> 0..100 (asset balloon scores low, shrinkers high). Exists
    so the backtest can measure the rank IC on its own (like share_count_score); the
    PRODUCTION signal is the opt-in quality earnings_quality leg, not this. None when
    the band or the signal is absent."""
    return _band_axis(m.asset_growth, "asset_growth", t)


def accruals_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Standalone accruals axis (Sloan 1996): the INVERTED accruals band -> 0..100
    (high accruals = soft earnings score low). Backtest-only, like asset_growth_score;
    partly overlaps the Piotroski CFO>NI leg (the `accruals~piotroski` collinearity
    pair measures it). None when the band or the signal is absent."""
    return _band_axis(m.accruals, "accruals", t)


def shareholder_yield_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Standalone shareholder-yield axis (Boudoukh et al. 2007 / Faber): the (NON-
    inverted) total-payout band -> 0..100 (higher cash returned to owners scores higher,
    UNLIKE asset_growth/accruals which invert). Exists so the backtest can measure the
    rank IC + its collinearity vs fcf_yield and share_count on its own; the PRODUCTION
    signal is the opt-in value shareholder_yield leg, not this. None when the band or the
    signal is absent."""
    return _band_axis(m.shareholder_yield, "shareholder_yield", t)


def _sue_value(m: StockMetrics, *, sigma_floor: float = _SUE_SIGMA_FLOOR,
               decay_trading_days: int = _SUE_DECAY_TRADING_DAYS,
               min_quarters: int = _SUE_MIN_QUARTERS) -> Optional[float]:
    """The decayed standardized-earnings-surprise scalar from the metrics, applying the
    σ-guard (PREDICTIVE_SIGNALS §1). Abstains (None) when the firm has fewer than
    `min_quarters` usable surprises (`earnings_quarters` floor — so the common 1-quarter
    case yields None), then delegates the σ-floor / decay guards to stats.sue. Decay is
    anchored on `earnings_days_since_last_report` (the PAST report), never days-to-next."""
    if m.earnings_quarters is None or m.earnings_quarters < min_quarters:
        return None
    return _stats_sue(
        m.earnings_last_surprise_pct, m.earnings_surprise_dispersion,
        m.earnings_days_since_last_report,
        sigma_floor=sigma_floor, decay_trading_days=decay_trading_days)


def sue_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Standalone SUE axis (Bernard-Thomas 1989, Novy-Marx 2015): the decayed,
    dispersion-standardized earnings surprise mapped through the `sue` band -> 0..100
    (a fresh beat scores high). Backtest-only, like share_count_score / piotroski_score
    — the PRODUCTION signal is the opt-in momentum.sue leg folded into momentum_score,
    not this. Uses the default decay/σ-floor priors (the config knobs are a scoring
    concern). None when the band, the inputs, or the σ-guard abstain.

    MEASUREMENT GATING: SUE is NOT a live-price backtest axis — the momentum backtest
    replays price-only snapshots (`snapshot_from_closes`) that carry NO earnings, and
    historical surprises aren't in SEC companyfacts (so the XBRL path can't reach it
    either). This axis therefore rides ONLY the guarded snapshot-replay path
    (SnapshotSignalSource), which no-ops until daily accumulation exists. See CLAUDE.md."""
    if "sue" not in t:
        return None
    v = _sue_value(m)
    return _norm(v, *t["sue"]) if v is not None else None


def residual_momentum_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Standalone residual-momentum axis (Blitz-Huij-Martens 2011, PREDICTIVE_SIGNALS §2):
    the 12-1 momentum of CAPM (vs SPY) residuals, vol-standardized, mapped through the
    `residual_momentum` band -> 0..100 (a strong de-betaed uptrend scores high; NOT
    inverted). UNLIKE the SUE axis this IS reconstructable from prices alone, so it rides
    the LIVE-price MomentumSignalSource (the dated seam computes m.residual_momentum). The
    PRODUCTION signal is the opt-in momentum.residual leg folded into momentum_score, not
    this — exists so the backtest can measure its rank IC + collinearity vs raw momentum.
    None when the band or the signal is absent."""
    return _band_axis(m.residual_momentum, "residual_momentum", t)


def pct_to_52w_high_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Standalone nearness-to-52-week-high axis (George-Hwang 2004): the `pct_to_52w_high`
    band -> 0..100 (nearer the high scores higher; NOT inverted). Backtest-only measurement
    axis (like residual_momentum_score) — NO production leg reads it; exists so the backtest
    can measure its rank IC + the load-bearing collinearity vs price_vs_200dma (both are
    close/(trailing reference); corr >= 0.5 => duplicate). None when band or metric absent."""
    return _band_axis(m.pct_to_52w_high, "pct_to_52w_high", t)


def max_daily_return_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Standalone MAX-effect axis (Bali-Cakici-Whitelaw 2011): the INVERTED `max_daily_return`
    band -> 0..100 (a big lottery-like daily spike scores LOW — it is a negative predictor).
    Inversion is expressed by the band ordering [high, low] (like accruals_score), so a flipped
    band would silently score high-MAX high; a monotonicity test pins the direction. Backtest-
    only. None when band or metric absent."""
    return _band_axis(m.max_daily_return, "max_daily_return", t)


def vol_scaled_momentum_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Standalone risk-managed-momentum axis (Barroso-Santa-Clara 2015): the `vol_scaled_
    momentum` band -> 0..100 (NOT inverted). Backtest-only; exists so the backtest can measure
    its rank IC + the load-bearing collinearity vs residual_momentum (the expected cousin).
    None when band or metric absent."""
    return _band_axis(m.vol_scaled_momentum, "vol_scaled_momentum", t)


def price_vs_200dma_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Standalone price-vs-200dma LEG axis: the production momentum leg `price_vs_200dma`
    exposed on its own (reusing its existing band) so the leg-level `pct_to_52w_high ~
    price_vs_200dma` collinearity can be measured (the duplication hides at the leg, not the
    momentum sub-score). Backtest-only. None when band or metric absent."""
    return _band_axis(m.price_vs_200dma, "price_vs_200dma", t)


def rel_strength_6m_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Standalone rel-strength-6m LEG axis (reuses the production band) — the companion leg-
    level collinearity reference for pct_to_52w_high / vol_scaled_momentum. Backtest-only.
    None when band or metric absent."""
    return _band_axis(m.rel_strength_6m, "rel_strength_6m", t)


def net_debt_to_ebitda_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Standalone leverage axis for the backtest: inverted net-debt/EBITDA band ->
    0..100 (less leverage scores higher; net cash tops the band). Backtest-only,
    like share_count_score; the PRODUCTION signal is the over_leveraged GATE, not
    this. None when the band or the signal is absent."""
    return _band_axis(m.net_debt_to_ebitda, "net_debt_to_ebitda", t)


def ebit_ev_yield_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Standalone absolute-valuation axis for the backtest: the EBIT/EV earnings-
    yield band -> 0..100 (higher yield = cheaper scores higher). Backtest-only,
    like share_count_score; there is NO production sub-score reading ebit_ev_yield
    yet (spec §11 deferred the leg). None when the band or the signal is absent."""
    return _band_axis(m.ebit_ev_yield, "ebit_ev_yield", t)


def value_fcf_yield_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Backtest-only per-leg attribution: the value axis's fcf_yield leg in
    isolation, so its standalone rank IC sits beside the combined `value` IC."""
    # No band guard (unlike ebit_ev_yield_score): mirrors value_score's unguarded
    # indexing; the value bands are core config the backtest always supplies.
    return _norm(m.fcf_yield, *t["fcf_yield"])


def value_pe_vs_history_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Backtest-only per-leg attribution: the value axis's pe_vs_history leg in
    isolation (see value_fcf_yield_score)."""
    return _norm(m.pe_vs_history(), *t["pe_vs_history"])


def value_plus_evebit_score(m: StockMetrics, t: dict) -> Optional[float]:
    """Backtest-only: the `value` average WITH the EV/EBIT earnings-yield leg
    folded in. Comparing IC(value_plus_evebit) vs IC(value) answers whether the
    leg is additive or dilutive TO THE AVERAGE — the question a standalone-leg IC
    cannot. Mirrors value_score() exactly plus the (None-safe) 5th leg, so it
    equals value_score when ebit_ev_yield / its band is absent. NOT a production
    sub-score."""
    legs = [
        _norm(m.upside_to_target(), *t["upside_to_target"]),
        _norm(m.fcf_yield, *t["fcf_yield"]),
        _norm(m.pe_vs_history(), *t["pe_vs_history"]),
        _norm(m.peg, *t["peg"]),
    ]
    if "ebit_ev_yield" in t and m.ebit_ev_yield is not None:
        legs.append(_norm(m.ebit_ev_yield, *t["ebit_ev_yield"]))
    return _avg(legs)


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
                   config: dict) -> tuple[Optional[float], list[dict[str, str]]]:
    """Return (sub-score or None, abstentions). Replaces silent-drop with an
    explicit applicable/present partition; the floor is bucket-gated so 'unknown'
    stays a bit-identical no-op."""
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


def _earnings_quality_on(config: dict) -> bool:
    """True when the opt-in investment/earnings-quality (asset-growth + accruals)
    scoring block is present and enabled. Absent (it ships commented out) -> the two
    legs are skipped, so quality_score is byte-identical to the pre-feature version.
    Mirrors _dilution_on / the insider.conviction gating."""
    d = ((config or {}).get("quality") or {}).get("earnings_quality")
    return bool(d) and d.get("enabled", True)


def _sue_on(config: dict) -> bool:
    """True when the opt-in SUE (momentum) scoring block is present and enabled. Absent
    (it ships commented out) -> the SUE leg is skipped, so momentum_score is byte-
    identical to the pre-feature scorer. Mirrors _dilution_on / _shareholder_yield_on."""
    d = ((config or {}).get("momentum") or {}).get("sue")
    return bool(d) and d.get("enabled", True)


def _sue_leg(m: StockMetrics, config: Optional[dict]) -> Optional[_Leg]:
    """The opt-in, threshold-guarded SUE momentum leg, or None when OFF / no band /
    the σ-guard abstains. Reads the momentum.sue decay/σ knobs (falling back to the
    module priors), so the SAME guarded math the backtest sue_score uses applies here."""
    if not _sue_on(config) or "sue" not in ((config or {}).get("thresholds") or {}):
        return None
    blk = ((config or {}).get("momentum") or {}).get("sue") or {}
    v = _sue_value(
        m,
        sigma_floor=blk.get("sigma_floor", _SUE_SIGMA_FLOOR),
        decay_trading_days=blk.get("decay_trading_days", _SUE_DECAY_TRADING_DAYS),
        min_quarters=blk.get("min_quarters", _SUE_MIN_QUARTERS))
    # None value -> still emit the leg so _eval_subscore redistributes (None-safe); it is
    # filtered out of `present`. Returning the _Leg with a None value matches the dilution
    # / shareholder_yield precedent (the leg exists; its absence is a missing-value drop).
    return _Leg("sue", v, "sue")


def _residual_momentum_on(config: dict) -> bool:
    """True when the opt-in residual-momentum (momentum) scoring block is present and
    enabled. Absent (it ships commented out) -> the residual-momentum leg is skipped, so
    momentum_score is byte-identical to the pre-feature scorer. Mirrors _sue_on."""
    d = ((config or {}).get("momentum") or {}).get("residual")
    return bool(d) and d.get("enabled", True)


def _shareholder_yield_on(config: dict) -> bool:
    """True when the opt-in total-shareholder-yield (value) scoring block is present
    and enabled. Absent (it ships commented out) -> the value leg is skipped, so
    value_score is byte-identical to the pre-feature version. Mirrors _dilution_on /
    _earnings_quality_on / the insider.conviction gating."""
    d = ((config or {}).get("value") or {}).get("shareholder_yield")
    return bool(d) and d.get("enabled", True)


def _upside_to_target_on(config: dict) -> bool:
    """OPT-OUT, and the only one here — every other config gate above is opt-IN.

    `upside_to_target` (sell-side price target vs price) is a SHIPPED default leg with
    mandatory thresholds, so an absent key must keep it ON or an untouched config.yaml
    would silently change every value score. The knob exists because the leg is a
    standing MEASUREMENT question, not because it is suspect today: Brav & Lehavy find
    the target LEVEL negatively related to realised returns while the REVISION predicts
    (`docs/PREDICTIVE_SIGNALS_RESEARCH.md` §Quick wins #1). Until that is measured
    point-in-time on this data, flipping the default is exactly the move CLAUDE.md's
    design premise forbids — so this makes the leg togglable and changes nothing.

    Accepts BOTH spellings, because getting this wrong is worse than not shipping the
    knob: `{enabled: false}` and a bare `false` must both switch the leg off. The bare
    boolean is the idiom `quality.earnings_quality.asset_growth`/`accruals` already use
    in this same config, so an operator will reach for it — and under an
    `isinstance(d, dict)`-only read it would silently leave the leg ON and produce a
    "leg-off" run byte-identical to baseline. A silently-ignored switch on a
    measurement knob is a wrong measurement, not a no-op."""
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
    # Capital-allocation / dilution leg (inverted band: net buybacks score higher
    # than net issuance). Opt-in; the threshold guard keeps it None-safe if a config
    # enables the block without the band. None signal -> _eval_subscore redistributes.
    if _dilution_on(config) and "share_count_cagr" in ((config or {}).get("thresholds") or {}):
        legs.append(_Leg("share_count_cagr", m.share_count_cagr, "share_count_cagr"))
    # Investment & earnings-quality legs (PREDICTIVE_SIGNALS §3): asset growth and
    # accruals, both INVERTED (high -> lower quality). Opt-in + threshold-guarded like
    # the dilution leg; absent -> byte-identical. Masked for financials/REITs (the leg
    # names are in sectors.masked_legs) so they abstain there on the production path.
    # Each leg also has a per-leg on/off switch under quality.earnings_quality
    # (asset_growth / accruals), defaulting to True when absent -> an `enabled: true`
    # block with no per-leg keys keeps both legs (back-compat). Shipped config enables
    # accruals only (validated XS-IC +0.036 t=2.1 broad); asset_growth stays measured-
    # but-off in the backtest (no cross-sectional edge: XS-IC -0.006 t=-0.3).
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


def _momentum_legs(m: StockMetrics, config: Optional[dict] = None) -> list[_Leg]:
    legs = [
        _Leg("price_vs_200dma", m.price_vs_200dma, "price_vs_200dma"),
        _Leg("rel_strength_6m", m.rel_strength_6m, "rel_strength_6m"),
        _Leg("eps_revision", m.eps_revision, "eps_revision"),
    ]
    # SUE / post-earnings-announcement drift (PREDICTIVE_SIGNALS §1): the decayed,
    # dispersion-standardized earnings surprise. A fundamental momentum leg that price
    # momentum only weakly proxies (Novy-Marx 2015). Opt-in + threshold-guarded like the
    # value/quality legs; absent -> byte-identical. Momentum legs are never sector-masked,
    # so SUE is universally applicable (unlike the balance-sheet legs).
    sue_leg = _sue_leg(m, config)
    if sue_leg is not None:
        legs.append(sue_leg)
    # Residual (idiosyncratic) momentum (PREDICTIVE_SIGNALS §2): the 12-1 momentum of CAPM
    # residuals, vol-standardized. A de-betaed momentum leg shown to ~2x the Sharpe of raw
    # momentum (Blitz-Huij-Martens 2011) — but some replications report it underperforming,
    # so it ships OFF as a MEASURED candidate, NOT a replacement for the raw-momentum legs.
    # Opt-in + threshold-guarded like the SUE leg; absent -> byte-identical. Momentum legs
    # are never sector-masked. None signal -> _eval_subscore redistributes (None-safe).
    if _residual_momentum_on(config) and "residual_momentum" in ((config or {}).get("thresholds") or {}):
        legs.append(_Leg("residual_momentum", m.residual_momentum, "residual_momentum"))
    return legs


def _value_legs(m: StockMetrics, config: Optional[dict] = None) -> list[_Leg]:
    legs = [
        _Leg("fcf_yield", m.fcf_yield, "fcf_yield"),
        _Leg("pe_vs_history", m.pe_vs_history(), "pe_vs_history"),
        _Leg("peg", m.peg, "peg"),
    ]
    # Opt-OUT (see _upside_to_target_on): ON unless config disables it, and inserted at
    # the front so the leg ORDER is unchanged from before the knob existed — _eval_subscore
    # is order-sensitive only in what it reports, but a reordered `present` list would
    # churn every coverage/abstention fixture for no reason.
    if _upside_to_target_on(config):
        legs.insert(0, _Leg("upside_to_target", m.upside_to_target(), "upside_to_target"))
    # Total shareholder yield (PREDICTIVE_SIGNALS §5): dividends + net buybacks + net
    # debt reduction / market_cap. A POSITIVE predictor scored STRAIGHT (high -> high,
    # NOT inverted like the §3 legs). Opt-in + threshold-guarded like the dilution leg;
    # absent -> byte-identical. Masked for financials (the leg name is in
    # sectors.masked_legs) so it abstains there on the production path.
    if _shareholder_yield_on(config) and "shareholder_yield" in ((config or {}).get("thresholds") or {}):
        legs.append(_Leg("shareholder_yield", m.shareholder_yield, "shareholder_yield"))
    return legs


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
    cs = _flag_block(f, "crowded_short")
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
    # Dilution advisory: persistent net share issuance. Soft/None-safe like the
    # others — no-op when the config block is absent; never affects passed/composite.
    dil = _flag_block(f, "dilution")
    if dil and m.share_count_cagr is not None and m.share_count_cagr >= dil["min_share_cagr"]:
        out.append("dilution")
    # Cash-burn advisory: ALWAYS visible when FCF is negative (the stage-aware
    # negative_fcf gate may excuse a grower, but the burn is still surfaced).
    burn = _flag_block(f, "cash_burn")
    if burn and m.fcf_positive is False:
        out.append("cash_burn")
    # Social-media hype advisory (WSB via ApeWisdom). Soft/None-safe like the others —
    # no-op when the config block is absent; never affects passed/composite/scored.
    # "Context-aware" by coexistence: renders alongside crowded_short (squeeze) or
    # value_trap (pump caution) — no extra logic needed.
    sh = _flag_block(f, "social_hype")
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

    # news_spike: elevated AND rising mainstream news flow (Finnhub company-news).
    # Advisory only; mirrors social_hype. No-op when the config block is absent.
    ns = _flag_block(f, "news_spike")
    # Explicitly suppress on truncated (free-tier-capped, always-noisy) names: a spike
    # is meaningful for a normally-quiet name, and the counts are lower bounds there.
    if ns and m.news_count_7d is not None and not m.news_truncated:
        fresh = (m.news_data_age_days is None
                 or m.news_data_age_days <= ns["max_staleness_days"])
        rising_ok = (not ns.get("require_rising")) or (m.news_flow_rising is True)
        if m.news_count_7d >= ns["min_count_7d"] and rising_ok and fresh:
            out.append("news_spike")

    # filing_text_change ("Lazy Prices", PREDICTIVE_SIGNALS §4): a big YoY change in
    # the 10-K/10-Q risk-factor + MD&A language predicts negative returns. Fires when
    # the point-in-time similarity vs the immediately-prior same-type filing is BELOW
    # max_similarity. Advisory only; mirrors social_hype/news_spike — None-safe, a
    # no-op when the config block is absent, never affects passed/composite/scored.
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
    _v = _validity(config)
    scored = True if bucket == "unknown" else confidence >= _v["min_scored_weight"]
    # Bucket-INDEPENDENT floor. The line above lets `unknown` — the MAJORITY bucket —
    # score at any confidence, including 0.0. On 2026-08-10 that put BRVE top of the
    # report at composite 100.0 with ALL SIX components null: the weight redistributed
    # onto `risk` alone, which read 100 because the issuer reports no debt.
    #
    # This counts COMPONENTS, not weight. A weight/confidence threshold cannot express
    # the rule: a momentum-only name sits at confidence ~0.08 and is pinned as scored
    # (test_scoring_abstention.py:test_unknown_momentum_only_name_still_scored), while
    # BRVE sits at 0.0 — far too narrow a band to threshold safely. The honest
    # distinction is categorical: `risk` is a composite-only TILT, deliberately kept out
    # of `components` (see above), so a card carrying only the tilt has no scoring
    # component at all. No-op when the key is absent.
    _min_components = _v.get("min_composite_components")
    if _min_components is not None:
        n_components = sum(1 for _, s, _, _ in components if s is not None)
        scored = scored and n_components >= _min_components
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

    # risk-off regime advisory: soft/None-safe like value_trap. Fires only in a
    # risk-off macro regime AND on an exposed name (leveraged OR cyclical bucket).
    # Never affects passed/composite/scored. No-op when macro is None (overlay
    # disabled / fetch failed) or the config block is absent — keeps score()
    # byte-identical to the pre-feature scorer. NOTE: this signal is intentionally
    # NOT validatable by the XBRL backtest (backtest/signals.py passes no macro/SIC).
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

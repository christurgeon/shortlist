"""Investability floor — can a retail-scale book act on this name at all?

**Pure. No I/O.** The caller supplies both bulk maps; the whole floor is testable offline.

Deliberately distinct from `quality_floor`, which asks whether the BUSINESS is structurally
broken (no revenue; insolvent and burning cash). This asks whether the SECURITY is
reachable. The two are independent: a sound little company trading $20k/day is a fine
business and an unactionable idea, and a deep-screen slot spent on it buys nothing.

**Why this exists (measured, not assumed).** Across the three enabled originators the
25th-percentile pick is a **$15M** market cap and a third of all picks sit below $50M —
shell territory no account size can trade. Meanwhile the scoring gate
(`gates.min_market_cap`) sat at $2B, so ~80% of deep-screen slots went to names the gate
was configured to reject. Full evidence, including the floor-sensitivity curve that chose
these defaults: `docs/audits/2026-08-07-investability-floor.md`.

**Dollar volume, never share volume.** 1M shares/day of a $0.20 stock is $200k/day. The
share-count floor already in `short_interest.py` is not reused here for exactly that reason.

**Abstain, never guess.** Every rule fires only on a positive, present value. A missing
market cap is COMMON and means *unknown*, not *small* — `_normalize_finnhub` abstains on
non-USD caps (the TSM fix), so dropping on absence would silently delete foreign issuers.
A `0.0` is treated as a parse artifact, not a measurement: FINRA rows legitimately carry
zero-volume entries for non-trading issues.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Liquidity:
    """One candidate's tradeability inputs, joined from two bulk sources.

    `market_cap` / `last_sale` come from the listed-universe screener; `adv_shares` from the
    FINRA consolidated short-interest dataset, which carries `averageDailyVolumeQuantity`
    for 86% of its rows and covers 93% of ledger tickers. That dataset is **semi-monthly**,
    so ADV can be up to ~4 weeks stale — acceptable for a liquidity floor (ADV is
    slow-moving) and stated at the fetch site rather than compensated for here.
    """
    market_cap: Optional[float] = None
    adv_shares: Optional[float] = None
    last_sale: Optional[float] = None

    @property
    def dollar_adv(self) -> Optional[float]:
        """Average daily DOLLAR volume, or None when either input is missing/non-positive."""
        if not self.adv_shares or not self.last_sale:
            return None
        if self.adv_shares <= 0 or self.last_sale <= 0:
            return None
        return self.adv_shares * self.last_sale


@dataclass(frozen=True)
class Verdict:
    keep: bool
    reason: str = ""


_KEEP = Verdict(keep=True)


def assess(liq: Liquidity, *, min_market_cap: float, min_dollar_adv: float) -> Verdict:
    """Keep/drop for one candidate. Abstains (keeps) on any missing or non-positive input."""
    cap = liq.market_cap
    if cap is not None and cap > 0 and cap < min_market_cap:
        return Verdict(keep=False,
                       reason=f"market cap ${cap / 1e6:,.0f}M below "
                              f"${min_market_cap / 1e6:,.0f}M floor")
    dv = liq.dollar_adv
    if dv is not None and dv < min_dollar_adv:
        return Verdict(keep=False,
                       reason=f"avg daily volume ${dv / 1e3:,.0f}k below "
                              f"${min_dollar_adv / 1e3:,.0f}k floor")
    return _KEEP


def liquidity_from_universe(*, universe: dict, adv_shares: dict) -> dict:
    """`{UPPER ticker -> Liquidity}` from two already-fetched bulk maps. **Pure.**

    Driven by `universe` (ticker -> `(market_cap, last_sale)`), not by the volume map: a
    symbol absent from the listed-stock screener is not a listed common stock we can size.
    A ticker present in `universe` but missing from `adv_shares` still gets a row with
    `adv_shares=None` so `assess` abstains — dropping the row entirely would reach the same
    verdict today but would hide the coverage gap from the manifest notes.
    """
    out: dict = {}
    for ticker, pair in (universe or {}).items():
        cap, price = (pair if isinstance(pair, (tuple, list)) else (pair, None))
        out[str(ticker).upper()] = Liquidity(
            market_cap=cap, last_sale=price,
            adv_shares=(adv_shares or {}).get(str(ticker).upper()))
    return out


def verdicts_from_liquidity(liq: dict, *, min_market_cap: float,
                            min_dollar_adv: float) -> dict:
    """`{UPPER ticker -> Verdict}` containing **only drops**.

    Keeps are omitted deliberately, mirroring `quality_floor.verdicts_from_fundamentals`:
    the funnel stage already treats absent-from-map as abstain, so emitting keeps would be
    redundant surface that could drift out of sync with that rule.
    """
    return {t: v
            for t, x in (liq or {}).items()
            for v in (assess(x, min_market_cap=min_market_cap,
                             min_dollar_adv=min_dollar_adv),)
            if not v.keep}

"""Deep-screen slot hygiene — drop candidates that cannot be a good buy under any reading.

**The binding constraint in this funnel is not candidate volume, it is the ~10 deep-screen
slots per night** (`scout.daily_x`). Discovery routinely surfaces more names than that, and
`budget.select` currently orders purely by signal weight — it knows nothing about the
business. Until now that was unavoidable: assessing fundamentals required the very per-ticker
screen we were trying to allocate.

NOTE on cost (corrected 2026-08-06): the nightly digest runs the FREE chain —
`daily_push.include_fmp: false` makes `digest_sources` drop FMP — so a wasted slot costs a
Yahoo/Finnhub/EDGAR screen and a line of the digest, NOT FMP quota. FMP quota binds the bot's
`/screen` and `/deep`, which keep the full chain. The floor is still worth having (a slot is
still finite), but do not justify it with an FMP figure.

SEC `frames` (`data/secframes.py`) breaks that circularity: ~12 requests buys fundamentals
for the whole universe, so a candidate can be checked *before* it consumes a slot.

**This is a FLOOR, not a ranker — deliberately.** An adversarial review (2026-08-05) made the
point sharply: a full-universe fundamental *ranking* is the existing quality/value composite
run at S&P-1500 scale, which is exactly the add-scoring-surface move this repo has killed
four times over (buyback, leverage tilt, accruals, EV/EBIT). Dropping the structurally unfit
is a different and much weaker claim — it needs no return study, only the observation that
the slot buys nothing. Anything stronger (re-ordering, weighting, originating) requires a
pre-registered cohort first.

Two rules, both conservative, both abstaining on missing data:

1. **No revenue** (present and <= 0) — a pre-revenue shell has no business to assess, and the
   scorer abstains on most of its legs anyway.
2. **Negative equity AND negative earnings AND negative operating cash flow** — impaired,
   unprofitable, *and* burning cash.

Rule 2's conjunction is load-bearing, twice over:

- **Buyback compounders.** Sustained buybacks drive book equity negative at healthy,
  profitable companies; CLAUDE.md records that exact trap for the `over_leveraged` gate,
  where an unguarded D/E rule would have flagged them. Negative equity alone must never drop
  a name — hence the earnings condition.
- **REITs.** Accumulated depreciation routinely leaves REITs with negative book equity AND
  negative GAAP earnings while they generate real cash. `GIPR` in the live selection ledger
  is exactly this shape and was a false positive before the OCF condition was added. Requiring
  cash burn separates "accounting losses from a non-cash charge" from "actually burning
  cash", **without needing a sector lookup the live path cannot cheaply perform** — the
  scorer's `sectors.masked_legs` has SIC available; this stage does not.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Fundamentals:
    """The minimal, best-covered slice of the universe snapshot.

    Field choice follows the measured coverage in
    `docs/audits/2026-08-05-dera-tag-coverage.md`: assets 99.4%, ocf 98.8%, net income 94.9%,
    equity 92.8%, revenue 79.4%. Gross margin (50.7%) and anything needing capex (70.1%) are
    deliberately absent — half the universe would abstain, and an abstention-heavy floor is
    a floor that does nothing.
    """
    cik: str
    revenue: Optional[float] = None
    net_income: Optional[float] = None
    equity: Optional[float] = None
    assets: Optional[float] = None
    ocf: Optional[float] = None      # operating cash flow — the REIT/depreciation guard


@dataclass(frozen=True)
class Verdict:
    keep: bool
    reason: str = ""


_KEEP = Verdict(keep=True)


def assess(f: Fundamentals) -> Verdict:
    """Keep/drop for one candidate. Abstains (keeps) whenever the inputs are missing —
    absent fundamentals are a coverage gap, not evidence of a bad business, and dropping on
    absence would quietly bias the funnel toward well-tagged filers."""
    if f.revenue is not None and f.revenue <= 0:
        return Verdict(keep=False, reason="no revenue (shell / pre-revenue)")
    if (f.equity is not None and f.equity <= 0
            and f.net_income is not None and f.net_income <= 0
            and f.ocf is not None and f.ocf <= 0):
        return Verdict(keep=False,
                       reason="negative equity, negative earnings and cash burn")
    return _KEEP


def _val(frames: dict, cik: str) -> Optional[float]:
    fr = (frames or {}).get(cik)
    return None if fr is None else fr.val


def fundamentals_from_frames(*, cik_to_ticker: dict, revenue: dict, net_income: dict,
                             equity: dict, assets: dict, ocf: dict = None) -> dict:
    """`{UPPER ticker -> Fundamentals}` from already-fetched SEC frames. **Pure** — the
    caller does the fetching, so the whole floor is testable offline.

    Driven by `cik_to_ticker`, not by the frames: a filer with no listed ticker cannot be a
    candidate, so it never enters the map. An absent value stays `None` (never coerced to
    0.0) because `assess` treats 0 as a real shell signal and `None` as abstain — conflating
    them would turn every coverage gap into a drop.
    """
    out: dict = {}
    for cik, ticker in (cik_to_ticker or {}).items():
        out[str(ticker).upper()] = Fundamentals(
            cik=cik,
            revenue=_val(revenue, cik),
            net_income=_val(net_income, cik),
            equity=_val(equity, cik),
            assets=_val(assets, cik),
            ocf=_val(ocf, cik),
        )
    return out


def verdicts_from_fundamentals(funds: dict) -> dict:
    """`{UPPER ticker -> Verdict}` containing **only drops**.

    Keeps are omitted deliberately: `funnel.apply_quality_floor` already treats
    absent-from-map as abstain, so emitting keeps too would be redundant surface that could
    drift out of sync with that rule.
    """
    return {t: v for t, f in (funds or {}).items()
            for v in (assess(f),) if not v.keep}

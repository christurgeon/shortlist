# Deep-screen quality floor — evidence (2026-08-05)

**What it is:** a filter that drops candidates whose fundamentals make a deep-screen slot a
waste, running between the 8-K veto and `budget.select`. Ships **OFF**
(`scout.quality_floor.enabled: false`).

> **CORRECTION 2026-08-06.** This document originally said the slots cost **FMP quota**. They
> do not: the nightly digest runs the free chain (`daily_push.include_fmp: false` drops FMP),
> so a slot costs a Yahoo/Finnhub/EDGAR screen and a line of the digest. FMP quota binds the
> bot's `/screen` and `/deep` only. The floor's justification is unchanged (a slot is finite),
> but it must not be argued from an FMP number.

**Why a floor and not a ranker.** The binding constraint in this funnel is not candidate
volume — it is the **~10 deep-screen slots per night**. `budget.select` orders purely by signal
weight and knows nothing about the business, because assessing fundamentals used to require
the very per-ticker screen we were allocating. SEC `frames` breaks that circularity. But an
adversarial review was blunt that a full-universe *ranking* is the existing quality/value
composite at S&P-1500 scale — the add-scoring-surface move this repo has killed four times.
Dropping the structurally unfit is a weaker, defensible claim needing no return study.

---

## 1. Feasibility, measured live

| | |
|---|---|
| universe | **7,999 listed filers** |
| SEC requests | **16** cold, **6** warm (day-cached) |
| wall time | **~5 s** |
| coverage | revenue 57.1%, net income 66.7%, equity 65.3%, assets 69.3% |

Coverage is lower than the DERA tag-coverage audit's figures (79–99%) for a benign reason:
that measured *10-K/10-Q filers*, this measures **all 7,999 listed tickers**, which includes
ETFs, trusts and funds that never file those forms. They abstain, which is correct.

## 2. Against the real selection ledger

135 distinct tickers across 188 pick-rows in `ScoutState.picks`; **96% resolve** into the
frames universe.

**7 of 135 (5.2%) would have been dropped:**

| ticker | revenue | net income | equity | OCF |
|---|---|---|---|---|
| BENF | −39.1M | −87.4M | — | −38.7M |
| CALC | — | −29.6M | −1.1M | −21.2M |
| CUEN | 0.7M | −1.6M | −3.4M | −1.4M |
| DYNC | — | −13.2M | −17.3M | −2.0M |
| RTB | 11.1M | −17.5M | −1.0M | −23.0M |
| USO | −56.2M | −64.7M | — | −21.9M |
| VVOS | 17.4M | −21.2M | −1.1M | −15.3M |

Every one is burning cash. `USO` is an oil **ETF** — correctly excluded from a stock funnel.

**6 of the 7 came from `edgar:activist_13d`** (the rest: 1 Form 4, 2 WSB — sums >7 because a
ticker can be surfaced by several originators). That independently corroborates the
2026-07-26 composition audit, which found `edgar:activist_13d` selects at a **$50M median
market cap**. The floor is catching exactly the nano-cap shells that audit complained about,
in the funnel's **highest-weight** originator.

## 3. Two false-positive guards, both found by measurement

The first version dropped **9** names. Two were false positives, and both are now guarded:

- **`GIPR` — a REIT.** Accumulated depreciation routinely leaves REITs with negative book
  equity *and* negative GAAP earnings while they generate real cash. GIPR's OCF is
  **+$929k**. Dropping it would have been wrong.
- **`COE`** — $95.6M revenue, negative equity and earnings, but positive operating cash flow.
  A real business with accounting losses.

**The fix is structural, not a sector lookup.** Rule 2 now requires negative equity **and**
negative earnings **and** negative operating cash flow. That separates "accounting losses
from a non-cash charge" from "actually burning cash" **without needing SIC**, which this
stage cannot cheaply obtain (the scorer's `sectors.masked_legs` runs later, where SIC is
available). Universe drops fell 669 → 577 (8.4% → 7.2%) and ledger drops 9 → 7, so the guard
discriminates rather than blanket-disabling.

The sibling guard — **negative equity alone must never drop a name**, because sustained
buybacks drive book equity negative at healthy compounders — is the same trap CLAUDE.md
records for the `over_leveraged` gate. Both are pinned by tests.

## 4. Limits, stated plainly

- **No return evidence.** This says 5.2% of past picks were *structurally unfit*, not that
  dropping them would have made money. The claim is slot economics, not alpha.
- **`frames` is LIVE-ONLY.** It carries no filing date, so a restatement silently overwrites
  what was knowable. Any backfill must use the DERA archive
  (`2026-08-05-standing-screen-data-source.md` §3).
- **Ships OFF.** Enabling changes which names get screened, so it is a selection-surface
  change. The evidence above is a reason to *consider* enabling, not a substitute for the
  decision.
- **Non-10-K filers abstain wholesale.** ETFs and funds mostly lack these tags entirely, so
  only the ones with negative revenue (like `USO`) are caught. A proper security-type filter
  is separate work — the ledger also contains `VOO`, `SOXL`, `BBASX`, `FTECX`.

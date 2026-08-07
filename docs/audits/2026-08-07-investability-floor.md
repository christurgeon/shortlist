# Making the funnel's output actionable — the investability floor (2026-08-07)

**What this is:** the evidence behind two changes that ship together — a new
`scout.investable_floor` funnel stage, and lowering `gates.min_market_cap` from **$2B to
$300M**. Measured against the live 196-pick selection ledger, 25 sessions of production
manifests, and a live pull of the listed universe.

**The problem, from `2026-08-07-funnel-gate-mismatch.md`:** the discovery layer finds names
the scorer is configured to reject. `edgar:activist_13d`'s median pick is **$50M** against a
**$2B** gate, so ~80% of deep-screen slots produced a name that could never reach the
`/deep` actionable block.

**Result: actionable picks go from 26% to 45% of the ledger, and zero-candidate nights fall
from 13 of 25 to 2 of 25.**

---

## 1. Why the gate moved, and what kind of claim that is

**This is an investability mandate, not an alpha claim.** No backtest says $300M outperforms
$2B and none is asserted here. Cohort measurement is blocked on price history; that is not
what this decision rests on.

What it rests on is that a $2B floor is **mismatched to the originators feeding it**.
Activism and insider buying structurally happen at small caps — that is what the enabled
signals are built to find. The distribution of what they actually surface:

| percentile | market cap |
|---|---|
| p10 | $6M |
| p25 | **$15M** |
| p50 | $196M |
| p75 | $1.0B |
| p90 | $34.9B |

The p75→p90 jump is the barbell the 2026-07-26 composition audit described, in one line. A
$2B floor does not trim a tail of that distribution; it deletes the body of it.

$300M sits inside the **$0.3–10B band** that audit identifies as retail-accessible. It is
reversible in one line, and the selection ledger records what surfaces — so this is a
checkable decision, not a permanent bet.

## 2. The floor-sensitivity curve that chose the numbers

Ledger picks from the three enabled originators (n=100 over 25 sessions), keeping names at
or above each cap floor and **abstaining (keeping) where market cap is unknown**:

| cap floor | picks kept | sessions with ≥1 | zero nights | median/night |
|---|---|---|---|---|
| none | 100% | 25/25 | 0 | 4 |
| **$50M** | 67% | 22/25 | 3 | 3 |
| **$100M** ← chosen | **57%** | **21/25** | 4 | 2 |
| $300M | 46% | 19/25 | 6 | 1 |
| $500M | 36% | 15/25 | 10 | 1 |
| $1B | 30% | 12/25 | 13 | 0 |
| **$2B** (previous gate) | 26% | 12/25 | **13** | 0 |

There is a knee. Below ~$100M the funnel is carrying shells; above ~$300M it starts emptying
sessions outright. **A third of all picks sit below $50M** — territory no account size can
trade, where spreads eat any edge and where EDGAR-driven microcap discovery overlaps
directly with where stock promoters operate.

Two instruments, deliberately at different levels:

- **`scout.investable_floor` at $100M** — the cheap early cut, before a slot is spent.
- **`gates.min_market_cap` at $300M** — the actionability line, applied after scoring.

A test pins the gate **above** the floor. Inverting them would make the floor the real gate
and silently change what `passed` means.

## 3. Dollar volume is the instrument that actually decides

Market cap is a poor tradeability proxy at this size: a $200M company might trade $600k/day
or $30k/day, and only volume distinguishes them. The floor's second leg is **average daily
dollar volume ≥ $500k**.

**Dollar, never share, volume.** 1M shares/day of a $0.20 stock is $200k/day — thin. The
existing share-count floor in `short_interest.py` is a different instrument and is
deliberately *not* reused.

**Source: the FINRA consolidated short-interest dataset the harness already fetches and
disk-caches** (`averageDailyVolumeQuantity`). Zero additional requests, no new dependency,
no sec.gov budget. Coverage: 86% of FINRA rows carry ADV, and **93% of every ticker this
funnel has ever surfaced** is present.

**Known limitation, stated rather than compensated for:** FINRA short interest is
**semi-monthly**, so an ADV value can be ~4 weeks stale. That is acceptable for a floor —
ADV is slow-moving — and is exactly why this feeds a floor and never a ranking input.

## 4. Measured effect

Live universe pull (2026-08-07): **7,095 symbols, 5,822 with a usable market cap**. Applying
both floors drops **2,430 of 7,095 (34%)** universe-wide.

Against the ledger:

| measure | before | after |
|---|---|---|
| picks dropped pre-screen | 0 | 39 of 100 (39%) |
| median survivors/session | 4 | 2 |
| zero-candidate nights | 0 | 2 of 25 |
| **actionable (clears floor AND gate)** | **26 of 100** | **45 of 100** |

Sample drops, showing both legs firing: `NEN` — volume $26k/day; `PASG` — cap $15M; `CRGO` —
cap $65M; `BRT` — volume $491k/day.

`BRT` at $491k against a $500k floor is worth naming: a hard threshold makes arbitrary calls
at its boundary. That is inherent to a floor and is the accepted cost of one; it is not
evidence the number is wrong, but it is the reason the floor is generous rather than tight.

## 5. Design contracts

- **Abstain, never guess.** Every leg fires only on a positive, present value. A missing
  market cap is *common* — `_normalize_finnhub` abstains on non-USD caps (the TSM fix) — and
  means "unknown", not "small". A `0.0` is treated as a parse artifact: FINRA legitimately
  carries zero-volume rows for non-trading issues.
- **A candidate absent from the universe is KEPT.** Absence also captures OTC names, recent
  listings and plain API gaps.
- **Absent or disabled block ⇒ byte-identical funnel with ZERO fetches**, pinned by
  `tests/test_scout_investable_wiring.py`. Both fetch doubles in those tests *raise*, so the
  inertness assertion cannot pass as a tautology.
- **Every failure degrades to inert plus a LOUD manifest note** — a raise, an empty
  universe, and a FINRA outage each have their own note. A FINRA failure still applies the
  cap leg; screening unprotected is never silent.
- **Every drop is named with its reason** in `manifest.notes`, never a bare count — an
  over-aggressive floor must be visible without an audit.
- **`api.nasdaq.com` is undocumented**, the same fragility class as the retired Yahoo
  screener. The mitigation is that failure is loud and inert, not that it will not happen.
  It is **not** on sec.gov and must never be routed through `sec_throttle()` — padding that
  budget with unrelated hosts would misreport the thing it exists to measure.

## 6. What this does NOT claim

- **No return prediction.** Neither the floor nor the new gate value is backed by forward
  returns; both are investability constraints. The originators feeding them remain
  unmeasured on this repo's own data (`edgar_form4`, `edgar_13f`) or measured at
  approximately zero (`edgar_activist_13d`, −0.43%/mo with a CI spanning zero).
- **It does not fix composition.** It removes the untradeable tail; it does not make the
  surviving names better businesses. `scout.quality_floor` is the instrument for that and
  remains OFF pending its own bar.
- **It is not a substitute for the deep dive.** Smaller companies fail more often, and this
  change surfaces more of them. The funnel is triage.

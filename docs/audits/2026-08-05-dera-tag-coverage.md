# DERA tag coverage — what a standing pre-rank can actually be built on (2026-08-05)

**Purpose (plan Phase 2, step 2):** the follow-on the DERA spike
(`2026-08-05-standing-screen-spike-dera.md` §5) called for — *measure which XBRL tags are
populated, and where coverage collapses by sector*, before designing any ranking.

Offline pass over `2026q1.zip` already on disk. Reuses the repo's **existing** tag families
(`providers/_xbrl_facts.py`: `REVENUE`, `NET_INCOME`, `OCF`, `CAPEX`, `COGS`, `EQUITY`,
`ASSETS`) so this cannot drift from the XBRL backtest's definition of "revenue", and the
production `sectors.resolve_bucket` for sector attribution.

Universe: **4,708 listed 10-K/10-Q filings** (`20-F`/`40-F` excluded — the research layer is
10-K-only and foreign issuers already get an ADR-aware skip).

---

## 1. Raw family coverage

| family | coverage |
|---|---|
| assets | **99.4%** |
| ocf | **98.8%** |
| net_income | **94.9%** |
| equity | **92.8%** |
| liabilities | 87.9% |
| shares_out | 80.8% |
| revenue | 79.4% |
| capex | 70.1% |
| **cogs** | **51.0%** |

## 2. Derivable pre-rank inputs

| derived input | coverage |
|---|---|
| accruals inputs (ni + ocf + assets) | **93.9%** |
| ROE (ni + equity) | **89.1%** |
| net margin (rev + ni) | 75.3% |
| FCF (ocf − capex) | 70.1% |
| **gross margin (rev + cogs)** | **50.7%** |

**ROE and the accruals triple are the only cross-sector-robust inputs. Gross margin is not
usable** — half the universe would abstain.

## 3. Growth is computable from ONE file — the significant finding

A 10-K carries prior-period comparatives, so a single 85 MB quarterly download yields a time
series without multi-quarter ingestion. Annual figures only (`qtrs=4`, parent-only, us-gaap):

| distinct annual periods | filings | share |
|---|---|---|
| 1 | 159 | 3.5% |
| 2 | 1,634 | 35.8% |
| 3 | 2,725 | **59.7%** |
| 4+ | 47 | 1.0% |

- **YoY growth computable: 96.5%**
- **2-year CAGR computable: 60.7%**

This removes the ingestion job the spike assumed would be needed for any growth axis.

## 4. Sector breakdown — abstention lands where the scorer already masks

| bucket | n | revenue | ROE | FCF | gross margin |
|---|---|---|---|---|---|
| unknown (operating cos) | 3,926 | 81.4% | 89.4% | 72.2% | 58.3% |
| financials | 466 | 55.6% | 91.4% | 80.0% | **9.2%** |
| reit | 193 | 82.9% | 78.2% | **9.8%** | 20.7% |
| insurer | 123 | 97.6% | 87.8% | 58.5% | 12.2% |

**The collapses coincide with the scorer's existing `sectors.masked_legs`** — `gross_margin`
is masked for financials/REITs/insurers, and `fcf_yield`/`fcf_cagr` are masked for REITs
(9.8% here). So a DERA pre-rank abstains in the same places the scorer already does, rather
than introducing a new and different blind spot. That consistency is worth more than the
headline percentages.

`unknown` is **83% of the universe** — SIC bucketing only classifies financials/REITs/
insurers, and `unknown` is deliberately an unmasked no-op — so the pre-rank operates normally
on the large majority.

## 5. Three limits that constrain any design

1. **Coverage is not signal, and the ranking here is a trap.** The best-covered derived input
   — the accruals triple at 93.9% — is a leg this repo **already measured and DISABLED**
   (`docs/audits/2026-07-12-accruals-leg-disable.md`: positive-signed but sub-significant on
   largecap, flat-to-negative on the 231-name combined run, never clears t≈2 on any
   reproducible universe). Coverage says what we *can* compute, never what is worth
   computing. Do not let availability drive leg selection.
2. **DERA has no market cap.** `shares_out` is present (80.8%) but price is not, so any
   value- or size-aware axis inherits a price dependency (Yahoo/Finnhub/paid) and the
   availability constraints that come with it. A pre-rank built purely on DERA is a
   *fundamental-quality* rank, not a valuation rank.
3. **Only `2026q1` was measured.** Schema and tag-usage stability across the 2009Q2→present
   archive is assumed, not verified — and tag conventions demonstrably drift (the
   `standard_concept` drift that broke the accruals leg is the standing example).

## 6. Recommendation

A DERA-backed standing pre-rank is **feasible**, built on ROE + growth + an accruals-style
quality input, abstaining on gross margin, with sector abstention already consistent with the
scorer.

**But nothing should be built yet.** Per the plan, a standing screen changes which names
surface, so it is a scoring-surface change requiring a **committed pre-registration before it
can influence the digest**. The next step is that pre-registration — stating the ranking
inputs, the expected sign, the horizon and the kill rule — and *then* the build, so the
verdict is honestly pre-registered rather than fitted to whatever this quarter's data
happened to support.

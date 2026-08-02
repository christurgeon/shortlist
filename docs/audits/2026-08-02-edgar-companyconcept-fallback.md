# EDGAR `diluted_shares` companyconcept fallback — full-universe live verification

**Date:** 2026-08-02 · **Change:** `fix/edgar-companyconcept-fallback` (Task 1 `a77baf9` pure
aggregator, Task 2 `e04a554` wiring) — recovers `diluted_shares` for 8 of the 9 "root cause B"
issuers left by `#156` (`docs/audits/2026-07-31-edgar-concept-match.md`) by falling back to
SEC's single-tag `companyconcept` API when the 10-K statement view has no share-count row at
any label. Design: `docs/PLAN_EDGAR_ROOT_CAUSE_B.md`.

**Verdict: GO.** All five go/no-go clauses pass. XOM stays `[]`; every other ticker is
byte-identical. Production coverage moves **33/42 → 41/42** on `diluted_shares`.

## Size this honestly (unchanged from the plan)

This is **hygiene / harness-vs-backtest path parity, not an edge improvement**. All 8 recovered
series are *shrinking* share counts (2y CAGR, see the cross-check table below) — none within 6
points of `flags.dilution.min_share_cagr` (+0.03) — and `quality.dilution` is OFF, so
`scoring.py` never reads the field. `pe_ttm`/`pe_median_5y` read `diluted_eps` only, untouched.
**Net effect: a JSON/CSV field goes `null` → number for 8 tickers. No score, gate, flag,
ranking or selection changes.**

The real justification is path parity: `_xbrl_facts.py:132` already reads
`WeightedAverageNumberOfDilutedSharesOutstanding` from companyfacts for the XBRL backtest
panel, so the backtest measurement of `share_count` already covered all 8 of these issuers —
only the production harness was skewed. Closing this residual does **not** de-bias the
evidence for `quality.dilution` (the largecap XBRL run already measured that axis weak); it
narrows the prior "non-random skew, 9 tickers" objection to **1 ticker (XOM)**. **This is not a
step toward enabling `quality.dilution`.**

## Probe table (companyconcept coverage, live 2026-08-02)

`companyconcept/CIK{cik:010d}/us-gaap/WeightedAverageNumberOfDilutedSharesOutstanding.json`:

| ticker | present? | latest 3 annual values (FY0/FY1/FY2) |
|---|---|---|
| CMCSA | present | 3.709B / 3.908B / 4.148B |
| CVX | present | 1.856B / 1.817B / 1.880B |
| GOOGL | present | 12.230B / 12.447B / 12.722B |
| HON | present | 0.6428B / 0.6553B / 0.6682B |
| LMT | present | 233.5M / 239.2M / 251.2M |
| MO | present | 1.683B / 1.718B / 1.777B |
| MRK | present | 2.507B / 2.541B / 2.547B |
| PG | present | 2.4544B / 2.4719B / 2.4839B |
| **XOM** | **absent** | **genuinely absent — see below** |

Every value matches the plan's own probe table exactly (re-derived live here, not copied).

## XOM — exhaustive-absence evidence, live-verified 2026-08-02

`Company("XOM").cik` → **34088** (the operating company; the raw `company_tickers.json`
first-occurrence map instead sends XOM to CIK **2115436**, "ExxonMobil Holdings Corp," a
1,061-byte fee-filing shell — do not use that map for this lookup).

- `companyconcept/.../WeightedAverageNumberOfDilutedSharesOutstanding.json` → **200 OK**, 31
  rows total, 3 of them `form == "10-K"`. **Latest `end` across ALL rows (any form): 2013-12-31.**
  XOM has not tagged this concept since FY2013.
- Enumerated **every** `us-gaap` tag in XOM's full `companyfacts` payload (3.12 MB) whose
  `units.shares` carries a `form == "10-K"` row with `end >= 2024-01-01` — **11 tags**:

  | tag | latest `end` | latest value |
  |---|---|---|
  | CommonStockSharesAuthorized | 2025-12-31 | 9,000,000,000 |
  | CommonStockSharesIssued | 2025-12-31 | 8,019,000,000 |
  | CommonStockSharesOutstanding | 2025-12-31 | 4,179,000,000 |
  | ShareBasedCompensation…OtherThanOptionsForfeitedInPeriod | 2025-12-31 | 552,000 |
  | ShareBasedCompensation…OtherThanOptionsGrantsInPeriod | 2025-12-31 | 10,327,000 |
  | ShareBasedCompensation…OtherThanOptionsVestedInPeriod | 2025-12-31 | 8,716,000 |
  | StockIssuedDuringPeriodSharesAcquisitions | 2025-12-31 | 0 |
  | StockIssuedDuringPeriodSharesTreasuryStockReissued | 2025-12-31 | 8,000,000 |
  | TreasuryStockCommonShares | 2025-12-31 | 3,840,000,000 |
  | TreasuryStockSharesAcquired | 2025-12-31 | 182,000,000 |
  | **WeightedAverageNumberOfSharesOutstandingBasic** | 2025-12-31 | **4,305,000,000** |

  The **only** weighted-average share-count tag left is the **basic** count. **Not
  substituted** — a diluted count and a basic count are different measures, and conflating
  them would silently corrupt `share_count_cagr`. XOM correctly stays `diluted_shares = []`;
  this is the designed abstention, not a defect to chase.

## Cost measurement (live 2026-08-02, corroborating the plan's 108–140×)

| ticker | `companyconcept` (1 tag) | `companyfacts` (all tags) | ratio |
|---|---|---|---|
| PG | 34.5 KB | 3.63 MB | 108× |
| HON | 31.7 KB | 4.35 MB | 140× |

(The plan's original numbers — 35 KB/32 KB vs 3.81 MB/4.56 MB — were measured on an earlier
date; live payloads grow slightly over time as new filings accrete. The ratio class is
unchanged.) At ~35 KB this fallback fires for at most 9 tickers/run — comparable to any other
EDGAR request the harness already makes.

## Full 42-ticker before/after

Driven through `EdgarSource._build_financials_snapshot` (via `_fetch_financials_object` +
`_build_financials_snapshot`, **not** `extract_financials` directly — the latter never
exercises the new fallback and would report zero diffs across all 42). Universe enumerated
from the real production accumulation store:

```python
tickers = sorted({os.path.basename(os.path.dirname(p))
                  for p in glob.glob("/opt/shortlist/state/snapshots/*/*.json.gz")})
```

→ 42 tickers, matching `docs/PLAN_EDGAR_ROOT_CAUSE_B.md`'s own evidence table.

- **After:** run on `fix/edgar-companyconcept-fallback` @ `e04a554` (this checkout).
- **Before:** the same script run in a temporary worktree at `dccecab` (the signed-off-plan
  commit, immediately pre-Task-1) — `git worktree add /tmp/rcb-before dccecab`, removed
  afterward, never moved HEAD.
- The full `Statements` dataclass (`dataclasses.asdict`) was captured per ticker and diffed
  **programmatically** (dict equality), not just `diluted_shares`.

**8 tickers changed, all changing exactly one field (`diluted_shares`) and nothing else. 34
tickers — including XOM — byte-identical across every `Statements` field.**

| ticker | before `diluted_shares` | after `diluted_shares` (FY0/FY1/FY2) | changed fields |
|---|---|---|---|
| CMCSA | `[]` | `[3709000000.0, 3908000000.0, 4148000000.0]` | `diluted_shares` only |
| CVX | `[]` | `[1856000000.0, 1817000000.0, 1880000000.0]` | `diluted_shares` only |
| GOOGL | `[]` | `[12230000000.0, 12447000000.0, 12722000000.0]` | `diluted_shares` only |
| HON | `[]` | `[642800000.0, 655300000.0, 668200000.0]` | `diluted_shares` only |
| LMT | `[]` | `[233500000.0, 239200000.0, 251200000.0]` | `diluted_shares` only |
| MO | `[]` | `[1683000000.0, 1718000000.0, 1777000000.0]` | `diluted_shares` only |
| MRK | `[]` | `[2507000000.0, 2541000000.0, 2547000000.0]` | `diluted_shares` only |
| PG | `[]` | `[2454400000.0, 2471900000.0, 2483900000.0]` | `diluted_shares` only |
| **XOM** | `[]` | `[]` **unchanged** | none |
| AAPL, ABBV, ADBE, AMD, AMZN, BAC, CAT, COST, CRM, CSCO, DIS, GS, HD, IBM, JNJ, JPM, KO, LLY, MCD, META, MSFT, NFLX, NKE, NVDA, ORCL, PEP, QCOM, TXN, UNH, V, VZ, WFC, WMT (33 tickers) | — | — | **byte-identical** |

## Cross-check: `net_income / diluted_eps ≈ diluted_shares`

**What this proves, honestly:** a percent-level tolerance CANNOT discriminate a diluted count
from a basic one — for these issuers they differ by well under 1%, so this check corroborates
plausibility, it does not guarantee the row is the diluted count rather than some other nearby
tag. The real guarantee is structural (below): the seam requests one named concept URL and the
returned payload's own `cik`/`tag` fields were asserted to echo exactly what was requested, for
all 8 recovered tickers plus XOM.

| ticker | FY | net_income | diluted_eps | implied shares (NI/EPS) | reported `diluted_shares` | deviation |
|---|---|---|---|---|---|---|
| CMCSA | 0 | 19,998,000,000 | 5.39 | 3,710,204,082 | 3,709,000,000 | +0.032% |
| CMCSA | 1 | 16,192,000,000 | 4.14 | 3,911,111,111 | 3,908,000,000 | +0.080% |
| CMCSA | 2 | 15,388,000,000 | 3.71 | 4,147,708,895 | 4,148,000,000 | −0.007% |
| CVX | 0 | 12,299,000,000 | 6.63 | 1,855,052,790 | 1,856,000,000 | −0.051% |
| CVX | 1 | 17,661,000,000 | 9.72 | 1,816,975,309 | 1,817,000,000 | −0.001% |
| CVX | 2 | 21,369,000,000 | 11.36 | 1,881,073,944 | 1,880,000,000 | +0.057% |
| GOOGL | 0 | 132,170,000,000 | 10.81 | 12,226,641,998 | 12,230,000,000 | −0.027% |
| GOOGL | 1 | 100,118,000,000 | 8.04 | 12,452,487,562 | 12,447,000,000 | +0.044% |
| GOOGL | 2 | 73,795,000,000 | 5.80 | 12,723,275,862 | 12,722,000,000 | +0.010% |
| HON | 0 | 4,729,000,000 | 7.36 | 642,527,174 | 642,800,000 | −0.042% |
| HON | 1 | 5,705,000,000 | 8.71 | 654,994,259 | 655,300,000 | −0.047% |
| HON | 2 | 5,658,000,000 | 8.47 | 668,004,723 | 668,200,000 | −0.029% |
| LMT | 0 | 5,017,000,000 | 21.49 | 233,457,422 | 233,500,000 | −0.018% |
| LMT | 1 | 5,336,000,000 | 22.31 | 239,175,258 | 239,200,000 | −0.010% |
| LMT | 2 | 6,920,000,000 | 27.55 | 251,179,673 | 251,200,000 | −0.008% |
| MO | 0 | 6,947,000,000 | 4.12 | 1,686,165,049 | 1,683,000,000 | +0.188% |
| MO | 1 | 11,264,000,000 | 6.54 | 1,722,324,159 | 1,718,000,000 | +0.252% |
| MO | 2 | 8,130,000,000 | 4.57 | 1,778,993,435 | 1,777,000,000 | +0.112% |
| MRK | 0 | 18,254,000,000 | 7.28 | 2,507,417,582 | 2,507,000,000 | +0.017% |
| MRK | 1 | 17,117,000,000 | 6.74 | 2,539,614,243 | 2,541,000,000 | −0.055% |
| MRK | 2 | 365,000,000 | 0.14 | 2,607,142,857 | 2,547,000,000 | **+2.361%** |
| PG | 0 | 15,974,000,000 | 6.51 | 2,453,763,441 | 2,454,400,000 | −0.026% |
| PG | 1 | 14,879,000,000 | 6.02 | 2,471,594,684 | 2,471,900,000 | −0.012% |
| PG | 2 | 14,653,000,000 | 5.90 | 2,483,559,322 | 2,483,900,000 | −0.014% |

Max deviation **+2.361%** (MRK FY2 — net income collapsed to $365M that year, so the
NI/EPS ratio is far more sensitive to rounding of `diluted_eps` to 2dp; still well inside the
5% flag threshold). All 24 data points (8 tickers × 3 years) are within tolerance; none flags.

**Structural check (the guarantee the arithmetic above cannot give):** for all 8 recovered
tickers plus XOM, the raw companyconcept payload's own `cik` and `tag` fields were fetched and
asserted to echo exactly the CIK resolved from `Company(ticker).cik` and the requested tag
(`WeightedAverageNumberOfDilutedSharesOutstanding`):

| ticker | resolved CIK | payload `cik` | payload `tag` | match |
|---|---|---|---|---|
| CMCSA | 1166691 | 1166691 | WeightedAverageNumberOfDilutedSharesOutstanding | yes |
| CVX | 93410 | 93410 | WeightedAverageNumberOfDilutedSharesOutstanding | yes |
| GOOGL | 1652044 | 1652044 | WeightedAverageNumberOfDilutedSharesOutstanding | yes |
| HON | 773840 | 773840 | WeightedAverageNumberOfDilutedSharesOutstanding | yes |
| LMT | 936468 | 936468 | WeightedAverageNumberOfDilutedSharesOutstanding | yes |
| MO | 764180 | 764180 | WeightedAverageNumberOfDilutedSharesOutstanding | yes |
| MRK | 310158 | 310158 | WeightedAverageNumberOfDilutedSharesOutstanding | yes |
| PG | 80424 | 80424 | WeightedAverageNumberOfDilutedSharesOutstanding | yes |
| XOM | 34088 | 34088 | WeightedAverageNumberOfDilutedSharesOutstanding | yes (payload present but no 10-K row covers a 2023–2025 spine year, so the aggregator correctly abstains) |

## `flags.dilution.min_share_cagr` (0.03) — checked, not assumed

2-year `share_count_cagr` from the recovered `diluted_shares` series (`(FY0/FY2)^(1/2) − 1`,
matching `stats.cagr`'s convention — newest-first input, reversed internally):

| ticker | 2y `share_count_cagr` | crosses +0.03? |
|---|---|---|
| CMCSA | −5.440% | no |
| CVX | −0.640% | no |
| GOOGL | −1.953% | no |
| HON | −1.919% | no |
| LMT | −3.587% | no |
| MO | −2.681% | no |
| MRK | −0.788% | no |
| PG | −0.596% | no |

All 8 match the plan's own declared 2y CAGRs (CMCSA −5.5%, CVX −0.6%, GOOGL −1.9%, HON −1.9%,
LMT −3.6%, MO −2.7%, MRK −0.8%, PG −0.6%) within rounding. **None crosses the +0.03 flag
threshold** — all are shrinking (buybacks), so `flags.dilution` never fires on this change even
if it were enabled (it isn't; `quality.dilution` is OFF too).

## Accumulation-store discontinuity

From the deploy date forward, `store.py` daily snapshots for these 8 tickers (CMCSA CVX GOOGL
HON LMT MO MRK PG) will carry a populated `diluted_shares`, while the ~24 prior accumulated
dates carry `[]`. This is a **mid-panel field-presence break concentrated in 8 large caps** —
exactly the kind of non-random presence change that biases a walk-forward fit if a future
snapshot-replay backtest axis reads `diluted_shares`/`share_count_cagr` across the full stored
history without accounting for the break.

**Deploy date: not yet deployed as of this audit** (`/opt/shortlist` is at `f0dd2cd`, i.e.
`#156` only — this branch has not been merged/deployed). **A future evaluator must fill in the
actual deploy date here** once `deploy/install_opt_shortlist.sh` has been run for this branch,
so the break is dateable in the accumulation store.

## Go/no-go — all five clauses

1. **The 8 covered tickers go `diluted_shares = []` → 3 real values matching the plan's probe
   table.** **PASS.** CMCSA/CVX/GOOGL/HON/LMT/MO/MRK/PG all match exactly (probe table above).
2. **XOM stays `[]`** (the kill-switch). **PASS.** XOM byte-identical before/after; the
   companyconcept payload for XOM is genuinely present but its newest 10-K row (`end`) is
   2013-12-31, so no 2023–2025 spine year is covered and the aggregator's all-or-nothing
   contract correctly returns `[]`. No basic-share substitution occurred (verified: XOM's
   `diluted_shares` field, not any other, is what stayed empty).
3. **Every other ticker byte-identical.** **PASS.** 33 non-8/non-XOM tickers diff to nothing
   across the full `Statements` dataclass; the 8 changed tickers each diff in exactly one
   field (`diluted_shares`).
4. **Cross-check + structural guarantee.** **PASS.** All 24 NI/EPS-implied-shares data points
   deviate ≤2.361% (well under the 5% flag threshold — max deviation on MRK's low-net-income
   FY2, not a units/wrong-tag miss); this is corroboration only. The **structural** check —
   the payload's own `cik`/`tag` fields echoing what was requested — passed for all 9 tickers
   probed (8 recovered + XOM), which is the actual guarantee that the value came from the
   named concept URL.
5. **`flags.dilution.min_share_cagr` (0.03) crossing.** **PASS/confirmed.** None of the 8
   crosses it — all 8 are shrinking share counts, magnitudes −0.596% to −5.440%.

## Repro

```bash
cd /home/chris/shortlist
set -a && . ./.env && set +a

# after (this branch)
uv run --extra edgar python <driver-script-driving-EdgarSource._build_financials_snapshot> \
    > after.json

# before (base commit, temp worktree, HEAD never moves)
git worktree add /tmp/rcb-before dccecab
cd /tmp/rcb-before
set -a && . /home/chris/shortlist/.env && set +a
uv run --extra edgar python <same-driver-script> > before.json
cd /home/chris/shortlist
git worktree remove /tmp/rcb-before

python3 -c "import json; b=json.load(open('before.json')); a=json.load(open('after.json')); \
print(sorted(k for k in a if a[k] != b[k]))"
```

The driver script constructs `EdgarSource(config={})`, then per ticker calls
`src._fetch_financials_object(ticker)` → `src._build_financials_snapshot(ticker, fin)` and
serializes `dataclasses.asdict(snap.statements)` — **not** `extract_financials` directly, which
would never exercise the fallback.

CI: `uv run ruff check src tests` clean; `uv run pytest -q` → **2260 passed, 6 skipped, 19
deselected** (`tests/test_edgar_leverage_live.py`, `-m live`, not run — hits SEC).

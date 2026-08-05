# Standing-screen data source — DERA is too stale; SEC `frames` is the live answer (2026-08-05)

**Purpose (plan Phase 2):** finish choosing the standing screen's data source. The DERA spike
(`2026-08-05-standing-screen-spike-dera.md`) picked DERA over FDIC on universe size,
crosswalk quality and point-in-time cleanliness. One measurement then invalidated it as a
**live** source, and a better live source was found.

**Outcome: DERA and SEC `frames` are COMPLEMENTARY, not competing.**
`frames` for live emission, DERA for backtesting. Neither replaces the other.

---

## 1. The measurement that killed DERA-as-originator

`2026q1.zip` is the **newest published** financial-statement quarter (`2026q2` and `2026q3`
both return 404). Its 10-K/10-Q rows were **filed 2026-01-02 … 2026-03-31**. Measured against
today, 2026-08-05:

| | filed | age today |
|---|---|---|
| newest filing in the file | 2026-03-31 | **127 days** |
| median filing | 2026-02-27 | **159 days** |
| oldest filing | 2026-01-02 | 215 days |

Median fiscal **period end** is 217 days old.

The information itself was public on the EDGAR filing date months earlier — DERA only
re-publishes it in bulk. So a "signal" derived from DERA bulk is not early; it is a
convenient format arriving a couple of quarters late. **DERA bulk cannot be a live
originator.**

## 2. SEC `frames` — full universe, current, ~12 requests

`https://data.sec.gov/api/xbrl/frames/us-gaap/{TAG}/USD/{FRAME}.json` returns **one concept
across every filer for one period, in a single request.** Measured:

| tag / frame | filers | size | time |
|---|---|---|---|
| `Assets` / CY2026Q1I | 5,498 | 0.7 MB | 0.5 s |
| `Assets` / CY2025Q4I | 6,027 | 0.8 MB | 0.6 s |
| `NetIncomeLoss` / CY2025 | 5,570 | 0.9 MB | 0.2 s |
| `StockholdersEquity` / CY2026Q1I | 5,116 | 0.7 MB | 0.3 s |

**Freshness:** `Assets` CY2026**Q2**I already returns **1,807 filers** on 2026-08-05 — the
natural Q2 filing curve, arriving in near-real-time. That is roughly two quarters ahead of
DERA's newest.

**The tag family is load-bearing** (as `providers/_xbrl_facts.py` already encodes). For
CY2025 revenue: `RevenueFromContractWithCustomerExcludingAssessedTax` 2,663,
`Revenues` 2,191, `...IncludingAssessedTax` 643, `SalesRevenueNet` 0 — but the **union is
4,605 distinct filers.** Querying one tag would silently lose half the universe.

### Cost comparison

| source | requests | bytes | staleness |
|---|---|---|---|
| DERA bulk | 1 | 85 MB | **127–215 days** |
| per-ticker companyfacts | ~4,620 | ~3.8 GB | current |
| **SEC `frames`** | **~12** | **~8 MB** | **current** |

## 3. The division of labour — and why it is principled, not a fudge

**`frames` has no `filed` date.** Its fields are `accn, cik, end, entityName, loc, val`. It
returns the **current best value** for a period, so a later restatement silently overwrites
what was knowable at the time. That is precisely the "silently restated" look-ahead trap.

- **Live emission → `frames`.** Current data, and "what is true now" is exactly right for
  deciding what to look at today.
- **Backtesting → DERA.** `filed` on 100% of rows, delisted filers retained, archive to
  2009Q2. Staleness is irrelevant for history; point-in-time correctness is everything.

Using different sources for live and backfill is a **known hazard** in this repo (the 13D/A
live-vs-backfill population caveat). Here it is deliberate and the mismatch is *bounded*:
both surface the same underlying XBRL facts, and DERA is the point-in-time-correct rendering
of what `frames` shows today. **Any future backfill must state this explicitly**, and must not
quietly use `frames` for history — that would import restatement look-ahead into every
verdict.

## 4. Still true, and still limiting

- **No market cap in either source.** `shares_out` exists; price does not. Any value- or
  size-aware axis inherits a price dependency, and the cap-band composition work depends on it.
- **Coverage is not signal.** Unchanged from the tag-coverage audit: the best-covered derived
  input (the accruals triple, 93.9%) is a leg already measured and DISABLED on 2026-07-12.
  Availability must not drive leg selection.
- **Nothing is built and nothing is pre-registered.** A standing screen changes which names
  surface, so it remains a scoring-surface change requiring a committed pre-registration
  before it can influence the digest.

## 5. What this changes about the plan

Phase 2's substrate question is now answered on evidence:

1. **Live standing screen → SEC `frames`** (~12 requests, ~8 MB, current, whole universe).
2. **Measurement/backfill substrate → DERA** (PiT, survivorship-free, 2009Q2→present).
3. **FDIC → not adopted** (banks-only; every quiet-day digest would be banks, in the sector
   where the scorer masks the most legs).
4. **Per-ticker companyfacts → not needed** for this purpose. `frames` obtains the same
   currency at ~0.3% of the request count and ~0.2% of the disk.

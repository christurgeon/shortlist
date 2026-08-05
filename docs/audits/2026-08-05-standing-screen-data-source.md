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

## 5. Adversarial review — two corrections to this document, and a reframing

An independent adversarial review of §1–§4 landed four hits. Two are **errors in what was
already committed** and are corrected here rather than quietly edited.

### 5a. CORRECTION — "survivorship-free" was wrong as applied

§3 and the DERA spike called DERA "survivorship-free". **The archive is; the crosswalk used
to measure it was not.** `dera_spike.py` resolved CIK→ticker with
`cik_tickers.build_cik_to_ticker` against **today's** `company_tickers.json`. A company that
delisted in 2016 is retained in the historical ZIP but has since dropped out of that file, so
it silently fails to resolve — **re-excluding exactly the failures a point-in-time backtest
exists to capture.**

The 88.3% / 4,620-ticker figures are therefore "currently-listed filers", not "filers as of
the period". For 2026q1 the distortion is small (recent delistings are few); **for historical
quarters it is the whole problem.**

The repo already has the right tool: **`scout/symbology.py:Symbology.resolve_ticker(cik,
as_of)`**, the point-in-time resolver `backfill.py:784` already uses for the 8-K/buyback
cohorts. Any DERA backfill MUST use it. A current-day join is a survivorship bug wearing a
point-in-time costume.

### 5b. CORRECTION — restatement leakage across quarters

§3 justified DERA-for-backtest on `filed` being present. That is necessary but **not
sufficient**: the same fiscal period reappears as a *comparative* in every subsequent
quarterly ZIP, and later appearances can carry **restated** values. A backtest that joins
`num.txt` across quarters can therefore pick up a figure that did not exist at the evaluation
point — the as-originally-reported vs. most-recently-available trap.

**Rule: pin every fact to the filing whose own `filed` date matches the evaluation point.
Never take a period's value from a later quarter's comparative column.**

### 5c. REFRAMING — §1's stated reason was the wrong one

§1 rejected DERA-as-originator because the information is "long since priced". For a
**triage funnel that explicitly is not return-predicting**, that is the wrong test — triage
does not require unpriced information, and 127 days barely matters for ranking slow-moving
business *quality*. The conclusion survives, but on two better grounds:

1. **It is strictly worse than an equally-available alternative.** `frames` is current and
   costs ~12 requests (§2). The choice was never "stale data vs. nothing".
2. **A quarterly source has no daily refresh cadence.** A standing screen fed by a quarterly
   snapshot emits the *same* ranked list for ~13 weeks. That does not fix "a quiet day
   produced nothing new" — it produces a static work queue, not discovery. This is the
   sharper objection and §1 missed it entirely.

### 5d. The review's strongest point — build it as a FILTER, not an originator

A full-universe fundamental *ranking* is "the existing quality/value composite run at
S&P-1500 scale" — precisely the add-scoring-surface move this repo has already killed four
times (buyback, leverage tilt, accruals, EV/EBIT). Used **defensively** it is not: the
binding constraint is that only ~10 names/day can be deep-screened, so the highest-value
question is *which already-surfaced event candidates deserve those slots*.

## 6. What this changes about the plan

Phase 2's substrate question is now answered on evidence:

1. **Do NOT build a standing full-universe originator.** §5d: as a ranking it is added
   scoring surface; the repo has killed that move four times. The empty-day symptom does not
   justify it, and a quarterly source cannot fix a *daily* gap anyway (§5c.2).
2. **Build it as a FILTER on existing originators' output instead.** The binding constraint
   is the ~10/day deep-screen quota. The valuable question is *which already-surfaced 13D /
   Form 4 / 13F / 8-K candidates deserve those slots* — especially in the $0.3–10B band the
   composition audit says we miss.
3. **Measure that on DERA before building anything live** — free, offline, no price feed for
   the selection step, and it reuses the existing evaluator + pre-registration machinery.
   Subject to **§5a (PiT `Symbology`, never a current-day join)** and **§5b (pin facts to the
   matching filing, never a later comparative)**.
4. **Only if it clears a pre-registered bar**, make it live on `frames` (~12 requests,
   current) — as a filter, refreshed quarterly, never a tenth originator.
5. **FDIC → not adopted.** **Per-ticker companyfacts → not needed**; `frames` gets the same
   currency at ~0.3% of the requests and ~0.2% of the disk.

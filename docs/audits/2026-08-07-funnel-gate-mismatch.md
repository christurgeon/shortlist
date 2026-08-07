# The discovery layer finds names the scorer is configured to reject (2026-08-07)

**What this is:** evidence, measured against the live 196-pick selection ledger
(`/opt/shortlist/state/scout_state.json`), the six committed backfill cohorts, and the 28
production run manifests. It documents one structural finding that is **not being acted on**,
and two small fixes that **are**.

**Why it exists:** while executing Track A of
`docs/audits/2026-08-06-discovery-breadth-plan.md`, a proposed change — a market-cap
pre-filter at the funnel stage — was measured before building and turned out to be actively
harmful. The measurement is worth more than the feature would have been.

---

## 1. The finding: an $2B gate in front of a $50M funnel

`config.yaml:105` sets `gates.min_market_cap: 2.0e+9`. `config.yaml:526` records the design
intent: *"market-cap floor is enforced by the existing scoring gate."* So the floor fires
**after** the deep screen, as `below_min_mktcap` in `scoring.py:627`.

The currently-enabled originators fish nowhere near it.

| originator | picks | median market cap | below the $2B gate | gated |
|---|---|---|---|---|
| `edgar:activist_13d` | 54 | **$50M** | 50 / 52 known | **52 / 54** |
| `edgar:form4_cluster_buy` | 23 | $307M | 14 / 19 known | 14 |
| `edgar:form4_insider_buy` | 17 | $652M | 9 / 17 | 11 |
| `edgar:13f_new_position` | 6 | $258,332M | 0 / 5 | 1 |
| *(retired)* `wsb:hype` | 96 | $277,548M | 3 / 91 | 21 |

Across the three **currently-enabled** originators (n=100): median market cap **$196M**,
**78% of cap-known picks below the $2B gate**, and a further 7% carrying no market cap at all
(where the gate cannot evaluate, so the name passes unchecked).

**~80% of deep-screen slots are spent on names the committed gate is configured to reject.**

This is not a defect in either component. It is a **mismatch**: activism and insider buying
*structurally* happen at small caps — that is what the literature describes and what the
originators are built to find — while the gate encodes a $2B investability floor. Both are
individually defensible. Together they mean the funnel's dominant output cannot reach the
`/deep` actionable block (`report/sections.py` filters it to non-gated scored leaders).

### 1.1 Why the obvious fix is wrong

The natural response is a market-cap pre-filter at the `apply_quality_floor` seam, so a
sub-gate name never consumes a slot. **It was measured before it was built, and it fails.**

Replaying the ledger session-by-session, keeping only picks at or above $2B and abstaining
(keeping) where market cap is unknown:

- **13 of 25 sessions would deliver ZERO candidates.**
- Median survivors per session: **0**. Mean: 1.0.

The pre-filter converts a thin funnel into a mostly-empty one. It does not create actionable
names; it deletes the only names there are.

The two coherent responses are therefore **not** "filter harder":

1. **Lower `gates.min_market_cap`** so the gate matches where the originators actually fish
   (the 2026-07-26 composition audit argues the retail-accessible band is $0.3–10B, roughly
   half of which sits below the current floor). This is a **live scoring-gate change** that
   moves every score, gate and selection — it needs its own evidenced, pre-registered
   decision, not a side-effect of funnel plumbing.
2. **Accept the mismatch** and treat "gated" as the gate doing its job, recognising that the
   enabled originators are aimed at a population this configuration excludes.

**Neither is taken here.** Recording the contradiction is the deliverable; resolving it is a
separate, evidenced decision. Note the third-party review of the plan recommended building
exactly the pre-filter this section rejects — the measurement, not the argument, is what
settled it.

### 1.2 An independent cross-check

The ledger's stored `market_cap` and a fresh keyless pull of the listed universe agree:

- `api.nasdaq.com/api/screener/stocks` (NASDAQ + NYSE + AMEX, 3 requests, HTTP 200 JSON):
  **7,204 symbols**, 5,828 with a usable `marketCap`.
- Of 94 distinct live-originator ledger tickers, **90% are present**, 88% with a usable cap.
- Nasdaq-sourced median for those tickers: **$302M** (ledger-stored median: $196M).
- Would-be pre-screen drops: **67%** of tickers — consistent with the 78%-of-cap-known figure.

Also measured for a later decision: **2,737 universe names (47% of the cap-known universe)
sit in the $0.3–10B band.** The band is well populated; the funnel simply does not aim there.

---

## 2. Preliminary SEC-budget measurement (Track A2 — NOT yet satisfied)

The first production `sec_requests` landed in `/opt/shortlist/scout/2026-08-06/manifest.json`:

| consumer | requests | share |
|---|---|---|
| `edgar_form4` | 741 | **98.0%** |
| `unattributed` | 10 | 1.3% |
| `edgar_activist_13d` | 3 | 0.4% |
| `dera`, `cik_tickers` | 2 | 0.3% |
| **total** | **756** | |

This matches the 97.9% offline simulation and **supports** the inferred 2026-08-04 cascade.

**It does not satisfy Track A2, whose stated bar is ≥3 sessions.** One session is exactly the
kind of single-observation inference this workstream has repeatedly retracted. Recorded as
preliminary.

Two sizing conclusions are nonetheless already safe, because they are *bounds*, not estimates:

- **`edgar_index_daily_cap: 2500` has never bound.** Post-rewrite Form 4 volumes are
  46 / 325 / 346 / 366 / 740 / 928 — peak **37% of the cap**. Lowering it would truncate a
  **structured, not random** prefix (the `[:max_filings]` slice at `edgar_index.py:154`),
  introducing selection bias for zero budget benefit. **Leave at 2500.**
- **The budget is nowhere near binding.** At `DEFAULT_MIN_INTERVAL_S = 0.167` (5.99 req/s)
  within `TimeoutStartSec=1800`, the ceiling is ~10,240 requests/run against an observed peak
  of 756 — **13.5× headroom**. The 2026-08-04 cascade was caused by running *unthrottled*
  (~57 req/s), not by volume; the throttle already fixed it.

**Correction to the plan:** `sec_requests` **cannot** size `daily_x`. Deep-screen EDGAR
fetches go through the harness `EdgarSource`'s own `_EDGAR_MAX_CONCURRENCY` semaphore, which
is outside the shared throttle and therefore uncounted. The two halves of Track A4 need
different instrumentation; only the `edgar_index_daily_cap` half is answerable from this
artifact.

### 2.1 Fixed here: 1.3% of the budget was unattributable

The `unattributed: 10` came from four unlabelled `throttle()` call sites — `thirteenf.py`
(×3: submissions, archive index, infotable) and `cusip_map.py` (×1: FTD zips). All four now
pass a consumer label. `symbology.py`'s `_throttle()` is deliberately **not** labelled: it
paces **archive.org**, a different host with its own budget.

`tests/test_scout_sec_throttle_labels.py` is an **AST scan**, not a runtime assertion,
because the failure is a missing argument at a call site — an unlabelled call still works,
still paces, and still counts. Only reading the source catches it.

---

## 3. Fixed here: mutual funds were reaching the digest as stock picks

### 3.1 The leak

Three open-end mutual funds and one ETF reached the live selection ledger:

| ticker | originator | session | outcome |
|---|---|---|---|
| `FTECX` | `edgar:form4_cluster_buy` | 2026-07-08 | ungated, composite 0.0 |
| `VFLEX` | `edgar:form4_cluster_buy` | 2026-07-08 | ungated, composite 71.1 |
| `BBASX` | `edgar:form4_cluster_buy` | 2026-07-10 | ungated, composite 0.0 |
| `BBASX` | `edgar:form4_cluster_buy` | 2026-07-27 | **ungated, composite 100.0** |
| `GLD` | `edgar:13f_new_position` | 2026-07-14 | ungated, composite 40.0 |

`BBASX` at composite 100.0, ungated, is a mutual fund delivered to the analyst as the
top-ranked stock idea of the night. A mutual fund cannot be the issuer of an insider buy, so
these are **resolver artifacts**, not signals.

**This corrects `2026-08-05-session-log.md` §4b**, which investigated a security-type filter
and declined to build it because *"all 6 non-operating names came from already-dead
originators (`wsb:hype` disabled, `edgar:form4_cluster_buy` retired)."* Both premises are
wrong: `edgar:form4_cluster_buy` is the **pre-rewrite emission name of the still-enabled
`EdgarForm4Signal`** (renamed, not retired, at the 2026-07-27 rebuild), and `GLD` came from
`edgar:13f_new_position`, which is enabled at weight 1.0.

### 3.2 The fix, and why it cannot move a committed verdict

Two changes:

1. **`X` added to `_FIFTH_LETTER_SUFFIXES`** (`short_interest.py`) — Nasdaq's 5th-letter
   marker for an open-end fund. The set previously held `FYWURQ`.
2. **`edgar_form4` now applies `_junk_suffix`** (`insider.py:emissions_from_txns`). It was
   the only EDGAR originator not doing so; 8-K, buyback and 13F always have.

**Verdict-neutrality is proven, not assumed.** `_junk_suffix` is reached from only two
cohort assemblers, and neither cohort contains an X-suffix event:

| cohort | events | X-suffix | applies `_junk_suffix`? |
|---|---|---|---|
| `8k-2022-01-01-2025-12-31` | 1,843 | **0** | yes (`_assemble_8k`) |
| `buyback-2022-01-01-2025-12-31` | 588 | **0** | yes (`_assemble_buyback`) |
| `8k-neg-2022-01-01-2025-12-31` | 11,612 | 0 | no (`negative=True` skips it) |
| `13d-2022-01-01-2025-12-31` | 3,645 | 2 (`CPRDX`, `PMFAX`) | **no** (`_assemble_13d`) |

The two funds in the 13D cohort go through an assembler that never calls the rule, so they
are untouched. **Zero committed cohort events change.** (That those two exist at all is
independent corroboration that the leak is real and not confined to Form 4.)

`GLD` is **not** fixed: a 3-letter ETF symbol carries no suffix marker. Cloning a marquee
fund's new ETF position is not a stock idea, but filtering it needs an instrument this repo
does not have yet. Recorded as open.

---

## 4. Corrections to `2026-08-06-discovery-breadth-plan.md`

Found by adversarial review; all three were errors in that document, not in the code.

1. **`daily.py:667` → `daily.py:663`** for `raw = len(emissions)`.
2. **"08-04 · degraded (4 originators dead)" → 2** (`yahoo_screener`,
   `edgar_activist_13d`). The 4 came from counting every `ran=False` row, which
   double-counts `finnhub_news`/`wikipedia` — enrichment signals whose `ran=False` is a
   *consequence* of `raw=0`, exactly as that same document argues elsewhere.
3. **"three clean sessions" → two.** `yahoo_screener` was still enabled and WAF-failing on
   2026-07-30 and 07-31, so replaying the stored manifests through the real `run_health()`
   returns `degraded` for both. Only **2026-08-05 and 2026-08-06** ran with no originator
   failure — and they delivered **10 and 8** names respectively.

Correction 3 cuts both ways and the second half matters more: the sample supporting "the
funnel is thin" is *smaller* than claimed, but **both genuinely-healthy sessions delivered 8
and 10 names**. The evidence for building a standing sampler to guarantee a non-empty digest
is weaker after this correction, not stronger.

4. **Track B/C omitted a directly adjacent committed decision.**
   `docs/audits/2026-08-05-standing-screen-data-source.md` §6 concludes *"Do NOT build a
   standing full-universe originator… build it as a FILTER on existing originators' output
   instead."* The plan resurrected a standing sampler without citing it. A neutral-sign
   random control draw is arguably a different animal from a ranked full-universe
   originator — but that argument has to be **made**, not skipped. Track B does not start
   until it is.

---

## 5. What changed, and what deliberately did not

**Changed (code):** the `X` suffix; `edgar_form4` applying `_junk_suffix`; four throttle
labels plus an AST test binding them. `scoring.score()` is untouched.

**Deliberately NOT changed:**

- **`gates.min_market_cap`** — §1. A live gate; needs its own evidenced decision.
- **A market-cap pre-filter** — §1.1. Measured; it empties 13 of 25 sessions.
- **`edgar_index_daily_cap`** — §2. Never binds; lowering it introduces selection bias.
- **`scout.quality_floor.enabled`** — proposed, then pulled. The plan gates it on ≥3
  sessions of `sec_requests` and only one exists; and the 5.2% evidence is same-ledger, with
  the false-positive guards (GIPR, COE) found on the very picks the number is computed from,
  scored with LIVE-ONLY `frames` rather than a point-in-time replay. Neither is fatal — both
  mean the flip is a measured decision, not a one-line convenience.

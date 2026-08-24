# `docs/audits/` — dated evidence, and the register of things already settled

Two jobs, and they are different:

1. **The verdict register** below: one line per question this repo has already closed, so a
   later session does not re-derive it. Read this before opening any work item.
2. **An index of the dated notes**, which hold the measurements those verdicts rest on.

`CLAUDE.md` states the rule these files exist to serve: *measure first, kill on evidence,
commit the evidence.* A note here is **evidence as of its date** — cite it for *why* a
decision went the way it did, never as a description of how the code behaves today. For
current behaviour read `docs/SCORING.md`, `docs/RESEARCH.md`, `docs/TELEGRAM.md`,
`docs/EDGAR_CLIENTS.md`, `HARNESS.md` or the code.

A verdict here **outranks a story built from the data**. The 2026-07-26 postmortem retracted
four conclusions because a pre-registered floor everyone assumed was wrong turned out to be
correct. Reopen an entry only with new evidence, and say precisely what is new.

---

## Closed with a verdict — do not redo

- **Options-implied signals: a keyless feed EXISTS, and it belongs in `/deep`, not the
  pre-screen (2026-08-24)** — `docs/ASSESSMENT_GAPS.md` had recorded "no keyless feed known
  (mostly paid); likely noise for a fundamental pre-screen". The first half is **measured
  false**: CBOE's `delayed_quotes` endpoint returns a full chain with per-contract IV and
  greeks, keyless, 80/80 large caps and 79/80 small/mid from oracle-prod. The second half is
  **correct**, and the shipped feature concedes it — implied-vs-realized vol, the implied
  earnings move and 25-delta skew render only as a prompt-only `/deep` line, never on
  `/screen`, never in the composite, gates or flags. **Quote quality, not coverage, is the
  constraint**: the delta guard passes 80/80 large caps but 38/77 small/mid, and adding the
  spread guard leaves 71/80 against 10/77 — so the line abstains on most small caps, which
  independently confirms the "noise" judgment for exactly the population it was aimed at.
  **Do not re-probe the feed and do not propose this as a scored leg.** Three things that
  are settled and cost real measurement: the per-IP rate ceiling (a cookie-less loop dies at
  60 requests, so this can never be a universe scan), the vendor earnings calendar's
  reliability (present on 42/42 snapshots but revised 14 times in ~2 months, max 8 days —
  though never with under 12 days to go), and 8-K Item 2.02 as the authoritative announcement
  date (+0d on AAPL/GOOGL/MSFT). **Two priors were measured false and must not be restored**:
  a 30-day realized-vol denominator is *worse* than the shipped 252-day one (earnings-cycle
  contaminated), and the textbook variance-risk-premium caveat does not hold here — 60 of 80
  large caps price implied UNDER realized. Still open: IV rank/percentile, which needs a time
  series this feed cannot supply. `2026-08-24-options-surface-design.md`.
- **A material-weakness KEYWORD search is worthless; the adverse CONCLUSION plus a tense
  test works (2026-08-23)** — `"material weakness"` matched 226 of 228 filers (auditor
  boilerplate), and `"material weaknesses in our internal control over financial reporting"`
  matches NEGATIONS ("there were no material weaknesses" — SPGI, HMN). What discriminates is
  management's adverse conclusion ANCHORED to the filing's own period end: the dominant false
  positive is a prior-period weakness, since remediated, restated in a later filing (JJSF's
  FY2025 10-K carries a 2024-09-28 conclusion). Shipped as `research/controls.py` on the
  `/deep` path: 16/0/0 tp/fp/fn in-sample over 68 filings, and on 120 HELD-OUT names 16 of 16
  flagged filings were genuine with no missed positive found. Base rate 5.3% of large/small-mid
  caps, 10.0% at $300M-$5B. `window_chars`/`tolerance_days` are SLACK, not tuned — the verdict
  is flat over 100-800 chars and 7-200 days. **The text must come from the whole document**:
  `FilingText.combined()` alone fires on 2 of 7 known positives and edgartools'
  `part_ii_item_9a` returns 0 chars for 3 of 15 filers. That costs **2 extra sec.gov requests
  per brief**, not zero. NOT a ScoreCard flag (score() runs before research) and 10-K only —
  the 10-Q base rate is unmeasured.
  `2026-08-23-icfr-adverse-conclusion-detection.md`.
- **Widening `edgar_events.forms` is free, and low-yield (2026-08-23)** — going from 8 forms to
  18 made **0 additional HTTP requests** (edgartools filters an already-loaded submissions
  index). But over 228 names in a YEAR: NT 10-K/NT 10-Q fired **0.0%**, 8-K 4.02 0.4%, 3.01
  1.3%, 4.01 1.8%; only shelf forms (18-20%) and comment letters (~7%) are common. These are
  insurance bought at zero price, not features — do not re-litigate their yield.
  **Form 25/25-NSE are excluded on purpose**: 17 of 228 filed one within a year, essentially
  all for a matured note or warrant rather than the issuer. Separately,
  `edgar_events.index_limit: 40` was ALREADY binding for BLK (271 matched filings in 90d,
  against a p99 of 29) because the slice is taken newest-first BEFORE the lookback filter;
  raised to 120. `2026-08-23-icfr-adverse-conclusion-detection.md`.
- **The `/deep` arithmetic clause works, and its three 2026-08-18 follow-ups are closed
  (2026-08-22)** — measured 1 of 4 on names with a live refinancing question, diagnosed as a
  clause with no trigger and no destination field, fixed, re-measured **3 of 3** on a
  prompt-only A/B against byte-identical filing inputs. The 12-vs-18-month window defect from
  `2026-08-20-debt-liquidity-notes-design.md` §7.2 is closed with it, and
  `what_would_change_my_mind` saturation went 6/6/6 → 4/4/4. Zero derived figures reached a
  quote-verified field. **Known side effect, accepted:** the clause now fires *universally* —
  the AAPL control computed 12x coverage nobody needed and pushed `reconciliation` to its cap
  of 6. A correct low-value entry beats the silent failure it replaced, so this is an accepted
  tradeoff rather than an unexamined one; the open refinement is tracked at `TODO.md` §2a.
  **D6 quote reuse holds at ~1 per 3 briefs and was
  deliberately left alone** — do not add a third prompt clause for it without new evidence; the remaining rate
  is an order of magnitude below the pre-change 62/35 and another clause would have confounded
  this measurement. `2026-08-22-deep-arithmetic-clause-verification.md`.
- **Trailing-punctuation quote-verification false negatives are NOT fixed, on purpose
  (2026-08-22)** — a CCL quote matched 171 of 172 chars, failing only because the filing is
  dialogue (`2026," Weinstein said`) where the model wrote a sentence period. Trimming trailing
  terminal punctuation from the needle would be safe (it cannot bridge non-adjacent spans), but
  `_FOLD` was adopted on a measured 73% recovery and this is **n=1**. The interior case (a
  footnote marker bled inline, `EBITDA 1 ratio`) is the already-recorded extraction-artifact
  class, `2026-08-04-deep-brief-assessment.md` D1. Reopen only with a recurrence.
- **A per-brief self-consistency detector is NOT worth building (2026-08-22)** — measured
  2 of 17 briefs, and **0 of 9** under the current 2026-08-18 materiality prompt, against a
  pre-committed "~1 in 17 means a prompt clause, not a second call" rule. Both hits are
  pre-prompt and both sit on the **reconciliation** section, not the bull/bear boundary the
  motivating HDSN case implied — the shorter post-prompt red-flag/risk lists appear to have
  removed the marginal assertions that used to collide. `0/9` is NOT "solved": rule-of-three
  upper bound ~33%, effective n≈4-5 (7 of 9 are repeat runs on 3 tickers), and no HDSN brief
  was ever persisted, so the one known positive is excluded by construction.
  `2026-08-22-brief-self-consistency-base-rate.md`.
- **A null `market_cap` does not trip the size gate, and that stays (2026-08-21)** — all four
  gates in `check_gates` fail OPEN on a missing input, the gate-side form of "a missing
  sub-score is excluded, never zeroed". `/screen` runs on a ticker the user chose, so dropping
  it because the cap did not fetch is the worse failure. Rationale is at the gate itself
  (`scoring.py`); do not re-raise without new evidence.
- **`edgar/index.py`'s uncalled collectors are kept on purpose (2026-08-21)** — the scout
  retirement stopped the repo ACTING on 13D/Form-4 signals, it did not disprove the collectors.
  They parse an external format that drifts and their tests are what would tell a reviver
  whether the parsing still holds. Rationale is in the module docstring.
- **`bridge._close_near` now bounds the pairing gap at 45 days (2026-08-21)** — monthly
  sampling means the nearest legitimate close is <=~31 days from any target inside the
  history, so a wider gap means the fiscal end lies outside the sampled span. Unbounded, a
  short history paired a fiscal end with a close up to two years late and inflated the
  `pe_median_5y` anchor (measured 25.0 vs a true 20.0 on the regression fixture);
  `pe_vs_history` is a scored value leg. Pinned by `tests/test_bridge.py`. **No live value
  moves:** checked against a live collect on AAPL/MSFT/KO/NKE/DIS, where Yahoo returns a
  61-month history and every fiscal end sits well inside it — bounded and unbounded agree to
  the last digit on all five. The bound guards the degraded-history case only.
- **`median_pe` keeping NEGATIVE annual P/Es is deliberate, not a defect** — its docstring
  makes it the single source of truth for both the screener FMP provider and the harness
  FMP source. The `pe_ttm` fallback accepting negative EPS is harmless downstream:
  `pe_vs_history()` guards `> 0` and `pe_ttm` is not in `--json`. Do not add a sign guard
  without changing both call sites deliberately.
- **The EDGAR `operating_income` gap is diagnosed and NOT worth fixing (2026-08-21)** —
  presence tracks whether FMP won the merge (100% of fmp-won snapshots vs 62.8% EDGAR-only);
  the intra-ticker flips are FMP quota events, not edgartools drift or non-determinism; and
  the affected filers do not tag `us-gaap_OperatingIncomeLoss` **at all**, so the
  raw-concept-first remedy recovers nothing. Ceiling for any EDGAR-side fix is 62.8% → 67.4%,
  not ~97%. `2026-08-21-operating-income-edgar-gap.md`.
- **The autonomous scout is retired (2026-08-11)** — every originator that reached the
  evaluator came back INSUFFICIENT or KILL, the apparatus that could settle the rest was
  blocked on a paid price feed, and the stack was 47% of source LOC and 59% of tests.
  Decision, evidence, the archived 203-pick ledger and the seven committed pre-registration
  YAMLs: `2026-08-11-scout-retirement.md`. **Do not rebuild an originator without new
  evidence** — these verdicts are measured and committed.
- **A weight/confidence threshold cannot express "a composite must rest on a real leg."** A
  momentum-only name sits at confidence ~0.08 and is pinned as scored; the risk-tilt-only case
  sits at 0.0. The shipped rule is a **component count**
  (`validity.min_composite_components`), and a floor of 0.20 was tried first and correctly
  rejected by `test_scoring_abstention.py`.
- **Five "obvious" refactors were measured and deliberately rejected** (PR #145): merging
  `assemble_eightk_events`/`assemble_buyback_events`, splitting `bridge.snapshot_to_metrics`
  (order-dependent pipeline), splitting `extract_financials`/`panel_to_metrics` (transcription
  risk in numerics), genericizing the four `_load` double-checked locks, and extracting the
  `GovContractsSource`/`LobbyingSource` pagination loops. Revisit only with a specific reason
  *and* a measurement plan. `edgartools` `standard_concept` alias lists stay untouched
  (version-sensitive; they have broken accruals before).
- **WSB:** a per-ticker mention-ratio baseline and a market-cap ceiling were both measured and
  killed — do not rebuild either (`2026-08-07-wsb-novelty-rule.md`).
- **A market-cap pre-filter in the funnel** deleted the only names there were (13 of 25
  sessions → zero candidates). Resolved instead by lowering `gates.min_market_cap` to $300M.
- **Cohort levels are structurally unmeasurable on free data** (outcome-correlated attrition;
  22% of events have no price series, monotonic in age). Do **not** build the ABK/value-weighting
  correction; never quote a RAW-cohort alpha. No data purchase indicated.
- **Do not give an EDGAR client its own `SecThrottle`** — a per-client throttle cannot bound the
  process's request rate, which is exactly how the 2026-08-04 cascade happened. Concurrency buys
  nothing here (~17 ms latency; one serial worker already sustains ~57 req/s).
- **Python is pinned to 3.12 (`.python-version`) to match production**, NOT to dodge a test
  failure — that bullet's `test_block_bootstrap_ci_*` premise died with the scout retirement,
  and the suite passes on 3.11/3.12/3.13 alike. The file is rsynced to `/opt/shortlist`, so
  the value must track the deployed venv (3.12.3) or a hygiene commit rebuilds the live bot.
- **The accumulate failure-alert chain is VERIFIED end to end (2026-08-19)** — forced with a
  transient unit, Telegram message confirmed received. The script always `exit 0` by design,
  so the proof is the ABSENCE of its two stderr paths in the journal, not the exit code.
- **The `net_debt_to_ebitda` axis re-measured on DE-POLLUTED data changes nothing** — the
  2026-07-11 "leverage tilt NOT earned" verdict stands; nothing clears |t|>=2 on either
  universe. `2026-08-18-net-debt-to-ebitda-remeasure.md`.
- **`accruals` stays disabled** — re-measured on both reproducible universes 2026-07-18,
  reproducing the 07-12 table bit-for-bit. The 195-name universe that once earned it is
  permanently unreproducible. Nothing left to measure.
- **The three §2 price-refinement axes are measured and parked** — `pct_to_52w_high` and
  `vol_scaled_momentum` duplicate scored legs; `max_daily_return` is orthogonal but its sign
  flips across universes. **EV/EBIT** is a don't-ship (corr 0.55–0.72 with `fcf_yield`, no
  incremental IC). **`share_count`**, **`asset_growth`**, **`shareholder_yield`** and
  **`piotroski`** all failed the XS bar.
- **`shortlist-backtest --fit` cannot tune the live unfitted priors.** It fits **only** the four
  fundamental composite-axis weights (`quality, moat, growth, value`), requires `--source xbrl`,
  and proposes only (never writes `config.yaml`) — so it does not touch the
  `thresholds.accruals`/`thresholds.residual_momentum` bands, and `momentum` isn't a fit axis at
  all. Manual band review against measured IC is the only route.
- **Two committed double-sort spread claims are RETRACTED** (13D and 13D/A both now span zero);
  8-K still excludes zero. `2026-08-03-evaluator-rederivation.md` is the current record —
  quote it, not the older audits.
- **10-Q Part II Item 1 (legal proceedings) was deliberately NOT built** — measured, 10 of 15
  names are 200–800 chars of "Refer to Note 24 of this Form 10-Q". The legal substance is in
  the **notes**, so it is note-extractor work, not an item extractor. Do not re-raise it as a
  10-Q gap. `2026-08-14-tenq-part-ii-in-deep-design.md`.
- **`_tenq_mda` over-capture (3 filers in 35) is NOT harmful and must not be "fixed"** — JPM,
  MCD and PFE span a large fraction of the document, but all three start at a genuine MD&A
  heading, so the prefix surviving `max_chars.tenq_mda` is genuine MD&A prose. An earlier claim
  that JPM "is fed the first 40K of the wrong span" was wrong.
  `2026-08-14-tenq-mda-recovery-kill.md`.
- **The DEF 14A pay-vs-performance XBRL path is a NO-GO** (live-verified): SEC's XBRL APIs serve
  only `dei`/`us-gaap`, the `ecd` PvP tags are absent from companyfacts, and a
  `companyconcept/.../ecd/...` probe 404s. Snapshot replay is the only remaining route.

## Argued by an external review, recorded but NOT endorsed

The 2026-08-11 review of `/deep` (see §2 of `TODO.md` for the open half). These four are
positions this repo has considered and declined; each conflicts with a committed rule or
restates something already recorded.

- **"Split risk out of the composite."** `weights.risk: 0.10` is the *shipped* design of
  `docs/ASSESSMENT_GAPS.md` §2.9, deliberately a tilt. The review's point (low trailing vol is
  a preference, not an expected-return claim) is fair as a **labelling/display** question —
  expected-return evidence vs fundamental risk vs market exposure, shown separately. Changing
  the composite is a scoring change and needs evidence, not an argument.
- **"Disable `upside_to_target`."** Already recorded at
  `docs/PREDICTIVE_SIGNALS_RESEARCH.md` §Quick wins #1 (Brav & Lehavy: the *level* is
  negatively related to realised returns; the *revision* predicts). The mechanical obstacle is
  GONE as of 2026-08-18 — the leg is the scorer's one **opt-OUT** block
  (`scoring.py:_upside_to_target_on`), so the counterfactual can be run from config. The
  measurement itself is still open and lives in `TODO.md` §3.
- **"Label the composite heuristic until a survivorship-free, delisting-adjusted, walk-forward,
  multiple-testing-controlled validation exists."** That is already `CLAUDE.md`'s design premise
  verbatim. No action; do not open a work item that restates it.
- **Transcripts / estimate-revision history / 13F / news sentiment** are all already triaged in
  `docs/PREDICTIVE_SIGNALS_RESEARCH.md` (transcripts + estimates paid or no free point-in-time
  source; 13F a Phase-2 candidate; social/news as trigger not valuation). The one live free item
  was **recommendation-*change***, which SHIPPED 2026-08-22 as a display-only signal:
  `_rating_trend` (`data/sources/finnhub.py`) keeps the whole ~4-month window and stores the
  buy/hold/sell **deltas**, surfaced as the `/deep` `research/analyst_revision.py` line, the
  report's conditional `Revision` cell and a `--json` `analyst_revision` block. It is not a
  scored leg and did not need one: the level already reaches the scorer through
  `upside_to_target`, and adding the revision to the composite would need cross-universe
  rank IC this repo has not measured.

---

## Index of the dated notes

Newest first. A note marked *design* records intent and a probe at the time of writing; a note
marked *verdict* closes a question.

| Date | Note | Kind |
|---|---|---|
| 2026-08-24 | `2026-08-24-options-surface-design.md` — options surface as a `/deep` line | design + verdict |
| 2026-08-23 | `2026-08-23-icfr-adverse-conclusion-detection.md` — ICFR adverse conclusions + filing forms | verdict |
| 2026-08-22 | `2026-08-22-deep-arithmetic-clause-verification.md` — arithmetic clause 1/4 → 3/3 | verdict |
| 2026-08-22 | `2026-08-22-brief-self-consistency-base-rate.md` — brief self-consistency base rate | verdict |
| 2026-08-21 | `2026-08-21-operating-income-edgar-gap.md` — EDGAR `operating_income` gap | verdict |
| 2026-08-21 | `2026-08-21-inventory-context-line.md` | design |
| 2026-08-20 | `2026-08-20-debt-liquidity-notes-design.md` | design |
| 2026-08-19 | `2026-08-19-deep-prompt-live-verification.md` | measurement |
| 2026-08-18 | `2026-08-18-net-debt-to-ebitda-remeasure.md` | verdict |
| 2026-08-18 | `2026-08-18-deep-prompt-materiality-and-arithmetic.md` | design |
| 2026-08-17 | `2026-08-17-moat-management-evidence-design.md` | design |
| 2026-08-14 | `2026-08-14-tenq-part-ii-in-deep-design.md` | design |
| 2026-08-14 | `2026-08-14-tenq-mda-recovery-kill.md` | verdict |
| 2026-08-13 | `2026-08-13-eightk-text-in-deep-design.md` | design |
| 2026-08-11 | `2026-08-11-scout-retirement.md` — the big one | verdict |
| 2026-08-10 | `2026-08-10-roic-proxy-and-edgar-equity-design.md` | design |
| 2026-08-09 | `2026-08-09-13f-material-adds-design.md` | design |
| 2026-08-07 | `2026-08-07-wsb-novelty-rule.md` | verdict |
| 2026-08-07 | `2026-08-07-investability-floor.md` | evidence |
| 2026-08-07 | `2026-08-07-funnel-gate-mismatch.md` | evidence |
| 2026-08-06 | `2026-08-06-discovery-breadth-plan.md` | design |
| 2026-08-05 | `2026-08-05-*` — discovery funnel, standing-screen spikes (DERA/FDIC), quality floor, cohort price coverage, session log | evidence |
| 2026-08-04 | `2026-08-04-deep-brief-assessment.md`, `2026-08-04-tech-debt-burndown.md` | evidence |
| 2026-08-03 | `2026-08-03-evaluator-rederivation.md` — supersedes older spread claims | verdict |
| 2026-08-02 | `2026-08-02-edgar-companyconcept-fallback.md` | design |
| 2026-07-31 | `2026-07-31-edgar-concept-match.md` | evidence |
| 2026-07-26 | `2026-07-26-funnel-composition-audit.md` — the retraction postmortem | verdict |
| 2026-07-19 | `2026-07-19-13d-a-stake-increase-backfill-verdict.md` | verdict |
| 2026-07-12 | `2026-07-12-accruals-leg-disable.md` | verdict |
| 2026-07-11 | `2026-07-11-buyback-backfill-kill.md` | verdict |
| 2026-07-08 | `2026-07-08-eightk-composition-audit.md` | evidence |

`scripts/` holds the reproduction probes; `raw-*/` holds captured payloads.

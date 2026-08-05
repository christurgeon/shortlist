# TODO — follow-ups for a future session

Tracked, low-urgency follow-up work that has no natural home in code comments.
Newest context at top. See `docs/PREDICTIVE_SIGNALS_RESEARCH.md` for the signal designs and
`docs/ASSESSMENT_GAPS.md` for the broader scoring roadmap.

---

## Discovery funnel delivered zero candidates — DIAGNOSED, UNFIXED (2026-08-05)

Full evidence: **`docs/audits/2026-08-05-discovery-funnel-audit.md`**. Research prompt for a
breadth pass on new sources: `docs/audits/2026-08-05-discovery-sources-research-prompt.md`.
**Nothing was fixed** — this session was diagnosis only, by request.

The 2026-08-04 digest was empty because `raw = 0`. Three independent causes:

1. **`wsb_hype`'s demotion (#151, 2026-07-26) removed 10–15 of ~16–21 raw candidates/day.**
   Correct decision (it was 60% of picks at a $310B median cap), but no replacement
   origination was added, so it exposed how thin the rest of the funnel already was.
2. **`load_raw_company_tickers` (`cik_tickers.py`) has a same-day-only cache key, no retry,
   and no stale fallback.** One transient SEC 429 → `{}` → `signals.py:96` bails 13D, 13D/A,
   8-K, buyback and 13F symbology for the whole session. A valid 24h-old index sat unread on
   disk. `.cache/sec_tickers/` proves it: files through 08-03, none for 08-04. The
   top-weight originator (weight 1.5) was dead two sessions running.
3. **`edgar_index.py:155` fetches up to 2500 filings unthrottled** (`edgar_index_daily_cap`
   400 → 2500 in #152). Only `thirteenf.py` has a `SecThrottle`; there is no shared sec.gov
   throttle. Strongly correlated with cause 2 (DERA 429s appear in the 08-03/08-04 runs and
   only those) but the causal link is **inferred, not proven**.

Structural, beyond the defects: **every originator is event-triggered.** The only standing
screen, `YahooScreenerSignal`, has a 100% failure rate on this box (WAF). So empty days are
structural — there is no `min_candidates` or fallback universe anywhere in `daily.py`.
`edgar_13f` is dormant-by-design (4 bursts/year), not broken. Composition is still the
2026-07-26 problem: survivors skew nano-cap, so more volume ≠ better.

Ranked remedies are in §6 of the audit; user preference recorded is that the report should
**always surface something**, which implies a standing non-event screen. `api.nasdaq.com`
screener and the Nasdaq Trader halts RSS both returned keyless `200`s from the VPS.

⚠ **Do not hand-probe the Yahoo screener from the VPS** — doing so during this audit tripped
the WAF IP-wide and the `v8/finance/chart` price endpoint 429'd for minutes. Production was
unaffected, but that endpoint feeds the entire scorer.

**Status:** diagnosis committed; no fix applied. Remedies #1–#3 in the audit are defect fixes
and could ship independently of the (larger) standing-screen decision.

---

## Evaluator guards made unbypassable — SHIPPED (2026-08-04)

Post-mortem fix for the retracted 12pp claim. Design + adversarial review:
**`docs/EVALUATOR_GUARDS.md`** (revision 2, SIGN OFF WITH CHANGES). Suite 2293 green.

**A guard already existed and fired correctly; the number was quoted anyway.** Four
mechanical causes, all now closed:
1. **Per-bucket floor suppresses the SPREAD** (`642e264`). The first draft blanked
   `high_frac`/`low_frac` — rejected in review as inverted: it deletes the diagnostic and
   keeps the statistic whose validity that diagnostic tests. Now the fractions are never
   suppressed (a measurable fraction is not biased by attrition, it IS the measurement of it)
   and the registered `min_measurable_frac` applied per bucket suppresses the spread instead.
   Verified: `8k-neg` (high 0.527 / low 0.646) → `spread_ci` is now `None`.
   Also fixed a denominator bug shipped 2026-08-03 (`_frac` counted immature while the floor
   is mature-only) — corrected gaps are 0.1–0.7pp, not ≤3.3pp; the audit is amended.
2. **`level_suppressed` no longer hard-codes `False`** — it was asserting a decision nobody
   had made, so an ad-hoc caller got a dict claiming it was cleared.
3. **Closed-form Monte-Carlo error** on bootstrap intervals (`fda7c15`), so a width
   comparison inside its own noise is visible. The split-half estimator drafted first is
   biased ~1.6× and falsely reassures ~1/3 of the time.
4. **`validate --as-of`** (`2afa0a9`) — the retracted number came from an ad-hoc script that
   existed *because* validate hardcoded `date.today()`. Includes the safety obligation it
   creates: replay artifacts are labelled and **refused by the digest**, since a replay's
   `as_of` is the pinned date and the staleness gate cannot catch it.
5. **Coverage vs attrition split** in the floor note, with the corrected predicate
   (`hist is None or not hist.dates` — a dead symbol returns an EMPTY series, not `None`).

**CUT as disproportionate:** the reviewer's full C2 (mandatory `prereg`, 13 call sites).
Production always routes through `attach_double_sort`, and `--as-of` removes most of the
reason ad-hoc callers exist.

**Open:** extract `backfill.py`'s eight-reason classifier into a shared leaf + per-bucket
reason counts (reporting-only; the mechanical guard is already in).

**Status:** MERGED as **#160** (`4aa6249`), branch deleted. **NOT deployed** — `/opt/shortlist`
is at `92f3f6d`, now three merges behind (#157 is live there; #158 and #160 are not).

---

## TECH-DEBT.md retired — 1 open item fixed, 3 inherited here (2026-08-04)

`TECH-DEBT.md` (the 2026-06-14 fleet review's deferral list) is **deleted**. Of its **13**
entries, **10** were already closed — 7 RESOLVED plus 3 investigated/by-design no-op
verdicts (~77%, and only about half the file by line volume). It was largely a historical
record. Audit: `docs/audits/2026-08-04-tech-debt-burndown.md`.

**Counting correction:** an earlier draft of this entry said "14 entries, 11 closed (~90%)".
That double-booked the FMP-insider item — its long review blockquote was counted as a closed
entry while its parent heading was counted as open, even though the blockquote's own first
sentence says "stays deferred". Corrected on review.

**Fixed and gone:** FMP insider treated every non-`P` code as a sale (awards `A`, exercises
`M`, gifts `G`, tax-withholding `F`, conversions `C` all subtracted from `net_value_6m` and
inflated `sell_count`). `_fmp_insider.classify_tx` now three-ways them like the EDGAR
`_form4.classify_code` sibling, and `sources/fmp.py` **abstains** when a batch contains no
real P/S trade — without that guard an all-awards batch built `Insider(net_value_6m=0, …)`,
and `_is_present(0) is True` would let that all-zero record win `_merge_insider` and discard
EDGAR's authoritative data.

**Item 1 — momentum Stage 0 prize-bound re-run (INHERITED, blocked on host + quota).**
Re-run `uv run python -m shortlist.backtest.prize_bound` on the full **80-name** largecap
basket (the 2026-06-14 marginal PROCEED was a 28-name subset; a quota-starved run inflates
momentum's effective weight and overstates churn). **Decision rule:** compare `mom_12_1`'s
full-basket τ to the recorded **0.947** — holds or drops → the prize is real, write the
Stage 1 plan; rises toward 1.0 → **stop**, momentum at 0.08 is a near-zero mover
(EV/EBIT-style "measured, not shipped") and the only remaining lever is the value/momentum
weight split, a separate question. Drop `mom_6m` either way (τ 0.995 vs the incumbent
`rel_strength_6m`, zero churn — fully redundant).
**Why it is hard — and what is NOT the reason.** The real cost is FMP quota: ~12–13 calls ×
80 names ≈ **1,000 against the free 250/day cap**, so it needs a paid Starter tier or ~5 days
of split quota. Two blockers asserted in an earlier draft were **live-probed and are false**
(2026-08-04): an `FMP_API_KEY` *is* present in `.env` (an earlier check used a grep that
skipped `export `-prefixed lines; the key currently returns `429 Limit Reach`, i.e. quota
spent, not key missing), and Yahoo *is* reachable from `oracle-prod` — `v8/finance/chart/SPY`
returned **200 on 3/3 attempts** with the project's own `_UA`, and the scout's
`yahoo_blocked_until` was an expired self-clearing cooldown, not a standing IP ban. (A
generic Chrome UA got 429 on the same host, which is the header-shape fingerprint effect.)
So this is a **scheduling/quota** problem, not a host problem. It is a **measurement**, not
a code change.

**Item 2 — Finnhub `roiTTM` mapped to `roic` (INHERITED, needs re-scoping).**
`data/sources/finnhub.py` maps Return on *Investment* into the snapshot's `roic` (Return on
Invested *Capital*). The documentation half is already done — the mapping carries an inline
comment marking it a deliberate proxy on the FMP-gated fallback path. The numerics half
(keep the proxy vs. drop it to `None`) shifts quality/moat scores and was deferred pending a
quality/moat backtest.
**How it could be measured.** `--source xbrl` **cannot** — it derives ROIC from SEC
companyfacts and never exercises this fallback. But an earlier draft's stronger claim ("no
available backtest can measure it") is **too strong**: the **snapshot-replay** path can in
principle. `SnapshotSignalSource` re-scores stored merged snapshots through the production
scorer and emits `quality`/`moat` axes, both of which read `roic`; and the store already
holds **~706 Finnhub-provenance `roic` snapshots across 43 capture days** — past the ≥24
threshold, and exactly the FMP-gated population at issue. A proxy-on-vs-off replay (null the
`roic` where `provenance['fundamentals'] == ['finnhub']`, compare rank IC) is constructible.
What actually blocks it is **code work**: `backtest/cli.py` hard-codes a `--source snapshot`
refusal whose message ("needs >= 24 daily captures") never inspects the store and is now
stale, plus there is no on/off replay harness.

**Item 3 — should `fmp` outrank `edgar` for the insider transaction group? (INHERITED).**
Carried over from the FMP-insider review note; it would otherwise have been deleted with the
file. `config.yaml`'s `harness_sources` orders `fmp` before `edgar`, and `_merge_insider`
takes the coupled txn facts **wholesale** from the first source with a present field — yet
CLAUDE.md calls EDGAR "the free authoritative source" for insider data. So enabling a paid
FMP Starter tier would silently override EDGAR's insider numbers. A priority/intent
question, deliberately not resolved inside the classification fix.

**Status:** OPEN — items 1, 2 and 3. None is externally blocked in the way an earlier draft
claimed: item 1 needs FMP quota (or a paid tier), item 2 needs a snapshot-replay harness,
item 3 is a design decision. The classification bug that was item 3 of the old file is
closed. Do not re-file these against a deleted file.

---

## Evaluator correctness pack — SHIPPED; 13D "one survivor" claim must be RETRACTED (2026-08-03)

Branch `fix/evaluator-correctness`. Closes **0c**, **0g** and the 2026-07-11 **item 4** below.
Design + full evidence: **`docs/EVALUATOR_CORRECTNESS.md`** (tracked). Suite 2280 green, ruff
clean, verdict-impact gate **0 flips**.

**Read this first — a committed audit claim is now wrong, and it predates this branch.**
The audits and this file call the 13D double-sort spread "the one survivor" / "the strongest
evidence in either direction so far", quoting **+2.97%/mo, CI [+2.73%, +3.17%]**. Replaying
that cohort on current `main` gives **α +2.41%/mo, CI [−1.51%, +6.74%]** — **the interval
spans zero.** The old width (0.0044) is the artifact audit §3a diagnosed, and **#151 already
invalidated it**; this branch moves it again but did not cause it. **8-K still excludes zero**
(CI [+0.019, +0.080]), so the composite's sorting power is not retracted in general — only its
flagship instance. Until the audits are re-derived, **quote no double-sort spread CI from a
committed audit.**

**Three things shipped:**
1. **Pre-registration gate actually gates.** See item 4 below — the recorded `git mv` bug was
   the minor half; the unrecorded half let an *uncommitted* edit to a threshold pass.
2. **One corrected bootstrap** for both the spread CI and the verdict-bearing parent CI:
   resample ISSUERS, relabel per issuer-copy. The old per-draw relabelling disabled
   `calendar_time_portfolio`'s same-ticker dedup, so it bootstrapped a *different estimator*
   than it reported. Also: anchored month grid, bias-corrected percentile, and **no fallback**
   to the month bootstrap (it fails on thin cohorts, which is exactly where its interval is
   most artificially tight — abstain instead).
3. **ds-cohort floor check + per-bucket disclosure.** See 0g.

**Method note worth keeping.** Five directional claims were argued from plausible mechanism
and then contradicted by measurement — three of mine (spread CIs "widen"; `git status` proves
the file is registered; the ds cohort measures worse), and two from the adversarial reviews
(issuer clustering "makes intervals 21% too narrow"; a named test would fail — it went
*vacuous* instead, `None == None`). Two of mine survived into a committed spec before being
caught. Same failure as the 2026-07-26 retractions; same mitigation — measure it, then write
the sentence.

**Open follow-ups:**
1. ~~**Re-derive the four cohort audits.**~~ — **DONE 2026-08-03**,
   `docs/audits/2026-08-03-evaluator-rederivation.md`. All five cohorts replayed under both
   code vintages on identical data. **Point estimates bit-identical; 0 verdict flips; two
   spread claims RETRACTED** (13D +2.97%→+2.42% CI [−1.93%, +8.06%]; 13D/A +1.61%→**+0.07%**
   CI [−3.40%, +4.58%] — both span zero). **8-K survives** (+4.71%, CI [+1.89%, +7.97%]).
   Both retractions trace to **#151**, not to the bootstrap change.
   **Residual:** `8k-neg` (50% price-covered) and `13d-a` (79%) could not be measured from the
   cached snapshot. A fresh-price re-run needs a **throttle on `fetch_history`** (an
   unthrottled serial loop; ~6k Yahoo requests would risk the IP the nightly scout depends on)
   or an off-hours window that cannot collide with the 22:30 UTC timer. Small, tracked.
2. **Pre-register a `|high_frac − low_frac|` tolerance**, then enforce it. v1 discloses only,
   deliberately (0g).
3. **`random.Random` instead of the hand-rolled LCG** (`validate.py`). Real — glibc's LCG has
   known lattice structure in successive tuples, and successive draws are used as indices —
   but cross-cutting to every seeded path and would churn existing fixtures, so it was kept
   out of a correctness PR.
4. **`min_measurable_frac_ds`** was deliberately NOT added to the prereg YAMLs: the ds floor
   reuses the parent's registered value. It gates only display-only fields, and adding a key
   to all six files would reset all six content-registration clocks under the new §1.3 rule.
   Reversible choice, recorded rather than silent.

**Status:** MERGED to `main` and **PUSHED** to `origin/main` (`718dc37`) on 2026-08-04.
**NOT deployed** — `/opt/shortlist` is at `92f3f6d` — #157 is deployed and live, but this
pack is not (it lands with the next `git pull` there). Deploying is safe but not required: `validate` is
an operator-run CLI, no systemd unit invokes it, and the nightly digest only reads a static
`validate-latest.json`. Item 1 is the one that matters — a committed audit currently
overstates its evidence.

---

## EDGAR companyconcept fallback — MERGED (#157), deploy date not yet recorded (2026-08-02)

**MERGED to `main` as `641e94e` (#157, squash) on 2026-08-02; branch deleted.**
`docs/PLAN_EDGAR_ROOT_CAUSE_B.md`, `docs/audits/2026-08-02-edgar-companyconcept-fallback.md`.
It has **not** been deployed to
`/opt/shortlist` as of this entry (`/opt/shortlist` is still at `f0dd2cd`). Once
`deploy/install_opt_shortlist.sh` has been run for this branch, fill in the actual deploy date
in the audit's "Accumulation-store discontinuity" section — it dates the mid-panel
`diluted_shares` field-presence break in the accumulation store for CMCSA/CVX/GOOGL/HON/LMT/
MO/MRK/PG (pre-deploy snapshots carry `[]`, post-deploy snapshots carry real values).

Not load-bearing if this gets skimmed past: the date is also directly computable from the
store itself (first date under `state/snapshots/` with a non-empty CMCSA/HON `diluted_shares`),
and both trigger points (`backtest/signals.py:SnapshotSignalSource`, the commented-out
`quality.dilution` block in `config.yaml`) now carry their own caveat pointing back to the
audit — this entry is belt-and-braces, not the only guard.

**Status:** CLOSED 2026-08-03. Deployed ~22:51 UTC (`/opt/shortlist` `f0dd2cd` → `92f3f6d`,
symbols grep-verified in the deployed tree). The audit now records the date — **and the
off-by-one this entry existed to catch**: `shortlist-accumulate.timer` runs 21:30 UTC, so the
2026-08-03 snapshot predates the deploy by ~80 min and still reads `diluted_shares=[]`
(verified on CMCSA/HON, not inferred). **The field-presence break is 2026-08-04.** Bot restarted 2026-08-04 02:35:01 UTC
(26s SIGTERM drain for the in-flight long-poll, inside the 50s stop timeout), so the live bot
is running #157 rather than stale modules. **Fully deployed and live.**

---

## Session close — both fixes MERGED and DEPLOYED, production validated (2026-08-02)

`#154` (statements year-joined merge), `#155` (plan), `#156` (EDGAR concept-first matching)
are all merged to `main`; `/opt/shortlist` is at **`f0dd2cd`** and validated against live
EDGAR **from the deployed venv**, not just by file inspection:

| ticker | deployed `diluted_eps` | verdict |
|---|---|---|
| JNJ | `[11.03, 5.79, **13.72**]` | FY2023 corrected from 5.20 — the sign-flipped `eps_cagr_ps` (+45.6%/yr vs −10.3%/yr true) is gone |
| QCOM | `[5.01, 8.97, 6.42]` | continuing-ops → total; shares recovered (1.105B) |
| MSFT | `[17.95, 13.64, 11.8]` | computed → as-reported; shares recovered (7.453B) |
| HON | `[7.36, 8.71, 8.47]` | EPS recovered from nothing; shares still `[]` (root cause B, expected) |
| AAPL | `[7.46, 6.08, 6.13]` | **unchanged** — no regression on a working name |

**DEPLOY TRAP FOUND THE HARD WAY (now in CLAUDE.md).** The first redeploy attempt silently
did nothing: running `sudo bash deploy/install_opt_shortlist.sh` **from `/opt/shortlist`**
makes `SRC == DEST` (`install_opt_shortlist.sh:19` derives `SRC` from the script's own path),
so the rsync copies the directory onto itself. It still runs `uv sync`, reinstalls units and
restarts the bot — **so it reports success and refreshes mtimes while deploying nothing.**
Only a post-deploy `git -C /opt/shortlist log --oneline -1` caught it. Fix was `git pull`
inside `/opt/shortlist` first. **Never treat the installer's exit code as evidence the code
moved.**

**Open, in priority order:**
1. ~~**`--refresh` the cached research briefs**~~ — **RESOLVED 2026-08-02, and the premise was
   WRONG: NO cached brief was ever contaminated by #156.** Measured rather than reasoned:
   - Of 23 cached briefs, only IBM and MSFT belong to the corrected-EPS set. MSFT's brief
     mentions EPS nowhere (its buyback figures are filing-quoted dollar amounts, not derived
     from `share_count_cagr`).
   - IBM's brief quotes "EPS CAGR of 18.8%", which I took as the stale computed
     `eps_cagr_ps`. It is not. It is **`eps_cagr = cagr(net_income)` = +0.1883**, the
     net-income proxy the growth leg uses while `quality.dilution` stays OFF — and
     `net_income` was never touched by #156. The corrected per-share figure is
     `eps_cagr_ps = +0.1714`, which **no brief quotes**.
   - **Why the wrong conclusion looked confirmed:** pre-fix EPS was computed as
     `net_income / constant_scalar`, which makes `cagr(eps) ≡ cagr(net_income)` *identically*.
     So the stale `eps_cagr_ps` and the honest `eps_cagr` were the same number, and matching
     18.8% to +0.1883 "verified" the wrong metric. This is the `eps_cagr_ps` degeneracy the
     2026-07-31 audit already recorded, biting from the other direction.
   - **Lesson (same class as the four blast-radius misses):** reasoning "field X changed, so
     anything mentioning X is stale" is not verification. Trace which metric the consumer
     actually reads. `eps_cagr` and `eps_cagr_ps` are different fields with confusingly
     similar names.
   - IBM was refreshed anyway ($0.47); not wasted, since it picked up a newer 10-Q (cache key
     `…+0000051143-26-000078` vs `…-000038`), but the stated justification was wrong. The
     superseded brief file remains on disk beside the new one — harmless (the cache key is
     accession-composite so it is never served), but it is clutter.
   - Independent confirmation that #156 works in production: this run emitted
     `share_count_cagr = 0.0143` for IBM, where pre-fix it was `None`.
2. **FMP quota is ~2.7× over-subscribed** — accumulate (42 tickers) + scout (10) ≈ 676 calls/day
   against a 250/day free limit, which is why **23 of 24 store dates have ZERO fmp-won
   statements** and EDGAR supplies 100% of production statements. Options: drop `--max-tickers`
   to ~18, remove `fmp` from the accumulate chain (it contributes nothing today), or the paid
   Starter tier (~$14–20/mo, the only one that also unblocks the live FMP verification that
   stayed blocked all session). **A config-or-money decision, not a build.**
3. ~~**Root cause B (9 tickers)**~~ — **RESOLVED 2026-08-02** (`fix/edgar-companyconcept-fallback`,
   `docs/PLAN_EDGAR_ROOT_CAUSE_B.md`, `docs/audits/2026-08-02-edgar-companyconcept-fallback.md`).
   A pure aggregator (`_edgar_facts.diluted_shares_from_concept`) plus a network seam on
   `EdgarSource` (`_fetch_diluted_shares_concept`) recover `diluted_shares` for **8 of the 9**
   via SEC's single-tag `companyconcept` API (CMCSA CVX GOOGL HON LMT MO MRK PG), fallback-only
   and abstain-over-guess (fires only when the statement view already yielded `[]`; all-or-
   nothing re-index onto the spine). All 5 go/no-go clauses passed live on the 42-ticker store
   universe: the 8 match the plan's probe table exactly, byte-identical elsewhere, and the raw
   payload's own `cik`/`tag` fields were asserted to echo the request (the structural guarantee
   the NI/EPS arithmetic cross-check alone can't give). **XOM is a permanent residual** — it
   last tagged the diluted-shares concept in FY2013; the only weighted-average share tag left
   on recent 10-Ks is the *basic* count (4,305,000,000), which is deliberately NOT substituted.
   Sized as **hygiene, not an edge change**: all 8 recovered series are shrinking share counts,
   none within 6pp of `flags.dilution.min_share_cagr`, and `quality.dilution` stays OFF — no
   score/gate/ranking/selection changed, and on these 42 measured tickers no flag changed
   either. That's universe-scoped, not population-scoped — outside the 42 (scout, `/screen`,
   `/portfolio`) the ON-by-default `dilution` flag becomes newly evaluable for names that
   previously abstained (still advisory-only; see CLAUDE.md / the audit for the full caveat).
   The prior "do not enable `quality.dilution` until B is closed" objection is now **narrower
   (1 residual ticker, XOM, not 9)** — this does **not** itself justify enabling that leg,
   which remains a separate evidence-gated decision.
4. **Widen the go/no-go** beyond the store's 42 tickers — keyless, costs only time, and it is
   the only thing that further reduces residual risk (another code review would not).
5. `get_shares_outstanding_diluted()` still returns MCD's count in millions.

**Status:** CLOSED — both features shipped, deployed and validated in production. Items 1–5
are follow-ups; item 1 is the only one with a stale artifact sitting in production.

---

## EDGAR diluted-shares/EPS concept-first matching — SHIPPED, verified on all 42 store tickers (2026-07-31)

Closes the "NEW follow-up" opened in the Statements-merge entry below (`diluted_shares`
empty for MSFT/GOOGL/COST). Branch `fix/edgar-diluted-shares`, `docs/PLAN_EDGAR_DILUTED_SHARES.md`
(revision 4, signed off). Root cause: `_row_diluted_shares`/`_row_diluted_eps` matched
filer-presentation `label` text (which varies wildly — MSFT/COST/ORCL/PEP/QCOM label the
diluted-share row just `'Diluted'`, IBM `'Assuming dilution (in shares)'`, VZ omits the word
"diluted" entirely), not the authoritative raw us-gaap `concept`. Fix: `_rows_by_concept` +
`_series_by_concept_or_label` (`_edgar_facts.py`) match `concept` first, value-aware (a
concept row only wins if it yields a complete series, so it can never shadow a working label
row), falling back to the label scan. `db79d2f`.

**Verified live on all 42 real production tickers** (`/opt/shortlist/state/snapshots`,
keyless, before/after diffed programmatically against pre-fix `29f170f`):
- **7 tickers recovered `diluted_shares`** (COST IBM MSFT ORCL PEP QCOM VZ): `[]` → three
  real values, each independently cross-checked against `net_income / diluted_eps` — all 21
  data points (7×3yr) deviate ≤0.58% from the reported share count, confirming correct rows,
  not `iloc[0]`-style mispicks.
- **9 tickers flipped a computed EPS approximation to as-reported** (COST DIS IBM MCD MSFT
  ORCL PEP UNH VZ): the old fallback divided every year's net income by TODAY's share count
  scalar (MCD's `diluted_eps[0]` was **11,952,819.65** — a live garbage value feeding
  `pe_vs_history`; `pe_ttm` computed to `2.25e-05`). Now as-reported 2-dp values.
- **The go/no-go's clause 3 (byte-identical outside the 10 distinct tickers above — 7 shares
  ∪ 9 EPS, union not sum) FAILED on the first pass** and correctly caught a blast-radius
  undercount the plan's own detection heuristic (">2 decimal places" — finds only *computed*
  EPS) could not see: 4 more tickers changed, plus a correction to a 5th already in the 10.
  **HON, MRK, XOM** had `diluted_eps = []` in production (the fallback's `and shares_diluted`
  guard never fired because `get_shares_outstanding_diluted()` returns `None` for them) — now
  correctly recovered via concept match, a pure improvement. **JNJ and QCOM carried a
  pre-existing, live-in-production wrong-row EPS bug**, independent of and predating this
  branch: the label matcher picked `IncomeLossFromContinuingOperationsPerDilutedShare`
  (continuing ops) instead of the total `EarningsPerShareDiluted`. Both rows are
  byte-identical in years with zero discontinued-ops impact (masking the bug on spot-checks),
  and diverge sharply otherwise — **JNJ's FY2023 stored `diluted_eps=5.20` vs as-reported
  `13.72`** (the value the code actually stores post-fix; an independent cross-check,
  net income $35.2B ÷ 2,560.4M shares = $13.75, corroborates the order of magnitude and sign
  but is NOT the stored figure), because the pre-fix value excludes the one-time
  Kenvue spin-off gain booked as discontinued operations. Consequence: `eps_cagr_ps` on JNJ
  read **+45.6%/yr in production** when the corrected series computes **−10.3%/yr** — a sign
  inversion on a growth input. Plan owner adjudicated (`docs/PLAN_EDGAR_DILUTED_SHARES.md`
  §[R4], `491b6a1`): not a Task 1 code defect (the picker is correct and consistent on all
  five; matching `concept` instead of `label` is precisely what prevents this class of bug), a
  blast-radius **measurement** failure — the revised 14-ticker distinct expected-change set
  passed clause 3 on re-run. Full before/after table + repro:
  `docs/audits/2026-07-31-edgar-concept-match.md`.
- **Methodological lesson, recorded so it doesn't get relearned:** a "looks like a real
  number" detection heuristic (rounding/decimal-place patterns) cannot catch a *wrong-row*
  pick — a continuing-operations EPS is a clean, plausible 2-dp value indistinguishable from
  the total by inspection. Only an independent arithmetic cross-check against a value derived
  from a *different* filed tag (here, `net_income / diluted_shares`) surfaces it.

**Still open / deferred (unchanged from the plan, not touched by this branch):**
- **Root cause B (9 tickers, concept genuinely absent):** CMCSA CVX GOOGL HON LMT MO MRK PG
  XOM — no `WeightedAverageNumberOfDilutedSharesOutstanding` tag at any label; `diluted_shares`
  correctly stays `[]`. Skews to old-line industrials/energy/pharma (non-random) — harmless
  for the advisory `dilution` flag, a selection bias for a scored leg. **Do not enable
  `quality.dilution` until this is closed.** Candidate routes (raw companyfacts fetch;
  `net_income / diluted_eps` derivation, gated on EPS provenance to avoid circularity) in
  `docs/PLAN_EDGAR_DILUTED_SHARES.md` §Deferred.
- **`get_shares_outstanding_diluted()` units hazard:** returns MCD's count in millions, not
  absolute shares (`[716.4, 721.9, 732.3]`). This branch removes MCD's *dependence* on it
  (the EPS fallback that consumed it is dead for MCD now) but does not fix the function
  itself — any future consumer inherits the bug. Module docstring corrected
  (`_edgar_facts.py:7-10`) to stop claiming universal absolute-USD/share units.
- **JNJ/QCOM's pre-fix `eps_cagr_ps` was wrong in production until this branch deploys** —
  any prior measurement, backtest, or research brief that read `eps_cagr_ps` for these two
  names before this date used a sign-flipped (JNJ) or understated (QCOM) growth figure.
  Cached research briefs are accession-cached and will NOT auto-regenerate; `--refresh` to
  pick up the corrected value.

- **The `_row_by_standard_concept` duplicate-index fix has NO live repro.** None of the 42
  tracked tickers hits that path; the pre-fix crash was reproduced only on a synthetic frame,
  and coverage rests solely on the new unit test. It was fixed on consequence severity (a
  raise there degrades a ticker's ENTIRE statements payload to `None` via EdgarSource's
  failure isolation, and EDGAR now supplies 100% of production statements), not on observed
  frequency. Stated plainly so nobody later reads it as a fix for something we had seen.
- **Parked, from the final re-review:** `docs/PLAN_EDGAR_DILUTED_SHARES.md`'s historical
  "Step 2"/"Step 3" code blocks still quote the ORIGINAL signed-off text — the old test name
  and the pre-correction docstring, including the false "ultimately ABSTAIN" safety claim and
  the "argsort" wording. That is deliberate revision-log history and each block is followed by
  its `[R…]` correction, so it is not contradictory for a linear reader — but someone skimming
  and copying from those blocks would pick up stale text. Annotate them "superseded" next time
  that file is touched.

**Status:** MERGED as `bfb9796`… superseded — see below; shipped as **#156** and DEPLOYED
(`/opt/shortlist` at `f0dd2cd`, live-validated: JNJ FY2023 EPS now 13.72). Branch deleted.
Original note: SHIPPED on `fix/edgar-diluted-shares`, verified end-to-end (2236 tests green,
ruff clean, 42-ticker live before/after with the revised 14-ticker distinct go/no-go
passing). A final whole-branch review found a vacuous test, three doc-count errors, a
duplicate-index crash in `_row_by_standard_concept`, and other doc corrections — all fixed on
this branch (see the code review fix commit). Not yet merged to `main` or deployed to
`/opt/shortlist`.

---

## Statements-merge fix — MERGED (#154) and DEPLOYED (2026-07-31)

Actioned the 2026-07-20 data-audit item 1 ("FMP-won statements silently drop every
EDGAR-only field"). Branch `fix/statements-merge`, cut from `origin/main` `31e9764`.

**The defect:** `data/models.py` routed `statements` through whole-source `_pick_first`
while `config.yaml` ranked `fmp` above `edgar`, so for every non-402 name FMP won the
entire `Statements` object and every EDGAR-only field (`diluted_shares`, `diluted_eps`,
`fiscal_period_end`, `total_assets`, `asset_growth`, `accruals`, the §5 financing legs) was
fetched and discarded. Consequence: `bridge.py` derives `share_count_cagr` from
`st.diluted_shares`, and the ON-by-default `dilution` flag gates on it — structurally
incapable of firing on exactly the best-covered names. `shortlist-accumulate` was
persisting the degraded snapshots nightly with no retroactive repair.

**The fix (shipped):** `_merge_statements` (`data/models.py`) replaces `_pick_first` for
`statements` — the highest-priority source with data stays the spine (so revenue/growth
legs are untouched), and fields it left empty are backfilled from lower-priority sources
**re-indexed by fiscal YEAR, never by list position** (every consumer — `piotroski_f`,
`bridge._financial_series`, `cagr`, `[0]`-as-latest — aligns by index). Six pre-computed
latest-FY scalars copy only on a newest-year match. Design: `docs/STATEMENTS_MERGE.md`.
Plan: `docs/PLAN_STATEMENTS_MERGE.md`.

**All three tasks complete and reviewed.** `fed86a4` (pure fiscal-year join helpers) +
`75f7ab2` (`_merge_statements` + routing) + `bd863d6` (stale-docstring fix from round-1
review) + `cd481a0` (Task 3: end-to-end `dilution` regression test, CLAUDE.md +
`docs/STATEMENTS_MERGE.md` updates). A final whole-branch review then found a SECOND live
scoring-surface change the original design missed — recovering `diluted_eps` +
`fiscal_period_end` also re-activates a dormant EDGAR PE fallback (`bridge.py:241,243`)
feeding `pe_vs_history`, a **scored `value` leg** — and a merge-layer limitation worth
documenting (an all-`None` FMP column reads as "present" and silently blocks backfill for
that field, deliberately NOT fixed because relaxing it would make the `negative_fcf` hard
gate newly evaluable on an unmeasured population). Both are now recorded in
`docs/STATEMENTS_MERGE.md` §4/§6 and `CLAUDE.md`, with a regression test pinning the
`pe_vs_history` reactivation (`tests/test_statements_merge.py`). Suite green
(`uv run ruff check src tests` clean, `uv run pytest -q` passing) with the new test added.
**UPDATE 2026-08-02: merged as #154 (`bfb9796`) and DEPLOYED** — `/opt/shortlist` reached it
via `git pull` + `install_opt_shortlist.sh`; the accumulate timer has been capturing the
recovered fields since. Branch deleted.

**Open verification gap — exists nowhere else in the repo, read before touching this
branch again.** The plan's "Done When" required a live before/after `shortlist --json` run
on a real FMP-covered ticker showing `share_count_cagr`/`asset_growth`/`accruals`
populated where `main` returns null. **This did not happen.** FMP's daily quota was
exhausted, so both runs 429'd; with FMP absent, EDGAR won `statements` wholesale under
BOTH old and new merge logic, making the comparison uninformative. The mechanism is
covered by unit RED evidence only (see below). Re-run on AAPL/MSFT/LMT on a fresh-quota
day and record the actual values. Treat "recovered fields still null on a non-402 name" as
a bug report against the fiscal-year join key, not a config problem.

**LARGELY CLOSED 2026-07-31 — verified on REAL production data with ZERO FMP calls.** The
live CLI run is still owed, but its central unknown (do FMP and EDGAR agree on the fiscal-year
join key for real issuers?) is now answered empirically. Method: the accumulation store at
`/opt/shortlist/state/snapshots` holds 1,008 real snapshots; **17 of them are FMP-won**
(`provenance.statements == ['fmp']`, all 2026-07-07) and the rest EDGAR-won — so the store
already contains both sources' real spines. Re-merging the FMP-won statements against the
same ticker's EDGAR-won statements through the actual `merge_snapshots`, then through
`snapshot_to_metrics`:

| ticker | FMP years | EDGAR years | `share_count_cagr` before → after |
|---|---|---|---|
| AAPL | 2025–2021 | 2025–2023 | None → **−0.0259** |
| AMZN | 2025–2021 | 2025–2023 | None → **+0.0158** |
| CSCO | 2025–2021 | 2025–2023 | None → **−0.0131** |
| ADBE | 2025–2021 | 2025–2023 | None → **−0.0356** |
| DIS  | 2025–2021 | 2025–2023 | None → **−0.0052** |
| MSFT | 2025–2021 | **2026**–2024 | None → None (EDGAR `diluted_shares` empty) |
| GOOGL| 2025–2021 | 2025–2023 | None → None (EDGAR `diluted_shares` empty) |
| COST | 2025–2021 | 2025–2023 | None → None (EDGAR `diluted_shares` empty) |

- **The join key agrees on real data**, including non-calendar fiscal years (AAPL/DIS Sept,
  MSFT June, COST Aug, ADBE Nov) — the case most likely to break a year-label join.
- **Signs are right**: AAPL/ADBE/CSCO negative (buybacks), AMZN positive (SBC issuance).
- `diluted_eps` recovered **8/8** (0 → 5 rows), so the `pe_vs_history` reactivation is real
  and broad, not a corner case.
- **MSFT is the vintage guard earning its slot on real data**: EDGAR carries FY**2026**
  while FMP's newest is FY2025, so the latest-FY scalars correctly abstained rather than
  attaching a 2026 figure to a 2025 spine. That disagreement was hypothetical when designed;
  it is now observed.
- **NEW follow-up (upstream, not this fix):** EDGAR's own `diluted_shares` is `[]` for
  MSFT/GOOGL/COST while `diluted_eps` populates. The merge correctly abstained; the ceiling
  on this fix's yield is EDGAR-side coverage, ~5/8 here.
  **CORRECTION (2026-07-31): this was first attributed to
  `providers/_edgar_facts.py:get_shares_outstanding_diluted`. That is WRONG** — `diluted_shares`
  never comes from that function. `extract_financials:286` sources it from
  `_row_diluted_shares(income_df)`, a **label-string** matcher; `get_shares_outstanding_diluted()`
  is only an EPS fallback input (`:283-284`). Root-caused live and planned in
  `docs/PLAN_EDGAR_DILUTED_SHARES.md` (signed off): prevalence is **16/42 = 38%**, splitting
  into label-mismatch (7, fixable by matching the raw us-gaap `concept`) and
  concept-genuinely-absent (9, deferred). A third defect surfaced: the same label bug on
  `_row_diluted_eps` silently substitutes a **computed** EPS (each year's net income ÷ TODAY's
  share scalar) for 9 issuers — including MCD, whose `diluted_eps[0]` is **11,952,819.65** and
  whose live `pe_ttm` is therefore `2.25e-05` on a scored surface.

**Live CLI wiring smoke — PASSED 2026-07-31, keyless (no FMP quota).**
`uv run shortlist --tickers AAPL --json --provider yahoo,edgar,finnhub` runs the full
CLI → collector → merge → bridge → scoring → JSON path against real HTTP and emits
`share_count_cagr -0.0259`, `asset_growth -0.0157`, `accruals 0.0015`. The
`share_count_cagr` value is **identical** to the store-based offline merge above, so the two
independent paths cross-validate. (Note the CLI flag is `--tickers AAPL`, NOT positional.)
Together with the FMP-spine branch verified offline on real FMP data, the only thing never
exercised is the FMP-wins branch *under a live fetch* — logic and wiring are each covered.

**WHEN to retry — measured 2026-07-31 00:42 UTC, second attempt, still blocked.** A direct
FMP probe still returned `"Limit Reach"`. Root cause is **our own nightly timers**:
`shortlist-accumulate` (21:30 UTC) and `shortlist-scout` (22:30 UTC) had run 3h12m and
2h12m earlier and drained the free-tier quota. UTC midnight had passed 42 min before the
probe **without** a reset, so FMP's free window is **NOT calendar-UTC-day aligned** (rolling
24h, or a non-UTC boundary). So "just try again tomorrow" at a similar hour will collide
again. Retry in the **mid-day UTC window** — after the reset and well before the 21:30
accumulate timer — and never shortly after 22:30 UTC. Budget ~26 calls (1 ticker × 2 runs,
~13 calls/ticker, 250/day free limit); the constraint is timing, not budget.

**Unit RED evidence for the mechanism (in lieu of the missing live run).** For the
`pe_vs_history` reactivation specifically: `_pick_first([('fmp', fmp_statements),
('edgar', edgar_statements)])` returns the FMP object byte-identical to a plain
`_fmp_st()` fixture (`diluted_eps == []`) — i.e. under the OLD merge logic EDGAR
contributes nothing even when present in the source list, which is exactly the
"single-source FMP-only" branch `test_pe_vs_history_reactivates_on_an_fmp_covered_name`
already exercises and asserts `pe_ttm`/`pe_median_5y` stay `None`. Live-verified with the
old `_pick_first` helper directly against the test fixtures during this session.

**Renamed for accuracy:** `tests/test_sources_leverage.py::test_pick_first_merge_carries_leverage_fields`
→ `test_statements_merge_carries_leverage_fields` (docstring was already correct; only the
name still referenced `_pick_first`, which no longer merges statements).

**Parked minor, still open, low priority:** `models.py` `_usable_years` does not reject an
all-`None` `fiscal_years` list. Net observable behaviour is identical to rejection
(`_newest_year` → None short-circuits the scalar guard; `_reindex_by_year` → `[]` so no
backfill), so it is a docstring-vs-contract self-consistency gap, not a wrong output.

**Incidental finding, not fixed:** `tests/scout/test_daily_demo.py` (TODO item 0d, below)
was passing as of the prior session — GOOGL's 2026-07-20 pick had aged out of its 7-day
cooldown. The test still reads the live `state/scout_state.json`, so it is dormant, not
fixed, and will fail again the next time a pick lands inside that window.

**Status:** MERGED (#154) and DEPLOYED. The live FMP before/after was never run — FMP's
quota is structurally exhausted (see the FMP over-subscription item), so the data-path claim
still rests on unit tests plus the store-based offline verification. Re-run if the quota is
ever fixed.

---

## Form 4 opportunistic-insider originator — SHIPPED and deployed (2026-07-30)

Merged as #151 (evaluator fixes) + #152 (the originator) and **live on the VPS**. Verified
against the running system, not just the files: `MemoryMax=629145600`,
`EDGAR_RATE_LIMIT_PER_SEC=6`, `TimeoutStartUSec=30min`, timer + bot active, DERA cache warm
(15 zips + prebuilt index).

`edgar_form4` replaced: was a bare cluster count with **no dollar floor** (emissions read "2
insiders bought $5k" — the bottom quartile) reading ~400 of a median-838 filing day. Now: raw
Form 4 XML, CMP-2012 routine/opportunistic classification off a DERA trade-month index, $100k
per-transaction floor, role weighting, 10b5-1 exclusion, joint-filing abstention, 2500/day.
Ships at **weight 1.0**, not the retired signal's 1.5 — no live track record yet.

Live-measured, not assumed: tier mix **routine 48.5% (dropped)** / opportunistic 19.3% /
unclassified 32.2% (n=887), independently reproducing CMP-2012's "over half … are routine".
Cold index build **26.9s, 68,499 entries, 295 MB peak**; warm read **0.5s** (54×) — the cache
fix confirmed working in production.

**Open follow-ups:**
- **The backfill leg is NOT wired.** `preregister/edgar_form4.yaml` is committed (so the
  anti-p-hacking guarantee holds) but no cohort has been run. It needs quarterly-ZIP fetching
  plus a point-in-time `assemble_factory` — the index must be built only from quarters strictly
  BEFORE each event's quarter, or future trading behaviour leaks into the classification.
  Needs its own spec.
- **Live measurement is the picks ledger.** Every emission carries its tier, so the
  opportunistic-vs-unclassified comparison becomes possible as calendar time accrues. That
  within-cohort spread is the statistic this data supports; absolute cohort levels are not
  trustworthy (see the 2026-07-26 audit).
- **Deferred minors** from the reviews are listed in the git history of the merged PRs
  (e.g. `_TRUE` duplicated between `dera.py`/`insider.py`; `n_joint` counts tickers pre-filter
  and is labelled "filings"; `fetch_daily_records`/`fetch_recent_records` are now dead code
  plus tests pinning them).
- ~~**`tests/test_earnings.py::test_normalize_finnhub_populates_earnings` fails on `main`**~~ —
  **FIXED 2026-07-30** (it hardcoded `2026-07-29` as a *future* earnings date and the calendar
  passed it; now derived from `date.today()`). The sibling case, `test_daily_demo.py` reading
  the live `state/scout_state.json`, is still open — see item 0d below.

**Status:** DONE and deployed. Remaining work is the backfill leg (needs its own spec) and
letting the ledger accumulate — deliberately no new signals until it says something.

---

## Form 4 opportunistic-insider rebuild shipped; backfill leg deliberately deferred (2026-07-27)

`edgar_form4` rebuild (item 2 of the funnel-composition-audit entry below) is **DONE** — all
6 tasks of `.superpowers/sdd/PLAN_FORM4_INSIDER/` landed on
`feat/form4-opportunistic-insider`: `scout/dera.py` + `scout/insider.py` (CMP
routine/opportunistic classification off a SEC DERA bulk index), a rewritten
`EdgarForm4Signal` (raw Form 4 XML, one request/filing via `full_text_submission()`, $100k
per-transaction floor, role weighting, 10b5-1 exclusion, joint-filing abstention,
`edgar_index_daily_cap` raised 400→2500), and `scout/preregister/edgar_form4.yaml` committed
before any measurement run. Docs: `docs/FORM4_INSIDER.md` status flipped to IMPLEMENTED;
CLAUDE.md gained a full section + the `aff10b5One`/`footnoteId`/DERA-rounding/`Filing.text()`
landmines.

**Deliberately NOT wired: the backfill cohort itself.** Every other EDGAR originator's
backfill uses a pure per-chunk `assemble` (or, for `13d-a`, a stateful `assemble_factory`
that only needs the *prior filing* in scope). A Form 4 backfill is a harder case: the CMP
classification needs a trade-month index built from DERA quarters **strictly BEFORE** each
event's quarter, or future trading behaviour leaks into the routine/opportunistic label —
i.e. a point-in-time `assemble_factory` that walks quarterly ZIPs in order, not a single
static index. That is a real design (fetch cadence, PiT index cost, cache shape), not an
afternoon's row in `_BACKFILL_SPECS`, and needs its own spec before it's built. Until then
the live signal accrues evidence only through the picks ledger + firehose (no cohort verdict
possible).

**Status:** Rebuild DONE, merged into `feat/form4-opportunistic-insider` (not yet merged to
main/deployed at time of writing — see the branch's own PR when opened). Backfill leg is
OPEN, blocked on a follow-up spec; not started.

---

## Funnel composition audit — originator universe is the bottleneck, not the scorer (2026-07-26)

Full evidence: **`docs/audits/2026-07-26-funnel-composition-audit.md`** (committed). Reviewed
21 daily sessions, the 141-row selection ledger, and all four backfill cohorts.

The finding that reframes the discovery roadmap: **the composite ranks well inside every
measured cohort** (double-sort spreads +2.97%/mo for 13D, +6.26%/mo for 8-K, +0.99%/mo for
buyback) **while every cohort's level is deeply negative** (−0.8 to −8.6%/mo) — universe
composition at origination, not a scoring failure. Read the *signs* only; see the error-bar
caveat below.

Entry-price composition is the suspected mechanism, but the support is weaker than the first
draft claimed and it is **not monotonic** — 8-K has lower penny density than 13D (27% vs 33%)
yet a worse level (−8.57% vs −5.99%). Only one contrast really holds, on n=3 cohorts: the
single clearly-non-penny cohort (buyback, 10% sub-$5, median $27.72) is ~7–10× less negative
than the two penny-heavy ones — and buyback selects profitable firms, a confound that would
produce the same picture. The better-powered evidence is the 13D gate rate by entry price
(90% gated at $0–1 → 47% at $20+).

What needs no statistics at all: the live funnel is barbelled — 60% of picks are `wsb:hype`
at a **$310B** median market cap, 21% are 13D at a **$50M** median, and 46% of all ledger
rows were gated after burning a deep-screen slot.

**Caveat found while stress-testing the above (§3a of the audit): the evaluator's error bars
are not valid.** `validate.py:_ctp_rows` flattens each event's whole K-month return to a
constant monthly rate and repeats it across the holding window, so the CTP series has no
price-path variance. Implied monthly tracking error backed out of the published `(alpha, ir)`
pairs: **0.32%** for 13D raw, 1.04% 13D scored, 3.67% 8-K, 2.85% buyback — against a plausible
3–8%. An IR of −46.97 is an artifact. Consequence: the KILL rule ("alpha 90% CI entirely
negative") effectively fires on *any* negative point estimate, so the `edgar_buyback_auth`
KILL (CI upper bound −0.005pp) is likely INSUFFICIENT under honest error bars. Signs survive;
magnitudes, CIs, IRs and verdict confidence do not.

**🛑 SUPERSEDED SAME DAY — read §4 of the audit first.** Running item 1 (below) broke the
analysis above. `calendar_time_portfolio` averages **per-event geometric monthly equivalents**
(`(1+ret)**(1/K)-1`), which is concave — by Jensen the mean of the flattened returns is ≤ the
flattened mean, and the gap explodes near total loss (a −99% event contributes −31.6%/mo; a
+500% winner only +16%/mo). Measured on the full 13D cohort: **mean 12m return +7.0%
(POSITIVE), evaluator reports −4.46%/mo (≈ −42%/yr) — the sign is flipped.** So "every cohort's
level is deeply negative" is an ARTIFACT, the composition thesis is neither confirmed nor
refuted, and **every level-based verdict this project has issued is void** (including the ones
re-derived today). The double-sort SPREAD survives — it is a difference between two cohorts
measured identically, so the common bias cancels. The directly-observed funnel facts (items 2
and 3) are untouched.

~~**NEW ITEM −1, ahead of everything: fix `calendar_time_portfolio`.**~~ — **RESOLVED, and
this entry was STALE for a week.** Shipped in **#151 (`7398ef2`)**: `MeasuredEvent.monthly_rets`
+ `_monthly_path()` give the CTP each name's ACTUAL month-t return, falling back to the old
constant rate only when an event carries no path. Verified 2026-08-03 by reading
`validate.py:239-252`, not by trusting this entry.

**This mattered:** the text below said "quote NO cohort alpha … treat the verdicts as
unmeasured", i.e. it told any reader that every measurement in the project was void, long
after the fix had landed. If you are skimming for what blocks work, a stale blocker at the top
of the file is worse than no entry. Original text: *a month's portfolio return must be the
mean of held names' ACTUAL month-t returns, not the mean of their compounded-then-flattened
ones … then re-derive all four cohorts and rewrite the audits.*

**The re-derivation half is still OPEN and now has a concrete consequence** — see the
2026-08-03 entry at the top of this file: the 13D double-sort spread the audits call "the one
survivor" **no longer excludes zero** under the fixed code.

Open work, in order:
0. **DONE (2026-07-26) — event-level bootstrap CI.** `alpha_ci` now comes from
   `validate.py:event_bootstrap_alpha` (resamples EVENTS with replacement, rebuilds the CTP
   per replicate, relabels drawn tickers so the same-ticker dedup can't discard a duplicate
   draw); falls back to the month bootstrap for cohorts with no event list. `ir` carries a
   permanent "upward-biased — display only" note. Suite 2125 green, ruff clean.
   **Re-derived `edgar_buyback_auth`:** scored CI moved [−1.80%, −0.00%] → [−1.72%, **+0.13%**]
   — it now straddles zero, so the CI trigger no longer fires. 13D re-derivation (the severe
   case, implied TE 0.32%) still running at time of writing.
0b. **DONE (2026-07-26, operator decision) — the bare `alpha <= 0` KILL trigger is REMOVED.**
   KILL now requires the CI to be entirely negative; a negative point estimate with a
   straddling CI yields INSUFFICIENT. All four cohorts re-derived (§3b of the audit):
   - **`edgar:buyback_auth` KILL → INSUFFICIENT — genuinely RETRACTED** (CI now
     [−1.72%, **+0.13%**]). `docs/audits/2026-07-11-buyback-backfill-kill.md` carries a
     SUPERSEDED header; the `config.yaml` comment is rewritten. **Stays `enabled: false`.**
   - **`edgar:8k` KILL → INSUFFICIENT but for an UNRELATED reason** — a vintage measurability
     floor (2023: 0.89 vs 0.90). Its alpha CI is still entirely negative [−10.21%, −7.15%].
     **Not a rehabilitation.** Note added to the 2026-07-08 audit; memory amended.
   - **`edgar:activist_13d` unchanged and now better supported** — −4.45%/mo, CI
     [−5.22%, −4.47%] after a 3.4× widening. The negative level was NOT an artifact.
0e. **RESOLVED 2026-07-26 — levels are structurally unmeasurable; STOP trying to fix them.**
   A bounded experiment (audit §5) settled it. Alpha by nominal entry price: ALL **+3.04%/mo
   (+43%/yr)**, ≥$5 −1.82%, ≥$20 −4.15% — neither end credible; dropping untradeable `*F`/`*Y`
   OTC tickers moved it 0.13pp, so contamination isn't the driver. The real blocker is
   **outcome-correlated attrition**: 21.5% of events have no price series and 3.6% no ticker,
   with the missing rate monotonic in age (2022 **33.7%** → 2025 14.1%) — i.e. companies
   disappearing via acquisition/delisting. For a 13D cohort that removes the WINNERS (a forced
   sale at a premium is a successful campaign). No weighting, factor model or bootstrap fixes
   non-random attrition; it needs CRSP-style point-in-time delisting returns that free Yahoo
   cannot supply. **The `min_measurable_frac: 0.90` prereg floor was firing correctly all
   along** — 13D measures 0.62–0.70 — and the earlier analysis quoted the levels anyway.
   **CORRECTION (audit §5.4): this applies to the RAW firehose, not to every cohort.** The
   **scored/gated** cohorts CLEAR the floor — 13D scored frac **0.92**, alpha **−0.43%/mo, CI
   [−2.43%, +1.46%]** (credible); 8-K scored frac 0.93. Quality/gate filtering removes the
   shells whose disappearance drove the attrition, which is what R-B5 already said: the scored
   cohort is the decision surface, the raw firehose never was.
   Consequences: (a) **do NOT build the ABK/value-weighting correction** — removed from the
   roadmap; (b) never quote a RAW-cohort alpha; (c) **scored-cohort levels ARE usable**, read
   alongside the double-sort spread; (d) **no data purchase indicated** — survivorship-free
   vendor data (Sharadar SEP ~$50/mo, Norgate, EODHD) would repair the raw cohorts, but the
   decision-relevant scored ones already measure; revisit only if a future signal's scored
   cohort fails the floor; (e) KILL requires an entirely-negative CI on a floor-clearing
   SCORED cohort.
0c. ~~**Remaining gap:** `double_sort`'s `spread_ci` still uses the month-resampled
   bootstrap.~~ — **RESOLVED 2026-08-03** (`02beaf6`, `docs/EVALUATOR_CORRECTNESS.md` §2).
   Note the framing here ("the spread CIs are still too tight") was **not confirmed**: measured
   at B=1000 the event bootstrap widens 1.22× (13d) and 1.18× (buyback) but is 0.97× on 8k —
   the justification is model *consistency*, not conservatism. Fixing it surfaced a larger
   defect in the **verdict-bearing** `event_bootstrap_alpha`: per-draw relabelling disabled
   `calendar_time_portfolio`'s same-ticker dedup (+19.6%/+23.7% held-set inflation), so it
   bootstrapped a different estimator than it reported. Both now use an issuer-clustered
   resample with issuer-copy relabelling. Verdict-impact gate: **0 flips** across all four
   cohorts, raw + scored.
0g. ~~**Remaining gap:** the double-sort cohort is never measurability-floor-checked.~~ —
   **RESOLVED 2026-08-03** (`02beaf6`, `docs/EVALUATOR_CORRECTNESS.md` §3).
   `attach_double_sort(..., ds_floor_failed=)` now blanks the absolute legs when the ds cohort
   fails its OWN floor, not only when the parent is suppressed. **The guard is PREVENTIVE:**
   measured on all four committed cohorts the ds population is measured *better* than the
   scored one on **both** the pooled and the vintage branch (13d ds 0.940 vs scored 0.919
   with a bad 2025 vintage; 8k ds 0.958 vs scored 0.932 with a bad 2023 vintage). It has never
   fired — do not describe it as fixing an active bias.
   **Disclosure added alongside it:** per-bucket measurable fractions (computed over ALL
   composite-defined events — splitting the already-filtered `eligible` list would report a
   tautological 1.0), so "a difference between two *identically-measured* buckets cancels the
   common bias" is checkable rather than asserted. The audit's own wording is "**largely**
   cancels"; `validate.py` had dropped the hedge and it is now restored.
   **CORRECTION (2026-08-03):** this entry first reported a **12pp asymmetry on 8k-neg** as
   evidence the cancellation premise fails. **That was wrong** — 8k-neg is only 50%
   price-covered in the cached snapshot, so the gap measured which tickers were cached. On
   every ≥95%-covered cohort the buckets are measured alike (≤3.3pp), which *supports*
   cancellation. See `docs/audits/2026-08-03-evaluator-rederivation.md` §4.
   **Follow-up (open):** enforcing a tolerance on `|high_frac − low_frac|` needs a
   PRE-REGISTERED threshold — inventing one post-measurement is the exact sin pre-registration
   exists to prevent, so v1 discloses and does not enforce.
0d. **Flaky, unrelated:** `tests/scout/test_daily_demo.py` fails on clean HEAD — it reads the
   live `state/scout_state.json`, so GOOGL falls inside the 7-day cooldown from its
   2026-07-20 pick. Date-dependent; should use a fixture state, not production state.
1. **RUN 2026-07-26 — INCONCLUSIVE, and it exposed item −1.** Banded the 13D cohort at ≥$5 /
   ≥$20 on both the stored `as_of_price` and (after finding that field is **split-adjusted** —
   `LGMK` enters at $18,487.50/share) on true nominal prices via
   `PriceHistory.nominal_close_asof`. Alpha got monotonically WORSE with the band
   (−4.45% → −5.20% → −8.16%/mo), which is backwards; both price definitions agreed. That is
   the Jensen bias in item −1, not a real result. **Re-run this test only after item −1
   lands.** Trap for the next attempt: `as_of_price` in the backfill JSONLs is split-adjusted
   and is NOT a size proxy — band on nominal price or, better, on real market cap.
2. **Rebuild `edgar_form4` as an opportunistic-insider originator.**
   **DESIGN APPROVED 2026-07-26 → `docs/FORM4_INSIDER.md`** (tracked deliberately; the
   conventional `docs/superpowers/specs/` is gitignored and has already eaten two artifacts).
   Decisions: two pure leaves `scout/dera.py` + `scout/insider.py`; ONE record contract shared
   by live (raw Form 4 XML) and history (raw DERA TSV), both RAW fields — never edgartools'
   normalized view, pinned by a both-paths-parse-identically test; trade-month index built
   from ALL transaction codes; emission unit is the ISSUER with a PER-TRANSACTION $ floor;
   three tiers (routine dropped / opportunistic 1.0 / unclassified 0.6, tier logged).
   **PLAN → `docs/PLAN_FORM4_INSIDER.md`** (6 TDD tasks); execution started 2026-07-26 on
   branch `feat/form4-opportunistic-insider` via subagent-driven development (ledger:
   `.superpowers/sdd/PLAN_FORM4_INSIDER/progress.md`). **The `form4` backfill leg is
   deliberately NOT in scope** — spec §3 defers the cohort, and a Form 4 leg needs
   quarterly-ZIP fetching plus a point-in-time `assemble_factory` (index from quarters
   strictly BEFORE each event's quarter, else future trading behaviour leaks into the
   classification). That needs its own spec.
   **Execution state — ALL 6 TASKS DONE (2026-07-27).** Tasks 1–4 **complete, each reviewed spec ✅ /
   quality approved** — `scout/insider.py` (record + XML parser + CMP classification +
   qualification/strength/emission) and `scout/dera.py` (bulk parser + trade-month index +
   quarterly ZIP fetch/cache). **Task 5 (live wiring) implemented, fix round 1 in flight.
   Task 6 (pre-registration + docs) DONE (2026-07-27, this entry).** Nothing merged; branch
   `feat/form4-opportunistic-insider` is unpushed at time of writing. Ledger:
   `.superpowers/sdd/PLAN_FORM4_INSIDER/progress.md`.
   - **Task 5 finding, still open:** `config.yaml`'s `edgar_index_daily_cap` was still **400**,
     and `daily.py` passes it as `max_filings` — so the live signal would still read ~400 of a
     **median 838 / p90 1,498 / max 3,496** filing day, i.e. the coverage defect the rebuild
     exists to fix. Fix in flight raises it to **2500** with the measured justification.
   - **Two real bugs in the plan's pseudocode, caught by the Task 5 implementer:** a dangling
     `_default_index` reference, and `edgartools' Filing.text()` round-tripping XML-native
     forms through an HTML renderer, **destroying the `<ownershipDocument>` tags the parser
     needs** — use `full_text_submission()` (one request, the raw `.txt`). Both would have
     shipped a silently non-functional production path.
   - **`daily.py:_signal_kwargs` edit approved** (outside the task's file list, correctly
     flagged): without it the `scout.form4` block never reaches the signal and
     `tier_strength.opportunistic` silently defaults to 0.6 instead of 1.0.
   - **Memory measured, not assumed:** cold index build = **288 MB peak RSS**, 68,499 entries
     from 15 real quarters (the 16th unpublished, skipped by design) — under the 400 MB stop
     threshold set for this 1.9 GB VPS.
   - **`issuer_cik` added to the record (spec §5.2)** so emissions set `Emission.cik`;
     `edgar_13f` ships `cik=None` and CLAUDE.md records that as a known limit blocking
     renamed-ticker re-resolution and CIK-based delisting classification. Retrofitting after
     the ledger has entries is far more expensive.
   - **TIER MIX MEASURED (2026-07-26) — the filter bites hard; spec §6 open question CLOSED.**
     Index from 15 published quarters (66,337 insiders), evaluated on the newest quarter's v1
     population (n=887, as-of 2026-03-31): **routine 430 (48.5%, DROPPED)** / opportunistic
     171 (19.3%) / unclassified 286 (32.2%). Nearly half the qualifying population is
     discarded as routine, independently reproducing CMP-2012's own "over half … are routine"
     on a different sample two decades later. `unclassified` does **not** dominate, so the §6
     deviation stands as chosen. Volume sanity: ~13 issuers/day with a ≥$100k buy, less 48.5%,
     ≈ 6–7/day — matching the spec's expected 6–8. Reproduce: `scratchpad/tiermix.py`.
   - **Spec amended mid-execution → `docs/FORM4_INSIDER.md` §5.1: joint filings are
     ABSTAINED.** A Form 4 may carry several `<reportingOwner>` blocks and neither the XML nor
     DERA joins a transaction to a *particular* owner. Measured 2025Q1: **1.72%** of all Form
     4s, **12.05%** of those containing an open-market purchase, **9.5%** of the v1 population
     — so ~1 in 10 emissions would carry a wrong `owner_cik` and hence a wrong CMP tier.
     `InsiderTxn.joint_filing` + rejection in `qualifies()` + a surfaced count.
   - **Also verified mid-execution:** DERA rounds `TRANS_PRICEPERSHARE` to 2dp while the XML
     keeps full precision (`24.57` vs `24.5686`), so the cross-path guard compares price with
     a tolerance — do NOT tighten it to `==`, and do NOT round the XML down to match.
   - **Deferred minor (Task 1):** an `isinstance` assertion-of-convenience in
     `tests/test_scout_insider_parse.py` exists only to satisfy ruff F401. Harmless; fold into
     a real assertion next time that file is touched.
   Background:
   1.5 (joint-highest) with **no prereg, no backfill spec, no audit** — while three
   lower-weighted originators were killed by measurement. Today it is a bare count heuristic
   (`min_buyers=2`, **no dollar floor** — real emissions read "2 insiders bought $5k", the
   bottom quartile of insider buying) and `edgar_index_daily_cap: 400` makes it read ~48% of a
   median Form 4 day (measured: median 838 filings/day, p90 1,498). Add dollar floor + role
   weighting + 10b5-1 exclusion + Cohen-Malloy-Pomorski routine/opportunistic split + size
   band. Backfill via **SEC DERA Insider Transactions Data Sets** (quarterly ZIPs, ~12.8 MB,
   verified live 2026-07-26; `ISSUERTRADINGSYMBOL` inline = PiT ticker, `AFF10B5ONE` = 10b5-1
   flag, `RPTOWNERCIK` = stable person ID for the routine classification). Measured headroom:
   median **13 issuers/day** with a ≥$100k open-market buy vs ~2/day emitted today.
3. **Funnel composition fixes.** (a) **DONE (2026-07-26, operator decision)** — `wsb_hype`
   demoted to confirmation-only (`scout.signals.wsb_hype.enabled: false`, rationale in the
   config comment). Raw flow ~9–13/day → ~5–9; ~4 deep-screen slots/day freed for the EDGAR
   originators. The per-ticker `social_hype` flag still confirms hype on names that arrive via
   another originator. **Not yet deployed to `/opt/shortlist`** — takes effect there on the
   next `git pull` + installer run. (b) Market-cap band at prefilter — **estimated "hours" in the
   first draft; that was wrong.** `funnel.py:prefilter` receives only ticker + signal
   provenance and has **no market-cap data at all** (cap arrives later, from the deep
   screen). Doing this needs a new cheap pre-screen lookup (Finnhub `stock/profile2`
   `marketCapitalization` is free and ~30 calls/day at current flow) plus its own config
   block and tests — a small feature, not a config tweak.
4. **Deferred but kept:** materiality-scaled government-contract-award originator (USAspending
   daily, award ≥ X% of TTM revenue) — matcher + source already exist.

**Status:** IN PROGRESS — but the analysis that framed this entry is PARTLY RETRACTED; item −1
(fix `calendar_time_portfolio`) now precedes everything and no cohort alpha should be quoted
until it lands. Items 0, 0b and 3(a) shipped (suite 2126 green, ruff clean, committed on
`fix/validate-event-bootstrap-ci`, pushed). **Item 2 (the `edgar_form4` rebuild) is now DONE**
(2026-07-27, branch `feat/form4-opportunistic-insider`, not yet merged — see the standalone
entry above for the deferred backfill-leg follow-up). Items 0c, 0d, 1, 3(b), 4 remain. Item 1
(size-band re-validation) is next. **The rest of this entry's items are UNCOMMITTED in the
working tree** (7 files, ~379 lines; `validate.py` is the only production module touched) and
nothing is deployed to `/opt/shortlist` — the daily push still runs the pre-change behaviour,
WSB included.

---

## Maintainability sweep — sources split + daily run() done; remaining follow-ups (2026-07-24)

Two behavior-neutral refactors shipped: `src/shortlist/data/sources.py` (1,639-line god
module) → a `data/sources/` package, one module per Source (PR #148; surface pinned by
`tests/test_sources_surface.py`), and `scout/daily.py::run` extracted into named phase
helpers (`_build_signals_and_statuses`/`_scan_discovery`/`_run_boosters`; 218 → 163 lines,
PR #149). Both verified via full suite + byte-identical `--demo`. Specs/plans live under the
gitignored `docs/superpowers/{specs,plans}/2026-07-2{3,4}-sources-package-split-*`.

Still open (lower priority):
- **`scout/signals.py` (1,059 lines)** — candidate package split (many signal classes in one
  file), same one-module-per-thing pattern as the sources split. Lower urgency: the classes
  are already cohesive.
- **Optional guardrail:** add ruff `C901` with a `max-complexity` (or a soft line ceiling) so
  these mega-functions can't silently regrow after a split. Fits the "curated for signal" ruff
  config; its own small change, not bundled with a refactor.
- **Cosmetic nits from the sources-split review** (not worth churn-risk on their own; fold in
  next time these files are touched): a stray `# --- helpers ---` section header was dropped;
  a few docstrings/comments now cross-reference symbols that moved to sibling modules.

**Status:** OPEN — only the lower-priority `signals.py` split + `C901` guardrail + cosmetic
nits remain; the two high-value targets (sources, daily run) are shipped.

---

## Code-quality sweep — 5 refactors measured and deliberately NOT taken (2026-07-21)

Branch `chore/code-quality-sweep` (PR #145; suite 2070 green, ruff clean, `shortlist
--demo --json` byte-identical to main) was a behavior-neutral parallel-agent pass. Each
item below LOOKED like obvious duplication and was rejected on inspection — recorded so
the next sweep (human or agent) doesn't re-derive the same "obvious" fix and get it wrong:

1. **`assemble_eightk_events` / `assemble_buyback_events` (`scout/backfill.py`)** — the
   buyback docstring says it "Mirrors assemble_eightk_events MINUS the item-set match",
   which reads like a merge waiting to happen. It isn't: the negative leg skips the
   quality drops entirely, buyback carries a `phrase` field, and junk-suffix timing
   differs. Merging would silently change **which events enter a measured cohort** — i.e.
   invalidate a committed audit verdict without failing a test.

2. **`bridge.py:snapshot_to_metrics` (~280 lines)** — an order-dependent pipeline, not a
   long function. Gov-contracts materiality reads `m.revenue` set earlier; the SUE decay
   anchor depends on `snap.events` computed later than `snap.earnings`. Splitting into
   per-section helpers risks reordering an interleaved derivation.

3. **`extract_financials` / `panel_to_metrics` (`providers/_edgar_facts.py`,
   `_xbrl_facts.py`)** — long but already banner-segmented 1:1 to `StockMetrics` fields;
   the risk of a silent transcription bug in a numerics extraction path outweighs the
   readability gain.

4. **Four-way `_load` / `_load_names` double-checked-lock wrapper (`data/sources.py`)** —
   `FinraSource`/`WsbSource`/`GovContractsSource`/`LobbyingSource` share the shape but
   differ in attribute names and bodies; genericizing needs a mixin across 4 classes for
   ~5 lines each.

5. **`GovContractsSource.fetch` / `LobbyingSource.fetch` (~90 lines each)** — pagination
   loops threading several accumulators (`primary_amt`, `recipients`, `ttm`/`prior`); no
   clean extraction seam without risking accumulator-ordering bugs.

Also parked: **`edgartools` `standard_concept` alias lists stay untouched** (version-
sensitive — bucket names drift across library releases and have broken accruals before).

**Status:** All five are deliberate no-ops, not backlog. Revisit only with a specific
reason and a measurement plan; items 1–3 in particular need evidence, not tidiness.

---

## Data audit — 4 fixes shipped; statements-merge data loss + leverage-axis re-measure deferred (2026-07-20)

Branch `fix/data-audit-2026-07-20` (PR #144; suite 2001 green, ruff clean) ships four
audit fixes: (1) `net_debt_to_ebitda` abstains on EBITDA ≤ 0
on BOTH paths (was sign-flipping so a leveraged money-loser displayed as net cash and
topped the inverted backtest leverage axis); (2) `fmp.fetch_insider` was a documented-but-
dead knob — the paid 402 endpoint burned ~1 of ~13 FMP quota calls/ticker on every fetch,
now honored; (3) FMP insider netting is now actually 183d-windowed (was "last 60 txns,
any age" labeled `net_value_6m`, and an all-stale list built a zero-valued `Insider` that
would claim the txn group over EDGAR in `_merge_insider`); (4) CLAUDE.md `daily_push`
doc drift (armed 2026-06-29, docs said OFF). Deferred follow-ups, by impact:

1. ~~**FMP-won statements silently drop every EDGAR-only field**~~ — **FIXED 2026-07-30.**
   Resolved by option (b): a bespoke `_merge_statements` (`data/models.py`) year-joins the
   EDGAR-only fields onto the FMP-won spine instead of discarding them. Design +
   verified consequence chain: `docs/STATEMENTS_MERGE.md`; plan:
   `docs/PLAN_STATEMENTS_MERGE.md`. The `dilution` flag can now fire on FMP-covered names.
   **Residual:** already-persisted accumulation snapshots stay degraded — there is no
   retroactive repair, so the store is complete only from the deploy date forward.
   Original text: `statements` is a
   whole-source pick-first merge and `fmp` precedes `edgar`, so for exactly the
   well-covered (non-402) names the merged snapshot loses `diluted_shares`,
   `diluted_eps`, `fiscal_period_end`, `total_assets`, `asset_growth`, `accruals`, and
   the §5 financing legs that EdgarSource fetched anyway. Consequences: the
   ON-by-default **`dilution` flag can never fire for FMP-covered names**
   (`share_count_cagr` None), `eps_cagr_ps` inert, and the accumulation store
   (fmp,finnhub,edgar) is persisting snapshots whose §3/§5 measurement inputs are
   missing for FMP-covered names — degrading future snapshot-replay measurement.
   Fix options: (a) extract the fields from FMP's ALREADY-FETCHED payloads (income
   statement carries diluted EPS/weighted-avg diluted shares/`date`; balance carries
   totalAssets; cash-flow carries dividends/repurchases/debt rows — /stable/ field
   names need LIVE verification first, the repo rule); or (b) a bespoke statements
   merger that backfills only the internally-aligned EDGAR pieces (the pre-computed
   scalars + the coherent `diluted_eps`/`diluted_shares`/`fiscal_period_end` triple).
   CAREFUL either way: `piotroski_f` and `_financial_series` align series by LIST
   POSITION — never positionally mix two sources' series in one Statements.
2. **Re-measure the `net_debt_to_ebitda` axis post-fix**: every prior IC run (incl. the
   2026-07-11 combined-universe "leverage tilt NOT earned" verdict) scored
   negative-EBITDA names at the TOP of the inverted leverage band (they read as net
   cash). The contamination is worst in the smallmid/combined universes where
   money-losers are common. One re-run on both committed universes before treating the
   axis as permanently dead — the verdict may stand, but it was measured on polluted data.
3. Minor parked observations: the `pe_ttm` fallback accepts negative EPS (harmless —
   `pe_vs_history()` guards `> 0`, and pe_ttm isn't in `--json`); `bridge._close_near`
   has no max-gap bound (a short monthly history can pair a fiscal end with a
   months-away close); `Fundamentals.operating_margin`/`current_ratio` and
   `Statements.total_equity` are extracted but consumed nowhere on the harness path;
   WSB `upvotes`/`rank_24h_ago` are captured but unused.

**Status:** item 1 shipped 2026-07-30 (`docs/STATEMENTS_MERGE.md`); item 2 (the
`net_debt_to_ebitda` re-measure) is what remains — one backtest command per universe.

---

## 13D escalation pack shipped — backfill run pending (2026-07-18)

Branch `feat/13d-escalation-pack` (worktree, review pending — not yet merged): shipped
`EdgarStakeIncreaseSignal` (`edgar_13d_stake_increase`, OFF at weight 0.5, measure-first)
+ `scout/stake.py` pure percent-of-class leaf (XML tier → raw-HTML tier → text tier;
max-of-coverpages; abstain-never-guess) + a `--signal 13d-a` backfill leg with a
run-level stateful chronological assembler. Pre-registered:
`preregister/edgar_13d_stake_increase.yaml` (POSITIVE, K=3m, window 2022–2025,
blocks≥8, frac 0.90, as_of 2026-07-18). Design + verified facts:
`docs/superpowers/specs/2026-07-17-13d-escalation-pack-design.md` (gitignored, local
copy only — see the CLAUDE.md section for the durable summary).

1. **Operator runbook — production backfill + validate (not yet run):**
   ```
   uv run --extra edgar shortlist-scout backfill --signal 13d-a \
       --start 2022-01-01 --end 2025-12-31
   uv run --extra edgar shortlist-scout validate \
       --backfill scout/backfill/13d-a-2022-01-01-2025-12-31.jsonl --json
   ```
   Run **outside 21:15–23:00 UTC** (SEC EDGAR maintenance window); the runner's `df`
   disk preflight aborts below 8 GB free. Serial + rate-limited by design, and
   **resumable** (re-run reports `written_this_run=0` once complete) — safe to split
   across sessions if it doesn't finish in one sitting. 2022-01-01..2025-12-31 IS the
   pre-registered window; don't widen or narrow it post hoc.
   **Expectation-setting:** the buyback/8-K precedent means a KILL-shaped verdict
   (negative or INSUFFICIENT FF3 alpha) is a live, even likely, outcome — the signal
   stays OFF either way with the evidence committed under `docs/audits/`. Known,
   already-diagnosed non-issues the evaluator will see: a legacy-cover-page parse rate
   well under 100% (~93% post html-tier-fix on the 2022-23 spot-check sample) with a
   couple of legitimate `ZERO_PERCENT_ONLY`-style abstentions (a holding-company-chain
   entity disclaiming ownership at that layer — correctly dropped, not a bug); and the
   population-scope caveat (backfill's measured cohort is slightly broader than what
   live scanning would ever emit, since backfill resolves tickers PiT while the live
   walker drops unresolvable-ticker rows before baselining).
2. **Deferred from the design spec §7** (recorded, not scheduled): a stake-**decrease**
   / exit negative-context signal; reweighting the initial-13D live strength by
   stake-%  (needs ledger data first); a generic `include_amendments: true` config
   (non-increase amendments stay dropped in v1).
3. **Repo test suite is Python-minor-version sensitive:** a fresh 3.11 venv fails
   `test_block_bootstrap_ci_*` on a floating-point boundary that a 3.13 venv doesn't
   hit (surfaced setting up this worktree). Consider pinning the dev environment via a
   `.python-version` file so a fresh clone doesn't hit a spurious local failure.

**Status:** MERGED as #141 (HEAD `2257646`). **Backfill + validate COMPLETE (2026-07-19/20).**
Run: 2026-07-19 13:45→18:44 UTC (**4h59m**, `rc=0`) on `/opt/shortlist`, 1422 events written
to `scout/backfill/13d-a-2022-01-01-2025-12-31.jsonl`; validate `rc=0`, digest artifact
`scout/validate-latest.json` written. (Op note: box had NO warm `sec_xbrl` and sat at 7.27 GB
— under the 8 GB preflight floor; cleared the shared uv cache to proceed. Disk finished at
~7.5 GB, so **a re-run would abort the preflight until space is freed again**.)

**VERDICT — `INSUFFICIENT` on both cohorts, but KILL-shaped (expected sign was POSITIVE):**
- raw: alpha **−1.99%/mo**, CI [−2.95%, −0.86%] (entirely negative), IR −1.92, blocks 17,
  measurable **0.72 < 0.90 floor** ← the INSUFFICIENT trigger.
- scored/gated: alpha **−4.39%/mo**, CI [−5.90%, −2.79%], IR −3.22, blocks 16, measurable
  0.938 clears the floor but the **2023 vintage stratum is 0.85** ← the trigger there.
- `n_immature: 0` (all matured — first verdict is canonical, not INTERIM); `sensitivity_flip:
  false`; evaluator self-labels "SYNTHETIC cohort — rank/KILL only (M1)". Signal **stays OFF**.
- Within-cohort double-sort is positive (spread +1.61%/mo, CI [0.11%, 2.93%]) but **both legs
  are negative** (high IR −1.24 / low −1.86) — ranking carries some info, level is still bad.

**Known caveats to carry into the audit doc (do not lose):**
1. **`delisting_by_reason` came back EMPTY** — the prereg's `delisting_return: -0.55` was
   never applied; the 393 unmeasurable (327 `no_price_series`) were **dropped, not imputed**.
   The drops are **non-random and skew toward ACQUISITIONS** (NLSN/MYOV/MTTR were takeouts —
   the *successful* activist outcome), so the missing 28% plausibly biases measured alpha
   **DOWNWARD**. The negative result may overstate how bad the signal is; worth a
   delisting-imputation sensitivity re-run before treating −4.4%/mo as the true level.
2. **4 out-of-window events** dated 2026-01-02 (SCOR×3, TTSH) past `window_end` — 0.28%,
   immaterial to the verdict, but a chunk-boundary overshoot worth a bug note.
3. **64 excess records / 48 duplicate `(ticker, event_date)` keys** (e.g. CRVW ×4 on
   2023-02-02) with `meta.adsh` **None** on backfill emissions (unlike live) — likely
   several filers per subject/day; double-counting understates standard errors (block
   bootstrap only partly mitigates) and `adsh` being null blocks dedup auditing.

**Evidence COMMITTED (2026-07-20, #142):**
`docs/audits/2026-07-19-13d-a-stake-increase-backfill-verdict.md` is now the canonical
record (full verdict tables, five caveats, repro notes). The caveats above are the short
form — **cite the audit doc, not this entry**. Two code bugs it records are still open:
the chunk-boundary overshoot (caveat 2) and null `meta.adsh` on backfill emissions
(caveat 3).

**Deferred decision — do NOT wire a "KILL" config comment (revised 2026-07-20).** The
handoff script said to point config at a kill if KILL-shaped. Hold that, or word it as
"measured INSUFFICIENT, stays off pending delisting-imputation sensitivity". Caveat 1 above
(dropped names skew toward ACQUISITIONS → alpha biased DOWNWARD) means the defensible claim
is **"no evidence to enable,"** NOT "proven value-destructive." The signal is already OFF at
weight 0.5, so the comment changes no behavior — asserting a clean kill would overstate the
evidence. Optional follow-on: delisting-imputation sensitivity re-run to pin the true level
(**needs disk freed first — box is ~7.5 GB, under the 8 GB preflight floor**).

## Snapshot-replay path is ready to un-gate; SUE not yet measurable (2026-07-18)

Checked the accumulation store (`/opt/shortlist/state/snapshots`) while chasing the
SUE/Lazy-Prices validation reminder. Findings:

- Accumulation has run cleanly since **2026-06-22** (~29 names/day). The mega-caps
  (AAPL/MSFT/NVDA/AMZN/META) now hold **26 daily snapshots — past the ≥24 floor**, and each
  carries the SUE inputs (verified live: `recent_surprise_pcts` = 4 quarters, clears the ≥3
  σ-guard; `last_surprise_pct`; the decay anchor). `SnapshotSignalSource` + `sue_score` are
  fully wired behind the gate.
- **`--source snapshot` is still hard-gated** by a stub in `backtest/cli.py:~423` that
  `return 2`s with "no organic history exists yet" — written when the store was empty.
  Follow-up: replace it with a real ≥24-snapshot store-history check so the path activates
  automatically (correctness-by-construction; smoke-test end-to-end). Small, no verdict.
- **SUE is NOT measurable today regardless of the gate** — the earliest snapshot is only
  ~26 days old and forward returns come from `PriceHistory.forward_return(T, horizon)`, so
  no forward window has closed yet. First ~1-month points close early Aug; a prereg-grade
  verdict (≥8 non-overlapping blocks) is a late-2026-into-2027 proposition. Just needs
  calendar time — keep accumulating.
- **Lazy-Prices (`filing_text_change`) can never validate on this path** — full filing text
  was deliberately kept out of the snapshot (EdgarSource fetches Form 4 + financials +
  filing-index only). Already noted below (2026-07-09 item #5) as a structural no-op; not a
  waiting game.

**Status:** open — un-gating the snapshot path is the only actionable prep; the SUE verdict
itself is blocked on calendar time, not code.

---

## Buyback backfill KILL + combined-universe XBRL IC run (2026-07-11)

Branch `fix/buyback-prereg-slug` (local, unpushed — operator to push/PR/merge):

1. **Slug fix shipped**: `preregister/edgar_buyback.yaml` → `edgar_buyback_auth.yaml`
   (pure git-mv rename; validate derives the slug from the event signal
   `edgar:buyback_auth`, so the old name could only ever yield INSUFFICIENT
   "prereg missing"). `_BACKFILL_SPECS` slug updated; new invariant test pins
   `spec["slug"] == _slug_for_signal(spec["signal"])` + file-exists for ALL four
   signals. Suite 1992 green, ruff clean.
2. **Buyback originator KILLED by its pre-registered cohort** (588 events 2022–2025,
   K=3m): scored/gated cohort FF3 alpha **−0.84%/mo, 90% CI entirely negative**
   [−1.80%, −0.005%], 16 blocks, frac 0.96, no delisting flip. Raw cohort
   INSUFFICIENT (frac 0.89 < 0.90) but same sign. The ILV/Peyer-Vermaelen drift did
   not survive this funnel — same outcome as edgar_8k. Stays `enabled: false`;
   committed evidence of record: `docs/audits/2026-07-11-buyback-backfill-kill.md`
   (the raw `scout/backfill/*.json` artifacts are gitignored).
3. **Combined-universe (231-name) XBRL IC run** — first trust-floor-passing
   fundamental ICs at h=1/3/6 (breadth 68–163; h=12 stays EXPLORATORY, 16 periods).
   Results doc: `docs/superpowers/specs/2026-07-11-combined-universe-xbrl-ic-results.md`
   (local). Verdicts: **value complex corroborated** (value_fcf_yield t=3.12/2.56/2.68);
   EV/EBIT don't-ship reconfirmed (corr 0.552, no incremental IC); **leverage tilt NOT
   earned** (net_debt_to_ebitda t≤1.7 raw, ≤1.24 residualized — the 07-05 exploratory
   numbers don't generalize; drop from the wiring queue); share_count = top future
   candidate (XS t 1.5–1.9 sub-bar, but bottom-bucket dilution penalty −7 to −26pp);
   shareholder_yield mixed (XS +2.08 but bottom bucket outperforms) stays OFF.

Follow-ups, by urgency:
1. ~~**accruals re-measurement**~~ — **RESOLVED 2026-07-18.** This item was already
   superseded the day after it was written: the 2026-07-12 audit disabled the leg
   (`accruals: false`, #138) on the reproducible universes, and the original 195-name
   broad universe it asked to re-measure on is **permanently unreproducible** (its
   composition + results doc were gitignored and are gone). Re-ran both reproducible
   universes on current code 2026-07-18 — largecap +0.013→+0.048 (XS t ≤ 1.46, sub-bar;
   only a TS t=2.62 @h6), combined flat-to-negative — **reproducing the 07-12 table
   bit-for-bit.** Verdict stands DISABLED; nothing left to measure. Reproduction appended
   to `docs/audits/2026-07-12-accruals-leg-disable.md`.
2. Operator: push branch `fix/buyback-prereg-slug` (+ PR/merge), then the still-open
   deploy of HEAD to /opt/shortlist (13F originator not yet live; see 2026-07-09/10
   entries below).
3. Consider a `dilution`-flag threshold review instead of the share_count scored leg
   (the payoff is tail-concentrated, which suits a flag/screen better than a ranker).
4. ~~**Prereg tamper-check is path-based, not content-based.**~~ — **RESOLVED 2026-08-03**
   (`f36c94c`, `docs/EVALUATOR_CORRECTNESS.md` §1). `verify_untampered` now walks
   `--first-parent --follow` and compares **parsed YAML**, taking the oldest contiguous match;
   `edgar_buyback_auth.yaml` correctly dates to **2026-07-09** again (was 2026-07-12, its
   `git mv`), matching its own `as_of:`.
   **A far more serious defect was found while fixing this one, and it was NOT recorded
   anywhere:** `load_prereg` read the **working tree** while `verify_untampered` only checked
   the path's last commit time, so **an uncommitted edit to a pre-registered threshold passed
   the gate**. Demonstrated live — editing `min_measurable_frac` 0.90 → 0.10 on disk gave
   `load_prereg -> 0.1` and `verify_untampered -> (True, 'ok')`. The entire anti-p-hacking
   guarantee was defeatable by not committing. `load_prereg` now parses
   `git show HEAD:<path>`, so there is no worktree gap to detect at all. (An intermediate
   design that *detected* divergence with `git status` was defeated in review by
   `git update-index --assume-unchanged` — detection was the wrong shape.)

**Status:** open — items are follow-ups; the session's builds/measurements themselves are done.

---

Shipped (commits `f698bf6..52b979c`, full suite 1919 green): **`edgar_13f`** marquee-fund
new-position cloning (7 live-verified fund CIKs, CUSIP→ticker via SEC FTD files +
name fallback in `scout/cusip_map.py`; DEFENSIBLE prior, **ON at weight 1.0**) and
**`edgar_buyback`** 8-K repurchase-authorization discovery (EFTS phrase query, measured
phrase precision **29/30 ≈ 97%**; DEFENSIBLE prior but **OFF at 0.5** on the 8-K
measure-first precedent). Design spec (local, gitignored):
`docs/superpowers/specs/2026-07-09-thirteenf-buyback-originators-design.md`. Post-merge
multi-agent review found 16 defects (9 fixed in `c871487`); a second review of that fix
commit found 12 more half-closed gaps, all fixed in `52b979c`. Follow-ups, by urgency:

1. **Deploy**: git pull → `install_opt_shortlist.sh` → restart `shortlist-bot`; then watch
   the first 22:30 UTC run for the two new `available()` lines + `edgar:13f_new_position`
   firehose events. Note the 13F burst behavior: first live sessions process 3 filings/day
   (carry-over) until all 7 funds' latest 13F-HRs are seen; next natural burst mid-Aug.
2. **Buyback backfill** (the only path to enabling it): `shortlist-scout backfill --signal
   buyback` + `validate` against `preregister/edgar_buyback_auth.yaml` (POSITIVE expected, K=3m,
   2022–2025). KILL-shaped ⇒ stays off (the edgar_8k precedent).
3. **13F deferred**: PiT CUSIP symbology for a backfill cohort (live FTD files leak
   post-event symbols); material-adds/exits; per-fund attribution is now possible from the
   firehose (`meta.fund_cik`/`adsh` added in `c871487`).
4. Commits are **local-only** — push (and PR if desired) pending operator action.

**Status:** open — item 1 is the standard deploy flow; 2 is a one-command measurement gate;
3 is future work.

## Session follow-ups — breadth fix (#119) + 8-K stack (#120) shipped (2026-07-07)

Both features merged and deployed (editable install picks the code up; effect from tonight's
timers). Loose ends, roughly by urgency:

1. ~~**Operator (sudo) step pending:**~~ — **DONE (verified 2026-07-09):** the live
   `shortlist-accumulate.service` ExecStart now carries `--sources fmp,finnhub,edgar`
   (EDGAR enrichment active). Original item: `sudo SHORTLIST_ACCUMULATE=1 bash deploy/install_opt_shortlist.sh`
   — rewrites the accumulate unit with `--sources fmp,finnhub,edgar` (EDGAR statements/SIC/
   Form-4 for the FMP-quota-gated names). **The accumulate block is gated on
   `SHORTLIST_ACCUMULATE=1`** — a bare installer run (done 2026-07-08, which did restart the
   bot onto current code) skips it, verified: the unit's ExecStart still lacks `--sources`.
   Until then the breadth fix still works (2026-07-07 run saved all 42: captured=16 thin=26,
   verified) but without EDGAR enrichment.
2. **Watch the first 22:30 UTC scout run:** the veto's 30-day cold-start self-heal sweeps
   ~100–180 EFTS pages (~30–60 s) and firehose-logs the initial `edgar:8k_negative` cohort
   (cap raised to 400 so it lands intact). One journal check.
   **Update 2026-07-08:** the first cold start FAILED loudly-but-safely (stale-state note in
   the report, run delivered) — root-caused to bursty EFTS 500s outlasting the ~3 s retry
   window on 40+-page crawls; fixed by #122 (retry budget 2→5, rides out ~23 s bursts) and
   live-verified on the exact failed window (4,981 rows; day cache pre-warmed on the VPS),
   so tonight's sweep should complete. Still worth the one journal check.
3. **Breadth re-check ~July 20:** per-date saved counts should now be ~40 (vs the 30 floor);
   confirm via `shortlist-accumulate status` (it now reports both floors + SUE breadth) once
   ≥24 post-fix dates accrue. The pre-fix thin dates (≤23 names) are permanently thin.
4. **Weekend finality-vs-cursor watch item (8-K veto):** the EFTS day-cache freezes a day as
   FINAL by *calendar* fetch-age while the sweep cursor lags by session days — if EFTS
   indexing lags in *business* days over a weekend, a late-indexed Friday filing could be
   permanently missed. Look at real weekend data after a few weeks before trusting the
   lookback edge.
5. **Lazy-Prices axis is a no-op regardless of the breadth fix** — `filing_text_similarity`
   is never populated by the daily collector (research-layer only); measuring it needs a
   collector change to compute EDGAR text similarity into the snapshot. Separate feature.
6. ~~**SUE decay runs systematically fast**~~ — **FIXED 2026-07-09** (entry at top): the
   root cause turned out deeper (free-tier calendar has NO history at all); anchor now
   rides the EDGAR 10-Q/10-K filed date.
7. ~~**Test isolation nit**~~ — **FIXED 2026-07-09:** `monkeypatch.chdir(tmp_path)` added to
   the run()-level veto byte-identical test (the `test_scout_backfill_cli.py` idiom).
   Residual observation: other run()-level tests (`test_scout_daily_research_gate`,
   `scout/test_digest_fmp_toggle`, `scout/test_fixes`, `scout/test_daily_push_flag`,
   `scout/test_orchestrator_integration`) still read the repo-relative
   `scout/validate-latest.json` — same benign-today class; apply the same one-liner when
   next touched.

**Status:** open — item 1 is a one-command operator step; 2–4 are observation gates; 5–7 are
future work.

## Snapshot-replay composite suppression rate is unmeasured (guard residual) (2026-07-07)

The new replay guard (`backtest/signals.py:SnapshotSignalSource.observe`, #`712e6e5`)
suppresses the emitted `composite` axis for any card scoring below
`validity.min_scored_weight` (0.25 prod / 0.34 default). Real accumulated `fmp`/`finnhub`
snapshots systematically lack price/insider legs, so a plausibly-healthy name (e.g.
quality+momentum only, confidence ~0.28) is suppressed by design — but the actual
**suppression rate on the live store has never been measured**. Follow-up: once ≥24
dates accrue, count suppressed-vs-emitted composites over the store before trusting any
composite-axis replay ICs; if the rate is high, the floor-vs-provenance-gating decision
(spec §5, `docs/superpowers/specs/2026-07-07-accumulation-breadth-fix-design.md` —
gitignored/local) should be revisited WITH that data.

Status: open — measure after ~2026-07-31 (24-date threshold).

## H2 correction — pre-registration anchor (verbatim spec text) (2026-07-06)

The immature-denominator correction's legitimacy rests on the 2026-07-01 registered spec
(local/gitignored per convention); the two load-bearing clauses are excerpted VERBATIM here
so the argument survives in the committed repo alone:
> §6.1: "Include an event in the cohort only when ≥ K forward data exists (H2)."
> §12: "fixed-horizon (H2): a 95-day-old event is excluded from the K=12m cohort (not measured early)."
> §6.1 (measurable, enumerated): "Non-measurable = no usable price series at all, or an
> unresolvable/ambiguous delisting." — calendar immaturity is neither.
Adversarially reviewed before implementation (SOUND-WITH-FIXES; B1 leak-proof predicate,
INTERIM labeling vs registered `verdict_as_of`, both-fractions transparency all mandated by
that review). **On lifting 2025 coverage before verdict_as_of: adjudicated WAIT** — a
targeted coverage push aimed at one vintage's floor is outcome-directed curation, the exact
pattern pre-registration exists to prevent; the vintage matures on its own by 2026-12-31.

## Production 13D backfill run — PAUSED, ready to fire (2026-07-05)

The raw-cohort 13D backfill machinery is **merged and live-verified** (#109 — walker n=22/3d
smoke, one-week e2e, `validate --backfill` returns the honest INSUFFICIENT at tiny n). The
production run itself was deliberately **paused** (hours of rate-limited fetching on the VPS)
— it is the last step before the harness's first real historical verdict on
`edgar:activist_13d`. **Update (Plan 3b, 2026-07-05):** `score_events` defaults to true, so
the production run below now reconstructs BOTH cohorts (raw + scored/gated) in the SAME
pass — no separate re-run needed to get the `scored_gated` verdict once it's fired. When
ready:

```bash
# serial + resumable (re-run the same command to resume); ~hours at ≤5 req/s SEC.
# Run OUTSIDE 21:15–23:00 UTC (shortlist-accumulate 21:30 + shortlist-scout 22:30 timers).
uv run --extra edgar shortlist-scout backfill --signal 13d --start 2022-01-01 --end 2025-12-31
uv run --extra edgar shortlist-scout validate \
    --backfill scout/backfill/13d-2022-01-01-2025-12-31.jsonl --json
```

- **The window above IS the pre-registered window** (`preregister/edgar_activist_13d.yaml`
  `window_start`/`window_end`, registered 2026-07-05) — the coordinator exact-matches it; any
  other window (including a subset) runs fine but is loudly + permanently labeled
  `window_not_preregistered` (deliberate: a different window is a different analysis).
  K=12m → expect INSUFFICIENT until enough independent blocks accrue; that is the design.
- Disk check before firing: companyfacts cache ≈ 2.5 MB × unique CIKs (up to ~8–13 GB at
  2600 CIKs) under `.cache/sec_xbrl` — `df -h` first (38 GB box shared with the live bot).
- Before a long run, optionally seed `symbology._OVERRIDES` for known rename-near-event
  cases (documented example: CIK 1823575, L&F Acquisition → ZeroFox de-SPAC 2022-08 — resolves
  the stale pre-rename ticker → honest non-measurable + `low_confidence` flag).
- Read the run summary's `by_reason` / `by_vintage` / `low_confidence` / `failed_chunks`
  blocks before trusting the fraction; `failed_chunks` → just re-run (resume skips done work).
- After the run: the verdict feeds the digest-wiring step (Phase-2 plan 5) and sets the
  precedent for the FINRA audit→leg.
- **Discrepancy to adjudicate before trusting a verdict:** the parent spec (§7) says the
  independent-block gate should require **≥8** blocks; the committed
  `preregister/edgar_activist_13d.yaml` pins `min_independent_blocks: 2`. Left UNCHANGED
  here (Plan 3b Task 6 — silently tightening an inference parameter after the fact is
  exactly what the tamper guard exists to prevent) — needs a human call on which value
  governs the real run.

**Status: RUN COMPLETE (2026-07-06 05:50 UTC, supervised).** 3,645 events over the full
registered window; one kernel-OOM incident mid-run (box had no swap; 2GB swapfile +
swappiness=10 added and shortlist-bot restarted to reclaim RAM — both operator-applied;
runner hardened: zombie detection, RSS cap, memory gate). **First verdict
(`validate --backfill`, 2026-07-06): INSUFFICIENT on BOTH cohorts** — raw measurable
fraction 0.624 and scored_gated 0.835, both below the pre-registered 0.90 floor, so the
evaluator refuses an alpha verdict (the survivorship gate working as designed; the
non-measurable tail is SPAC-era junk with no Yahoo history). **Notable non-evidence
observation:** the §6.2 double-sort's high-minus-low composite spread is POSITIVE
(+2.97%/mo, CI [+2.73,+3.17], 4 blocks, n=996/996) — inside the 13D cohort the scorer
ORDERS winners even though the cohort itself carried negative drift; labeled
rank/KILL-only + provisional per M1. Verdict artifacts: `scout/backfill/verdict-13d-2022-2025.json`
+ `scout/validate-latest.json` (flows into tonight's digest automatically). Next reads:
adjudicate the blocks-gate discrepancy (spec-8 vs prereg-2) and whether a
measurability-improved re-run (symbology._OVERRIDES seeding, delisting-classified tail)
can lift the fraction toward the floor.

**Update — H2 immature-denominator correction + re-verdict (2026-07-06, adversarially
reviewed, INTERIM).** The above first verdict used the wrong H2 denominator: the parent
spec (§6.1/§12) says an event only enters the K=12m cohort once it has ≥K forward data —
"a 95-day-old event is excluded, not measured early" — but `measure_cohort` was instead
counting immature (not-yet-matured) events as non-measurable *survivorship* losses,
applying the "never silently drop" rule written for delisting/no-series failures to a
calendar fact instead. Design:
`docs/superpowers/specs/2026-07-06-immature-denominator-fix-design.md` (adversarial
review verdict: SOUND-WITH-FIXES — legitimacy affirmed on the spec text; B1 tightened
"immature" to require a real entry price + unelapsed horizon, so a recent no-series name
can never be relabeled immature to dodge the floor; I1 added a pre-registered
`verdict_as_of: 2026-12-31` with a permanent INTERIM label on every verdict issued before
that date). Both fractions, reconstructable from the persisted verdict JSON
(`n_measurable/n_events` = old pooled; `n_measurable/n_selected` = new mature-only):

| cohort | old pooled (n_meas/n_sel) | new mature-only (n_meas/n_sel, +immature) | floor |
|---|---|---|---|
| raw | 0.624 (2275/3645) | **0.697** (2275/3262, +383 immature) | 0.90 |
| scored_gated | 0.835 (675/808) | **0.938** (675/720, +88 immature) | 0.90 |

Both numbers land close to the design's pre-registered prediction (raw ~0.70 still fails;
scored ~0.94 clears the aggregate floor) — the fix behaves as designed, and the
raw-vs-scored **asymmetry holds**: immaturity-exclusion alone cannot rescue the raw
cohort (its shortfall is real survivorship loss, correctly still counted), only the
already-cleaner scored cohort.

**New verdicts (both INTERIM — before registered `verdict_as_of` 2026-12-31), both still
INSUFFICIENT, but the scored cohort now fails for a DIFFERENT and more specific reason
than before:**
- **raw**: INSUFFICIENT — `measurable fraction 0.70 < floor` (unchanged reason; the
  correction narrows the gap but does not close it).
- **scored_gated**: the pooled/aggregate mature-only fraction (0.938) now *clears* the
  0.90 floor — this is the harness's first cohort whose top-line fraction is no longer
  the blocker. It is still INSUFFICIENT, though, because the pre-existing (untouched by
  this fix) **R-A4 vintage-stratified guard** trips on the newest mature vintage: 2025
  alone reads 0.89 (85/96) < floor. Concretely: **the scored 13D cohort's aggregate data
  coverage is finally good enough for a real verdict, but the most recent full vintage
  isn't quite there yet** — a genuine, narrow (1.5pp — needs 2 more measurable 2025 events, 87/96) coverage gap in 2025, not an
  artifact of the immaturity bug. This is a materially different outcome than the
  design's own framing ("clears floor → a REAL verdict, likely KILL given the negative
  alpha CI") — flagging plainly rather than rounding it up to a clean HOLD/KILL: **no
  cohort-level verdict has actually been earned yet**, pending either more 2025 vintage
  coverage or a later re-run once 2025 has more months to mature/resolve.
- The §6.2 double-sort spread is **unchanged and still positive**: +2.97%/mo, CI
  [+2.73,+3.17], 4 blocks, n=996/996 — the scorer still orders winners within the cohort
  even though neither cohort clears to a scoreable verdict. This remains the standing
  counterpoint to the raw cohort's negative alpha, and is the strongest evidence in either
  direction so far.

Artifacts: `scout/backfill/verdict-13d-2022-2025-v2.json` (new, both fractions
reconstructable) alongside the untouched original `verdict-13d-2022-2025.json` (old
pooled numbers, kept for the side-by-side); `scout/validate-latest.json` refreshed and
now carries `n_immature`/`n_events` on both verdicts (confirmed post-run). Design +
review: `docs/superpowers/specs/2026-07-06-immature-denominator-fix-design.md`.
Branch `fix/h2-immature-denominator`. Next reads: is a 2025-coverage push (delisting
classification / symbology overrides for that vintage specifically) worth it before the
canonical `verdict_as_of` date, or does the harness simply wait for 2025 to mature
naturally.

## Prioritization pass — ranked backlog + one net-new item (2026-07-05)

A "highest impact next" review re-affirmed the 2026-07-01 verdict: **finish the
signal-validation harness Phase 2** (entry below — #106 symbology merged; delisting →
13D backfill → FINRA audit → digest wiring is the critical path). Ranked follow-ups
*around* it, mostly pointers to existing entries:

1. **Leverage residualized-IC test (`net_debt_to_ebitda`) — NET-NEW, best independent
   parallel build.** The strongest signal ever measured here (largecap XS-IC +0.127 @12m,
   positive at every horizon in both IC and quantile spread — session memory
   `xbrl-backtest-first-largecap-ic`, 2026-06-15 runs) but **0.54-collinear with the
   scored `growth` axis**, so the standalone IC partly re-encodes growth. The decisive,
   pre-agreed test before any wiring: regress the leverage score on growth+quality
   cross-sectionally, rank-IC the **residual** (a contained backtest diagnostic, the
   `_COLLINEARITY_PAIRS` / EV-EBIT decision pattern). Survives → first evidence-backed
   composite change since accruals/residual-momentum; fails → clean kill. Caveats to
   carry: single-run evidence, survivorship-biased largecap, breadth 27.6 (< the 30
   floor) at h12, smallmid re-run was NULL — so run on both universes.
   **VERDICT (2026-07-05, pre-registered run complete): INCONCLUSIVE — no wiring.**
   Largecap residual IC real + sign-consistent (+0.079 t=2.26 @3m, +0.126 t=2.02 @12m;
   clauses a–e pass) but smallmid residual point estimates near-zero (+0.005 t=0.25 @3m,
   +0.009 t=0.33 @12m) → clause (f) fails; no KILL trigger fires. Axis stays
   measurement-only; re-test deferred (delisting-corrected universe / more h12 breadth).
   Evidence of record: `docs/superpowers/specs/2026-07-05-leverage-residualized-ic-results.md`
   (local/gitignored, raw JSON artifacts alongside).
2. **Verify daily accumulation is actually accruing** (entry 2026-06-21 item 1): the VPS
   timer must pass `--max-tickers 42`; ≥24 snapshots unblock SUE + Lazy-Prices
   measurement. Minutes of operator checking guarding two finished features.
   **VERIFIED (2026-07-06):** timer runs nightly with `--max-tickers 42`; 10 daily
   snapshots accrued per ticker (2026-06-26→07-05) → threshold ~July 20. **Caveat found:**
   only ~25/42 names save per night (FMP-gated names read THIN <50% coverage and are
   skipped) — the snapshot-replay trust floor needs ≥30 names/date, so per-date breadth may
   fall short even once ≥24 days accrue. Re-check the per-date saved count around July 20;
   if <30, options: raise the keyless coverage of the watchlist mix or lower the coverage
   skip-floor for accumulation only (measure, don't guess).
3. **Selection-ledger forward-return analysis** (entry 2026-06-29 item 1): picks accrue
   since 2026-06-30 → first 1m-horizon read ~**early Aug 2026**; calendar-gated, not
   effort-gated.
4. **Gate-impact measurement** (entry 2026-06-26, scope B): gates are entirely
   unmeasured; excused-vs-gated negative-FCF cohort comparison is the first empirical
   look. New machinery → ranks below item 1.
5. **DEF 14A pay-vs-performance axis** (entry 2026-06-28 item 1): quick ECD-tag
   reachability spike before committing anything.
   **SPIKE RESOLVED (2026-07-05): NO-GO on the XBRL path.** Live-verified that SEC's XBRL
   APIs serve only `dei`/`us-gaap` namespaces: the `ecd` PvP tags
   (`PeoActuallyPaidCompAmt` etc.) are absent from companyfacts for AAPL and MSFT, and a
   `companyconcept/.../ecd/...` probe 404s (NoSuchKey). The axis can therefore only be
   built via the snapshot-replay path (accumulate `research/proxy.py`'s edgartools
   `ProxyStatement` PvP extraction point-in-time). Deferred accordingly; no code needed.
6. **8-K discovery originator** (entry 2026-06-29 item 3): one-file `SignalSource` on
   the 13D pattern — more attractive now that the validation harness can measure it.
7. **Paid-FMP Starter flip** (entry 2026-06-30): a money decision + one config key,
   not a build.

**Status:** logged; item 1 is the only net-new build and the recommended next pick once
harness P2 lands (or in parallel — it touches only backtest diagnostics).

## Signal-validation harness — Phase 0 + Phase 1 shipped; Phase 2 next (2026-07-02)

The **highest-impact** build (design v3.1: `docs/superpowers/specs/2026-07-01-signal-validation-harness-backfill-design.md`,
local/gitignored; session memory `signal-validation-harness-project`). Turns the ~10 parked discovery
priors into a measurement flywheel.
- **Phase 0 = PR #104 (MERGED + deployed).** Raw-signal firehose (`scout/firehose.py`,
  `ScoutState.firehose`, config-gated, best-effort) + fixed the XBRL look-ahead H1 (nominal
  `quote[0].close` for `market_cap`/`PE`). Firehose logs from the 2026-07-02 nightly run onward.
- **Phase 1 = the evaluator (`feat/scout-validate-evaluator`, PR pending).** `scout/validate.py` +
  `scout/factors.py` (Ken French FF3) + `scout/preregister.py` (git-blob-hash tamper gate, YAML under
  `src/shortlist/scout/preregister/`) + `shortlist-scout validate` CLI + display-only report section.
  CTP → FF3 alpha (manual stdlib OLS) → block bootstrap (block≥K, effective-n = independent blocks) →
  KILL/HOLD/INSUFFICIENT + IR rank, **never PROMOTE**; alpha-uncomputable → INSUFFICIENT. Built TDD via
  subagent-driven dev, whole-branch review READY TO MERGE, 1496 tests pass.

**§14 spikes DONE (2026-07-02, all GO) → Phase 2 GO and improved.** Key reversals: survivorship is
**correctable** via a Wayback CDX resolver of `company_tickers.json` (+ the filing's own
`dei:TradingSymbol` 2019+ as primary); delisting sign **classifiable** (8-K Item 1.03=bankruptcy /
2.01+5.01=M&A); FINRA archive to 2017. Landmines: CIK-reuse (BBBY→Overstock — key on subject CIK,
resolve ticker via as-of snapshot, never ticker→CIK; guard extends to the ticker-keyed Yahoo price
fetch); FINRA rows have NO CIK (need reverse lookup) + go-dark-heavy → **FINRA gated on a measurable-
fraction audit; 13D is the clean first backfill target.**

**Phase 2 build order (spec §16, 5 plans):** (1) `scout/symbology.py` (dei:TradingSymbol+Wayback, fwd
+reverse, reuse `cik_tickers.build_cik_to_ticker`, rate-limited) → (2) `scout/delisting.py` (Form 25/15
+ 8-K classifier, bankruptcy-overrides-M&A precedence, BBBY/ATVI/TWTR fixtures) → (3) `backtest/
edgar_history.py` + 13D backfill leg → (4) FINRA audit spike → (if pass) FINRA leg → (5) wire verdicts
into the daily digest (**at wiring: `dataclasses.asdict()` the SignalVerdicts + normalize the section's
render_text to list[str]** — documented landmine in `report/viewmodel.py`).

**Status:** P0 (#104) + P1 (#105) merged+deployed. **P2 Plan 1 `scout/symbology.py` MERGED (#106,
2026-07-05)**: Wayback PiT CIK↔ticker resolver — live-for-active / archive-for-
delisted (forward), archive-only (reverse), cached-forever, ~1 req/s. Deep-dive spike validated the
mechanics (found the ≲2019 `build_cik_to_ticker` convention bug → live-for-active sidesteps it; FINRA
OTC ~82% unresolvable → reverse abstention reported); opus plan-review hardened it (owns-client C1,
low_confidence C2, never-raises); **live smoke on real archive.org PASSED** (BBBY recovered, reverse
avoids the Overstock reused-ticker CIK). Whole-branch review READY TO MERGE; suite 1520.
**P2 Plan 2 `scout/delisting.py` COMPLETE** (`feat/scout-delisting`, PR pending): Form 25/25-NSE/15
detect + 8-K item-code classifier (1.03=bankruptcy → Shumway venue partial NYSE −30%/Nasdaq −55%;
2.01+5.01 same-filing=M&A → last-close, no penalty; else unclassified → non-measurable), R-B3
bankruptcy-overrides-M&A precedence, BBBY/ATVI/TWTR fixtures + live EDGAR smoke PASSED (BBBY→
bankruptcy/nasdaq, ATVI→mna), `last_traded_close`/`terminal_price` single-sourced for the Plan-3
coordinator (R-A1: never read a close past the delisting date). CIK-keyed fetcher with a static
guard test (never a ticker-keyed Company lookup). Whole-branch review READY TO MERGE; suite 1543.
**P2 Plan 3 (RAW-cohort 13D backfill) COMPLETE** (`feat/scout-backfill-13d`, PR pending):
`backtest/edgar_history.py` ranged walker + `scout/backfill.py` coordinator (F12 next-session
entry, selected/excluded/sentinel accounting, R-A1 trading-gap guard, per-event classified
delisting terminals, idempotent `scout/backfill/*.jsonl`, month-chunked serial,
`fetch_history_sync` asyncio bridge) + `shortlist-scout backfill --signal 13d --start --end`
+ `validate --backfill PATH` (state-free SYNTHETIC path; `measure_cohort` per-event override
with `use_event_delisting=False` in the sensitivity band — red-green-pinned). **Live-verified
on the VPS**: walker n=22/3d; one-week e2e 6 selected / 2 measurable; `validate --backfill` →
INSUFFICIENT (SYNTHETIC, tiny-n — the honest verdict); RSS 409 MB. Suite 1582. Review loops
caught 2 real bugs (dead async seam, entry÷0) + VPS hardening (sentinel-fetch skip, walker
per-record guard). Known case: CIK 1823575 (L&F→ZeroFox de-SPAC 2022-08) resolves the stale
pre-rename ticker → honest non-measurable + `low_confidence` flag; seed `symbology._OVERRIDES`
before the production run if it matters.
**Operator next: the production backfill run** — e.g.
`uv run --extra edgar shortlist-scout backfill --signal 13d --start 2022-01-01 --end 2025-12-31`
(serial, resumable — re-run to resume; ~hours at 5 req/s; run OUTSIDE 21:15–23:00 UTC), then
`shortlist-scout validate --backfill scout/backfill/13d-2022-01-01-2025-12-31.jsonl`.
**P2 Plan 3b (scored/gated cohort) COMPLETE** (`feat/scout-backfill-scored`, PR pending): the
same backfill coordinator now OPTIONALLY reconstructs a PiT `score()` per event
(`scout.backfill.score_events`, default true) — companyfacts → `extract_panel` →
`panel_to_metrics` + dated closes → `scoring.score()` — filling `gated`/`composite` on the
same JSONL row (additive; `score_events: false` reproduces the byte-identical pre-3b
raw-only file). `validate --backfill` gained a second `cohort_type: "scored_gated"` verdict
(gate-agnostic double-sort over the composite-defined set) that appears only when ≥1 event
has a non-None composite. **Live-verified on the VPS** (same Aug 2022 one-week window as
Plan 3): 6 selected / 3 scored (composites 8.2–63.0); `validate --backfill` correctly
produced a `scored_gated` INSUFFICIENT verdict (n=1 after the gated-False filter, 0
measurable — too thin to be anything else, the honest verdict at this n); RSS 410 MB. Full
suite 1620.
**P2 Plan 4 (FINRA audit spike) COMPLETE — VERDICT: DEFER (2026-07-05).** Ran the v2
protocol (`docs/superpowers/specs/2026-07-05-finra-audit-spike-design.md`, local) on
oracle-prod: 16 sampled settlement cycles (2018–2024, 2017 excluded) × production
`config.yaml: scout.short_interest` jump cohort (`top_n=10`, exact prod kwargs) = 160
tickers; measurability = Yahoo price existence at event date + event+1mo
(`backtest.prices.fetch_history`, confirmed reachable from this VPS — the screener-only
IP-block premise didn't apply). **Pooled measurable fraction 129/160 = 0.806 (90% CI
±0.051)** — clearly below the pre-registered 0.90 bar (shortfall 0.094 > CI half-width),
so DEFER is mechanical, not borderline. Vintage split: 2018–2020 worse (43/60 = 0.717 ±
0.096) than 2021–2024 (86/100 = 0.860 ± 0.057, itself within-CI of 0.90 but moot given the
pooled failure). K-scoped delisting arm never fired (0 cases — all non-measurability was
plain Yahoo 404/no-entry-price, not classifiable corporate events). Separate diagnostic:
reverse ticker→CIK resolution (one shared `Symbology`, norm_symbol-normalized both sides)
= **101/160 = 0.631 pooled** — flat across vintages, no disagreements/low-confidence flags
— caveat recorded that even a passing raw leg would have capped a future FINRA *scored*
cohort well below the raw fraction. Full numbers, per-cycle table, rule walk, and request
accounting: `docs/superpowers/specs/2026-07-05-finra-audit-results.md` (local). **FINRA
leg (Plan 4b) is NOT built** — the discovery-only `FinraShortInterestSignal` stays as-is
(disabled at weight 0.5, ledger-measured); no `edgar_history`-style FINRA plumbing.
**P2 Plan 5 (digest wiring) COMPLETE (2026-07-05, PR pending):** `validate` (live +
`--backfill`) persists `scout/validate-latest.json` (asdict at the boundary, never-raises,
exit-code-safe); the report's `validation` section renders it applies()-gated (render_text
returns list[str] — the documented landmine closed branch-wide; SYNTHETIC markers;
double-sort line; the mandated "display / provisional / survivorship-biased — not
evidence, not advice" label; stale/malformed/null-config all degrade). Byte-identical when
the file is absent. **Signal-validation harness Phase 2 is now COMPLETE end-to-end** —
every build-order item shipped or resolved (Plan 4 FINRA leg deferred at its
pre-registered gate). The only remaining step is the OPERATOR one: fire the paused
production backfill run (entry at top), then `validate --backfill` for the first real
verdict, which will now also flow into the nightly digest automatically.

## FMP-free daily digest shipped + deployed — verify first run / paid-plan flip (2026-06-30)

`scout.daily_push.include_fmp: false` (#100) makes the unattended digest screen on free
sources (EDGAR/Finnhub/Yahoo/FINRA), reserving FMP's 250/day free quota for interactive
`/deep` (the bot's `/screen`+`/deep` keep the full FMP chain). Merged + deployed to
`/opt/shortlist` and the bot restarted (#99 also raised `research.timeout_s` 600→900 for
heavy filers like WDC). Two follow-ups:
- **Verify the first FMP-free digest run** (`shortlist-scout.timer`, ~22:30 UTC 2026-06-30):
  confirm it spends **0 FMP calls**, still ranks all 7 axes, and the "Free-source screen —
  /deep for PEG + analyst targets" caveat renders. A zero-FMP `AAPL` screen was validated
  pre-ship (all axes scored; only `peg` + `upside_to_target` drop).
- **Paid-FMP flip (deferred decision):** if subscribing to FMP Starter (~$22/mo, 300
  calls/min, no daily cap), set `scout.daily_push.include_fmp: true` (or delete the key) →
  digest uses the identical full chain as the bot, no code change.

**Status:** Done + live. Only the first-run verification and the conditional paid-plan flip remain.

## Activist 13D discovery + selection ledger — Phase 2 follow-ups (2026-06-29)

Shipped (#93; CLAUDE.md "Activist 13D discovery + selection ledger", AUTONOMOUS_SCOUT §4):
the `EdgarActivist13DSignal` discovery originator (initial SCHEDULE 13D), the common-stock
CIK→ticker resolver, `quality.py` filters, the selection ledger + excess-over-SPY scoreboard,
and the `scout.daily_push.research: false` digest mode with a `/deep` block. **Discovery-only —
no scored leg** (it ships as a defensible prior; the ledger measures it). Deferred, in priority
order:

1. **Forward-return analysis of the ledger ← the whole point of the ledger.** Once daily picks
   accumulate, analyze the scoreboard: excess-over-SPY hit-rate at 1/3/6/12m and the
   `activist_13d` vs `edgar_form4` cohort split (the `catalyst` field). This converts the
   defensible-prior signal weight (1.5) into evidence and tests the after-close-drift thesis
   (Bebchuk-Brav-Jiang 2015). **Gated on accumulated picks** — arm `scout.daily_push.enabled`
   (+ `research: false` for the lean digest) and let the timer run.
2. **Pre-screen market-cap floor in the funnel.** v1 relies on the post-screen
   `below_min_mktcap` gate + the non-gated `/deep`-block filter, so a micro-cap shell can still
   consume one of the ~10/day FMP deep-screen slots before exclusion. A keyless pre-screen floor
   would protect the budget, but there's no clean keyless market-cap source (needs shares×price),
   so it was deferred (spec §14). Revisit only if budget pressure bites at 13D volume (~4-12/day).
3. **8-K + FINRA short-interest discovery originators.** Two VPS-safe originators to widen the funnel.
   - ✅ **FINRA short-interest jumps — SHIPPED** (`FinraShortInterestSignal`, default OFF; CLAUDE.md
     "Short-interest discovery (scout)"). Ships as a **CONTESTED prior** (adversarial PnL review:
     the jump is the *negative* signal — Cohen-Diether-Malloy; DTC a *stronger* negative predictor —
     Hong et al), so it's a middle-band attention signal at weight 0.5, default-disabled, and the
     selection ledger earns it a weight via a pre-registered promotion/kill rule (≥30 picks, 6m
     median excess-over-SPY ≥ 0). Remaining follow-ups: (a) **promotion/kill measurement** — gated
     on accumulated picks (arm `daily_push` + enable the signal, like item 1); (b) a cleaner
     **fund/ETF universe filter** than the seed `deny_list` (scorer abstention is today's backstop;
     the 5th-letter `*F/*Y/*W/*U/*R/*Q` drop catches OTC/derivatives but not 4-letter ETFs/CEFs);
     (c) **from-zero ramps** (brand-new short positions, currently dropped by `min_prev_short_shares`)
     as a separate absolute-share variant.
   - ⏳ **8-K originator — still deferred.** Curated 8-K item classes (1.01/8.01/5.02) from the SEC
     daily index — a one-file `SignalSource` against the existing interface (+ `daily.py` wiring),
     the `EdgarActivist13DSignal` precedent.
4. **SCHEDULE 13D/A amendment signal.** `scout.activist_13d.include_amendments` exists (off —
   amendments run ~20-46/day and are spammy), but a stake-*increase* amendment is a real
   escalation. A future version could surface only amendments that raise the stake materially.
   Measure before enabling.
5. **Stake-% extraction.** The % owned lives in the filing body, not the index/header, so v1
   doesn't parse it (strength is 0.7 + marquee/co-filer bumps). A larger stake is a stronger
   signal — parse it if item 1's cohort analysis shows it discriminates.
6. **Marquee-activist alias map** (`scout/quality.py:_MARQUEE`) is curated + non-exhaustive (fires
   on a minority of filings by design — substring-anywhere match, narrow filer-name space). Extend
   as new credible activists appear.

**Status:** not started — all deferred at ship. Item 1 is the keystone and is gated on accumulated
daily picks; 2-6 are independent. None are correctness blockers (the feature is complete + reviewed).

---

## §2 price-refinement axes — measured, NONE wired (2026-06-28)

Built three OHLCV-only price signals as **backtest-only measurement axes** on the live-price
`MomentumSignalSource` (the residual_momentum precedent): `pct_to_52w_high` (George-Hwang),
`max_daily_return` (Bali MAX-effect), `vol_scaled_momentum` (Barroso-Santa-Clara). Pre-registered
measurement on **both** bundled universes (largecap-79 + smallmid-152), full 1/3/6/12m grid, with
collinearity kills (≥0.5) and Phase-2 homes fixed in advance. Evidence of record:
`docs/superpowers/specs/2026-06-28-price-signal-bundle-results.md`.

**No config flip earned — all three parked** (the share_count/asset_growth precedent):
- **`pct_to_52w_high`: REJECT** — corr **+0.70/+0.74** vs the scored `price_vs_200dma` leg on both
  universes (a monotone re-skin of trend; EV/EBIT duplication trap); weak/negative IC anyway.
- **`vol_scaled_momentum`: REJECT** — corr **+0.52–0.54** vs scored momentum (duplicates *raw*
  momentum, which is XS-insignificant here); never |t|≥2. NOT a `residual_momentum` twin (+0.21–0.23),
  confirming the de-beta — not the vol-scaling — is residual momentum's edge. A null doesn't refute
  Barroso-Santa-Clara (their result is time-series vol-targeting, not XS alpha).
- **`max_daily_return`: PARK** — the only one orthogonal to existing signals (corr −0.07/−0.10), but
  the inverted-score IC **sign flips across universes** (NEG/significant in largecap — the MAX/lottery
  effect *reverses* in mega-caps; weakly POS in smallmid). Fails "≥1 universe without the other
  contradicting," so even the pre-registered defensive-flag home is unjustified. Revisit only via a
  small-cap-restricted or through-cycle test.

**In-scope fix shipped:** the collinearity diagnostic was gated to `--source xbrl`; now runs on the
`--source momentum` path too (so the residual~momentum + price-axis pairs are actually measured).

**Side benefit:** `residual_momentum` (the live leg) re-confirmed as the only short-horizon XS winner
on both universes (LC +0.023 t2.63, SM +0.025 t3.71). **Status:** done — axes are measurement-only,
no production wiring; Phase-2 deferred (nothing earned it).

---

## DEF 14A proxy — Phase 2 follow-ups (2026-06-28)

The DEF 14A proxy reader shipped as a research-only context line (#90; CLAUDE.md
"Proxy statement (DEF 14A) …", ASSESSMENT_GAPS §3.1). Deferred, in priority order:

1. **`pay_for_performance_alignment` backtest axis.** Unlike the other narrative research
   inputs, the proxy's Pay-vs-Performance table is *structured XBRL* (Item 402(v),
   `ProxyStatement.pay_vs_performance`), so it could clear the rank-IC bar and become a
   scored leg. Caveat: 402(v) uses ECD-taxonomy tags (`ecd:PeoActuallyPaidCompAmt` …) from
   the proxy's own XBRL — **confirm these are reachable from the companyfacts/XBRL backtest
   path** (they may not be; the fallback is the snapshot-replay path once accumulation
   captures proxy facts). Then measure rank IC + collinearity vs `quality`/`insider` before
   wiring or rejecting, like the PREDICTIVE_SIGNALS legs. Unfitted until measured.
2. **Narrative sections (related-party / CD&A).** Highest-value-but-unextractable in v1 (no
   edgartools section splitter; ~350K-char raw text) — needs custom section extraction to
   surface as quote-verifiable haystack text. Heavier build.

**Status:** not started — both deferred at ship; item 1 gated on confirming 402(v) ECD-tag
reachability (or on accumulated proxy-fact snapshots).

---

## negative_fcf excuse — measurement path (scope B, follow-up to #83) (2026-06-26)

#83 populated `fcf_positive` on the XBRL panel (`_xbrl_facts.panel_to_metrics`, with a
stale-FY abstention guard mirroring the bridge), clearing the **field-level** blocker. The
field is now correct on both paths — but the stage-aware `negative_fcf` excuse is **still
unmeasured**: `XbrlSignalSource` emits sub-score axes only and never calls `check_gates`, so
nothing reads `fcf_positive` in the backtest yet.

**Remaining (scope B):** a gate-impact backtest diagnostic — compare forward returns of
*excused* (high-growth) vs. *gated* negative-FCF names to test whether the excuse
(`revenue_cagr ≥ 0.15 ∧ persistence ≥ 0.70`) actually improves returns vs. a blanket gate.
New machinery (the XBRL source would need to evaluate the gate, or a parallel cohort path).
Thresholds stay unfitted priors until then. See `docs/ASSESSMENT_GAPS.md` §2.7.

---

## Broad-universe XBRL backtest — settled three unfitted priors (2026-06-25)

Ran `--source xbrl` keylessly on a new bundled **`smallmid`** universe (158 small/mid-caps,
`backtest/universe_smallmid.txt`) — the properly-powered cross-section the prior large-cap
piotroski null lacked. **No config flip earned.** Cross-sectional rank IC (the |t|≥2 bar):

- **`share_count` (→ `quality.dilution`): NULL on both universes** (XS |t|<0.8). Robust — the
  big large-cap TS IC is a shared-factor artifact, not cross-sectional discrimination. Keep OFF.
- **`net_debt_to_ebitda` gate-threshold fit: NULL** (XS |t|<1). The `~growth` collinearity is
  universe-sensitive (+0.54 largecap trips, +0.19 smallmid doesn't), so even the "duplicates
  growth" rejection isn't stable. No basis to retune the 4.0 prior.
- **`piotroski` (→ `flags.value_trap.piotroski`): significantly *negative* on smallmid**
  (XS −0.072 t=−3.0 @6m) — a **value-regime artifact** (whole basket: value TS-t +5.9 vs
  quality −4.5 / piotroski −5.1 over ~2021–26), NOT a green light. The conditional value_trap
  mechanism still needs a through-cycle test. Keep OFF.
- Note: `accruals` (a LIVE leg, earned at +0.036 t=2.1 on its 195-name broad universe) reads
  −0.04 (not sig.) here — one regime-contaminated window, not grounds to reverse, but its edge
  is fragile across universes. Worth a through-cycle re-check once snapshot history allows.

Evidence-of-record: `docs/superpowers/specs/2026-06-25-xbrl-broad-universe-results.md` (local,
gitignored per the specs/ convention). The `smallmid` universe is bundled so it's re-runnable.

---

## Predictive-signal pipeline — remaining work (2026-06-21)

Five free-data signals were researched, built (PRs #65/#67/#68/#69/#70, all gated OFF),
backtested, and the two validated winners enabled live:

- ✅ **Live now:** `residual_momentum` (momentum leg, XS rank-IC +0.023 t=2.6 — #72) and
  `accruals` (quality leg, XS rank-IC +0.036 t=2.1 broad — #71).
- ⏸ **Measured & parked:** `asset_growth` (no cross-sectional edge, XS≈0) and
  `shareholder_yield` (strong time-series IC, XS≈0) — both wired + measured, kept OFF.
- ⏳ **Unvalidated (blocked on data):** `sue` and `filing_text_change` (Lazy-Prices) — see #1.

### 1. Turn on daily accumulation to unblock SUE + Lazy-Prices  ← do this first

**Why:** SUE (earnings-surprise drift) and the Lazy-Prices filing-text-change flag cannot be
backtested by the `--source xbrl` or `--source momentum` paths — their inputs (Finnhub
earnings surprises, 10-K/10-Q text) are **not in SEC companyfacts and are not accumulated
anywhere**, so rank IC is currently unmeasurable. Their scoring legs/flag are wired into the
**guarded snapshot-replay** backtest path but **no-op until ≥24 daily `TickerSnapshot`s exist**.

**Action:** enable `shortlist-accumulate` to capture point-in-time snapshots daily.
- A **disabled** systemd timer sample lives in `deploy/` (scheduling ships OFF by design).
  Operator can wire the systemd timer **or** a plain cron entry.
- **Breadth is now ready (#82, 2026-06-26):** the snapshot-replay path needs ≥30 names/date
  (`engine._TRUST_MIN_BREADTH`), so the bundled watchlist is now **42** names and the `deploy/`
  sample sets `--max-tickers 42` (FMP 429s past ~19 names, but the overflow still saves on
  keyless coverage). The library `--max-tickers` default stays 15 for ad-hoc runs, so a *default*
  `shortlist-accumulate run` truncates to 15 and stays below the floor — the timer/cron must pass 42.
- After ~24+ daily snapshots accumulate, run the snapshot-replay backtest to measure the
  `sue` and `filing_text_change` axes' rank IC + collinearity, then enable (or reject) them
  exactly like the other four — flip the config block only if the IC earns it.
- **Test gate reminder:** the suite needs the edgar extra — run `uv run --extra edgar pytest`
  (bare `pytest` errors on pre-existing edgar tests that import pandas).

**Pointers:** `CLAUDE.md` → "Accumulation"; `HARNESS.md` → "Feeding the snapshot path";
`shortlist.data.accumulate` / CLI `shortlist-accumulate`; `deploy/` (disabled sample).
**Status:** pipeline breadth-ready (#82); remaining = operator enables the timer/cron with
`--max-tickers 42`. Not started — operator action.

### 2. (Optional, low priority) Tune the now-live legs — `--fit` is NOT the right tool

`accruals` (in `quality_score`) and `residual_momentum` (in `momentum_score`) ship as
**unfitted priors** — their `config.yaml: thresholds.*` bands and the momentum weight are
hand-set, not fitted.

**Important caveat:** `shortlist-backtest --fit` fits **only the 4 fundamental *composite-axis*
weights** (`quality, moat, growth, value`), requires `--source xbrl`, and **proposes only
(never writes `config.yaml`)**. It therefore:
- can re-propose the **quality axis weight** now that `quality` includes the accruals leg (marginal);
- does **NOT** tune the `accruals` or `residual_momentum` **threshold bands**;
- does **NOT** touch `momentum` at all (it isn't a fit axis), so `residual_momentum` is
  entirely outside `--fit`'s scope.

So `--fit` is **largely not necessary/applicable here.** If you want it for the quality axis anyway:
`uv run --extra edgar shortlist-backtest --source xbrl --fit --fit-horizon 12 --fit-axes quality,value`
(proposal-only; review before hand-editing `config.yaml`).

The more useful (manual) tuning, if the legs underperform in practice: revisit the
`thresholds.accruals` / `thresholds.residual_momentum` bands and the `momentum` weight against
the measured IC. **Status:** optional — the shipped priors are reasonable; only pursue if the
live legs misbehave.

### 3. Harden autobuild before reusing it on this repo

The autobuild run used to build these signals **leaked a spawned session's commit onto local
`main`** (it worked in the live checkout, not its worktree). Recovered; pivoted to manual
isolated-worktree subagents. Before reusing autobuild here, harden it (a hand-off prompt was
drafted) — or at minimum run it against a **throwaway clone**, never the live working repo.
See the `autobuild-signals-backlog` session memory for the full diagnosis.
**Status:** separate repo (`/home/chris/autobuild`); not blocking shortlist work.

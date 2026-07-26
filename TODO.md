# TODO — follow-ups for a future session

Tracked, low-urgency follow-up work that has no natural home in code comments.
Newest context at top. See `docs/PREDICTIVE_SIGNALS_RESEARCH.md` for the signal designs and
`docs/ASSESSMENT_GAPS.md` for the broader scoring roadmap.

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

**NEW ITEM −1, ahead of everything: fix `calendar_time_portfolio`.** A month's portfolio
return must be the mean of held names' ACTUAL month-t returns, not the mean of their
compounded-then-flattened ones. Needs monthly price paths per event (available — the price
histories are already fetched and day-cached in `.cache/famafrench`). Then re-derive all four
cohorts and rewrite the audits. Until that lands, quote NO cohort alpha, and treat the
`edgar_8k` / `edgar_buyback_auth` / `edgar_activist_13d` verdicts as unmeasured rather than
negative.

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
0c. **Remaining gap:** `double_sort`'s `spread_ci` still uses the month-resampled bootstrap,
   so the spread CIs are still too tight. Display-only (no verdict reads it) but the digest
   shows it. Needs a per-bucket event resample.
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
2. **Rebuild `edgar_form4` as an opportunistic-insider originator.** It is enabled at weight
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
`fix/validate-event-bootstrap-ci`, pushed). Items 0c, 0d, 1, 2, 3(b), 4 remain. Item 1 (size-band re-validation) is
now unblocked and is the next step; item 2 (the `edgar_form4` rebuild) is the main build and
has not been started. **All of it is UNCOMMITTED in the working tree** (7 files, ~379 lines;
`validate.py` is the only production module touched) and nothing is deployed to
`/opt/shortlist` — the daily push still runs the pre-change behaviour, WSB included.

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

1. **FMP-won statements silently drop every EDGAR-only field** — `statements` is a
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

**Status:** open — fixes await operator push/PR; item 1 is the highest-value build
(pure data recovery, no new scoring surface); item 2 is one backtest command per universe.

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
4. **Prereg tamper-check is path-based, not content-based** (surfaced by the code review
   of this branch): `preregister.verify_untampered` reads `git log -1 --format=%cI` at the
   file's current path, so a pure `git mv` (as done here for `edgar_buyback.yaml` →
   `edgar_buyback_auth.yaml`) resets the machine-visible commit time to the rename date.
   Live validation is unaffected (rename ≤ today), and `git log --follow` + the content-
   pinned `as_of: 2026-07-09` preserve the real audit trail, but a *historical* reproduction
   (`run_validate` with a past `today`) would append a spurious "NOT PRE-REGISTERED" note.
   Proper fix = verify prereg *content* was committed ≤ as_of (affects all four signals) —
   its own PR. Evidence for the buyback KILL is committed at
   `docs/audits/2026-07-11-buyback-backfill-kill.md`.

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

## Deploy to /opt/shortlist done (code side) — bot restart + Telegram smoke pending (2026-07-09)

The pending deploy carrying #128 (/explain), #130 (VFLEX non-registrant skip), and #132
(SUE decay anchor) is synced: `/opt/shortlist` rsynced to `9e34ee5` (installer excludes
replicated exactly; `deploy/` units unchanged in the jump, no deletions/renames so no
`rm -rf src` needed), `uv sync --extra scout --extra edgar` audited clean, offline
`shortlist-scout --demo` smoke OK, and the deployed venv resolves the new
`scout/glossary.py` (`lookup("sue")` returns the SUE entry). The 21:30/22:30 timers pick
the code up automatically (editable install). Also verified: the accumulate unit's
ExecStart **already carries `--sources fmp,finnhub,edgar`** — the 2026-07-07 item-1
`SHORTLIST_ACCUMULATE=1` installer re-run is DONE (marked below).

Remaining (operator — needs root, session couldn't sudo):
1. `sudo systemctl restart shortlist-bot.service` — the long-running bot still has the
   OLD modules loaded until bounced; /explain and the VFLEX skip are NOT live in the bot
   until then.
2. After the restart, live Telegram smoke: `/explain sue` (glossary) and `/deep VFLEX`
   (expect the friendly non-registrant skip, not the raw edgartools error).

**Status:** code deployed 2026-07-09 ~04:05 UTC; bot restart + 2 smoke checks pending.

## SUE decay anchor fixed — EDGAR 10-Q filed date; free-tier calendar is empty (2026-07-09)

The systematically-fast SUE decay (2026-07-07 item 6) is fixed, but NOT by the planned
calendar-window widening alone — live probing showed **Finnhub's free tier returns zero
historical `calendar/earnings` entries** (even a full past year is empty), so a past
announcement date can never come from Finnhub on this plan. The shipped fix is a
three-tier anchor (CLAUDE.md → SUE section): true calendar date (paid-tier only; the
request now reaches back ~120d so it activates on a paid key) → **EDGAR 10-Q/10-K filed
date** (new `Events.last_report_filed`, exact forms only, `max(quarter_end, filed)` and
only when `Earnings.last_report_date_estimated`) → quarter-end fallback. Live-verified:
AAPL anchor 100d → 69d (10-Q filed 2026-05-01, print 2026-04-30 — ~1d error); NVO (20-F,
no 10-Qs) degrades cleanly to the fallback. `config.yaml: edgar_events.forms` gained
`10-Q`/`10-K` (config overrides the code default — a bare-code fix silently no-ops).
Replay note: old persisted snapshots default `last_report_date_estimated: true` on
`from_dict`, so accumulated pre-fix dates get the tier-2 anchor retroactively **where
the snapshot has EDGAR events** — one more reason the `SHORTLIST_ACCUMULATE=1` installer
re-run (2026-07-07 item 1) matters.

**Status:** merged pending PR; VPS picks it up on the next deploy (`git pull` →
`install_opt_shortlist.sh` → restart) — same flow as the pending /explain deploy.

## /explain glossary command shipped — deploy pending (2026-07-08)

PR #128 merged: `/explain [term]` bot command (static 60-entry financial glossary in
`scout/glossary.py`; scoring gains declarative `KNOWN_GATES`/`KNOWN_FLAGS` bound by an
AST-scan test — new gates/flags now fail CI until documented in glossary + theme legend).
The live bot at `/opt/shortlist` doesn't have it until the usual deploy flow runs
(`git pull` → `install_opt_shortlist.sh` → restart `shortlist-bot`). Content note: entries
are semantics-only (no config thresholds quoted) so config tuning never stales them.

**Status:** merged; VPS deploy + a quick live `/explain` smoke on Telegram pending. Same
deploy also picks up #130 (friendly `/deep` skip for non-SEC-registrant tickers like
VFLEX — was leaking the raw edgartools "Company not found / Tip:" error); re-run
`/deep VFLEX` after deploy to confirm the new copy. **Update 2026-07-09:** code deployed
(top entry) — only the bot restart + the two smokes remain.

---

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

## Verified: first FMP-free digest ran clean (2026-06-30) — item below resolved

The `shortlist-scout` timer fired 2026-06-30 22:30 UTC on `/opt/shortlist` (the deployed repo;
`/home/chris/shortlist` is a stale dev checkout — ignore its `state/`). Confirmed **0 FMP calls**
(`PEG` + `Target upside` null on every name incl. AMD/NKE; all 7 axes still scored, `value`
recovered from EDGAR/Yahoo), the "Free-source screen — /deep for PEG + analyst targets" caveat +
`/deep` block + prior-picks-vs-SPY scoreboard all rendered, `research: false` honored (no Claude
burn), picks recorded (`runs`/`picks` include 06-30), no delivery errors. **Delivery not
positively logged** (the gap the new `feat/scout-delivery-log` branch fixes) — 06-30 push itself
unconfirmed; check Telegram. Paid-FMP flip below remains a deferred decision.

---

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

## Daily scout push armed in config — VPS deploy + first run pending (2026-06-29)

`scout.daily_push.enabled` is now `true` (#95; lean digest, `research: false`) — but the flag
alone does nothing until the **VPS** runs it. Remaining operator steps: sync the repo to the box
and enable the `shortlist-scout` systemd timer (`deploy/shortlist-scout.timer`, or
`deploy/install_opt_shortlist.sh`), with `.env` present (`SEC_IDENTITY` ±
`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`; without the Telegram pair it journals to `scout/<date>/`
+ stdout and still records picks). Don't read `enabled: true` as "live" — verify the timer on the
box. Until it runs nightly, no picks accumulate, so the selection-ledger forward-return analysis
(item 1 of the next section) stays blocked.

**Status:** config armed (#95); VPS deployment + first nightly run pending (operator action).

---

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

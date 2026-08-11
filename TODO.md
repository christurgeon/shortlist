# TODO — open follow-ups

**A working set of open work, not a session journal** — when an entry's work ships, delete it
rather than marking it done. The durable record is `CLAUDE.md` (behaviour + landmines),
`docs/audits/` (evidence) and git history; a resolved entry left here is pure cost. This file
reached 2,133 lines by 2026-08-08 because nobody owned removing anything.

See `docs/PREDICTIVE_SIGNALS_RESEARCH.md` for signal designs and `docs/ASSESSMENT_GAPS.md` for
the scoring roadmap.

> **HOW TO PRIORITISE ANYTHING BELOW.** The bar is **"surface interesting stocks the user
> evaluates, and passes to `/deep` when they want a closer look."** *It is fine for a signal to
> have no measurable edge* — that is `CLAUDE.md`'s design premise, not a defect.
>
> **Discovery is the user's own research**, feeding `/screen` and `/deep`. Work that makes a
> *supplied* name easier to judge outranks work that measures forward returns.

---

# 1. Bot & report

## `shortlist-accumulate` has NO failure alerting on the box (2026-08-10)

The `OnFailure=` line is in the accumulate unit's heredoc and pinned by
`tests/test_deploy_units.py`, and the installer now writes the alert **template**
unconditionally — but it only regenerates `shortlist-accumulate.service` under
`SHORTLIST_ACCUMULATE=1`, so the unit on the box still dates from 2026-07-08 and has no
`OnFailure=`. That timer is **active** (21:30 UTC) and currently fails silently.

```
sudo SHORTLIST_ACCUMULATE=1 bash deploy/install_opt_shortlist.sh
systemctl cat shortlist-accumulate.service | grep OnFailure   # the check that settles it
```

Also unverified end-to-end: nothing has actually *failed* yet, so the `OnFailure` → template →
script → Telegram chain is untested in situ. Force it with a transient unit carrying the same
`OnFailure=`, or wait for a real failure and see whether the alert lands.

**Status:** accumulate is now the only scheduled unit, so this is the only alerting path left.

## Deploy recipe: `git pull` first, NEVER `rm -rf src`

`/opt/shortlist` is a **git checkout tracking `origin/main`**, not just an rsync target, so
`git pull` already removes files deleted upstream. The documented recipe is the only correct
one:

```bash
cd /opt/shortlist && sudo git pull && sudo bash deploy/install_opt_shortlist.sh
```

Do **not** `rm -rf /opt/shortlist/src` first, however tempting given the rsync's missing
`--delete`. That is destructive here: the installer
derives `SRC` from its own path, so running it from `/opt/shortlist` makes `SRC == DEST` and
the rsync copies the tree onto itself — with `src/` deleted there is nothing to restore it
from, and step 3's `uv sync` then rebuilds the venv against a package with no code. Recovery is `git checkout -- src/`,
because the repo is already at the right commit.

Two hazards that only bite together, so both are worth keeping in mind: the `SRC == DEST`
no-op is *harmless* when the content is already correct (which `git pull` guarantees), and the
missing `--delete` is *harmless* on a git checkout (which handles deletions itself).

**Status:** recipe corrected here and in CLAUDE.md. Two installer improvements are open —
the smoke test aborts under `set -euo pipefail` *after* the venv rebuild but *before* the unit
changes, which is the worst place to fail; and the installer could refuse to run when
`SRC == DEST` rather than reporting success.

## VPS remnants

`/opt/shortlist` still holds the orphaned `state/scout_state.json` and a `scout/<date>/`
artifact tree from the old nightly run. Nothing reads either. Decide whether to delete them
on the box or leave them as a local archive.

## A null `market_cap` still bypasses the size gate (2026-08-07)

`scoring.py:627` needs `m.market_cap is not None`, so a null cap ⇒ recorded `gated: false` —
the name passes the size gate unchecked. Measured on the (now-archived) ledger: 13 of 199 picks
had a null cap and **every one** was ungated. Note the interaction with `50be4ed` (Finnhub
non-USD abstention) — a correct fix that traded an *inflated* cap for a *null* one, closing the
wrong-number half of the TSM bug and leaving the silently-passes half.

The funnel-side half of this entry retired with `investable.py`. What remains is the gate, and
it now matters for `/screen` on a user-supplied ticker rather than for a discovery funnel.

**Status:** defect verified in code; the user was shown the ledger evidence on 2026-08-08 and
declined to act. Do not re-raise as high-leverage without new evidence.

---

# 2. Measurement (backtest + snapshot replay)

Ordering note: everything here answers an alpha question, which the prioritisation rule at the
top ranks *below* work on judging a supplied name. Take these when they are cheap or when they
unblock something.

## Snapshot-replay path: live, with two standing constraints (2026-08-09)

`--source snapshot` is un-gated and smoke-tested. What survives is the part that constrains
*future* runs:

- **Horizon maturity.** Signals come from the store, forward returns from Yahoo `hists`, so
  the store's span bounds the *observation grid*, not the horizon. Only **h=1** has matured
  windows from the earliest captures; **h=3 needs ~late September 2026**. A 3-month replay
  before then is not a thin result, it is an empty one.
- **The 0% suppression result is large-cap-only.** 1642/1642 stored snapshots emit
  `composite` — but the store is 42 large caps, the population where confidence is highest.
  **Re-measure before trusting composite replay IC if accumulation widens to small/mid**,
  where `validity.min_scored_weight` (0.25) is far likelier to bind. The new
  `validity.min_composite_components` floor (2026-08-11) is a second gate to re-check there.

Reading trap: low breadth is **not** suppression — a 2026-06-22 grid date shows breadth 10
because only 10 tickers had been captured that early (42 by 2026-08-08).

- **SUE** is blocked on calendar time only — a verdict with ≥8 non-overlapping blocks is a
  late-2026-into-2027 proposition. Keep accumulating.
- **Lazy-Prices (`filing_text_change`) can never validate on this path** — full filing text was
  deliberately kept out of the snapshot (`EdgarSource` fetches Form 4 + financials +
  filing-index only). Measuring it needs a collector change to compute EDGAR text similarity
  into the snapshot. Separate feature, not a waiting game.

## `operating_income` missing on 41% of the FMP-gated path — DIAGNOSE, do not assume (2026-08-10)

Scoped out of the ROIC work deliberately (that design was built not to depend on it, and does
not). It splits into three populations and only one is plausibly a bug — **guessing which is the
read-past-the-evidence pattern the 2026-07-26 postmortem records**:

- **banks — NOT a bug.** BAC/GS/WFC (SIC 6021/6211) report net interest income, not operating
  income; JPM's raw income-statement concepts are literally `[]`. SIC abstention already masks
  ROIC for these buckets, so they are correctly uncomputable.
- **non-financials that certainly do report it — a real gap.** CVX, XOM (2911), HON (3724),
  IBM (3570), JNJ/LLY/MRK (2834). Three pharma at one SIC is a striking pattern; they likely tag
  the line differently.
- **intra-ticker inconsistency — most concerning.** DIS 23/34 capture days, JPM 27/34, NKE 23/34.
  Same filing, different result across days ⇒ edgartools version drift over the window or a
  non-deterministic code path. Distinguish by checking whether the flip is a clean split at one
  date (version change) or interleaved (non-determinism, a real bug).

Deliverable is an **audit note**, not a fix. `_edgar_facts.py:383` uses
`_row_by_standard_concept(income_df, "OperatingIncomeLoss")`; the raw-`concept`-first rule
(`_rows_by_concept`) is the candidate remedy *if* the diagnosis supports it. Fixing this would
raise the computed-ROIC yield from ~56% toward ~97% of the affected path.

## Momentum Stage 0 prize-bound re-run

Run on the full **80-name** largecap basket (`uv run python -m shortlist.backtest.prize_bound`)
— the 2026-06-14 marginal PROCEED used a 28-name subset, and a quota-starved run inflates
momentum's effective weight. **Decision rule:** compare `mom_12_1`'s full-basket τ to the
recorded **0.947** — holds or drops ⇒ the prize is real, write the Stage 1 plan; rises toward
1.0 ⇒ **stop**, momentum at 0.08 is a near-zero mover. Drop `mom_6m` either way (τ 0.995 vs the
incumbent `rel_strength_6m`, fully redundant). Cost is ~1,000 FMP calls against the 250/day free
cap — a scheduling/quota problem, not a host problem.

## Other measurement gaps

- **Re-measure the `net_debt_to_ebitda` axis** on both committed universes. Every prior IC run —
  including the 2026-07-11 "leverage tilt NOT earned" verdict — scored negative-EBITDA names at
  the **top** of the inverted leverage band (they read as net cash) before the abstention fix.
  The verdict may stand, but it was measured on polluted data. One backtest command per universe.
- **Gate-impact measurement (`negative_fcf` excuse, scope B).** Gates are entirely unmeasured.
  Compare forward returns of *excused* (high-growth) vs *gated* negative-FCF names to test
  whether `revenue_cagr ≥ 0.15 ∧ persistence ≥ 0.70` beats a blanket gate. Needs new machinery
  (the XBRL source would have to evaluate gates, or a parallel cohort path).
  `docs/ASSESSMENT_GAPS.md` §2.7.
- **DEF 14A pay-vs-performance axis.** The **XBRL path is a NO-GO** (live-verified): SEC's XBRL
  APIs serve only `dei`/`us-gaap`, the `ecd` PvP tags are absent from companyfacts, and a
  `companyconcept/.../ecd/...` probe 404s. It can only be built via snapshot-replay once
  accumulation captures `research/proxy.py`'s PvP extraction point-in-time. Phase 2 also holds
  the narrative related-party/CD&A sections (no section splitter, ~350K-char raw text).
- **`dilution`-flag threshold review** instead of a `share_count` scored leg — the payoff is
  tail-concentrated, which suits a flag/screen better than a ranker.

---

# 3. Data layer

## FMP quota is over-subscribed — a config-or-money decision, not a build

Accumulate (42 tickers) alone runs ~550 calls/day against a 250/day free limit, which is why
**23 of 24 store dates have ZERO fmp-won statements** and EDGAR supplies 100% of production
statements. Options: drop `--max-tickers` to ~18; remove `fmp` from the accumulate chain
(it contributes nothing today); or paid **Starter** (~$14–20/mo).

The free window is **not calendar-UTC-day aligned** (measured 2026-07-31: still "Limit Reach"
42 min after UTC midnight, drained by our own 21:30 accumulate timer). Retry probes in the
**mid-day UTC window**.

Unblocks on this:
- **The live statements-merge before/after was never run.** The plan required a
  `shortlist --json` run on a real FMP-covered ticker showing `share_count_cagr`/`asset_growth`/
  `accruals` populated where the old merge returns null; both runs 429'd. The mechanism is
  covered by unit RED evidence plus a store-based offline re-merge on 17 real FMP-won snapshots
  (join key agrees on real data, including non-calendar fiscal years) — but the FMP-wins branch
  under a **live fetch** is still unexercised. Treat "recovered fields still null on a non-402
  name" as a bug against the fiscal-year join key, not a config problem.
- The momentum prize-bound re-run above.

## Should `fmp` outrank `edgar` for the insider transaction group? (design decision)

`config.yaml`'s `harness_sources` orders `fmp` before `edgar`, and `_merge_insider` takes the
coupled transaction facts **wholesale** from the first source with a present field — yet
`CLAUDE.md` calls EDGAR "the free authoritative source" for insider data. So enabling a paid
FMP Starter tier would silently override EDGAR's insider numbers. A priority/intent question,
deliberately left out of the FMP-insider classification fix; it becomes live the day the quota
decision above goes the paid way.

## EDGAR / statements minors

- **`get_shares_outstanding_diluted()` returns MCD's count in millions**, not absolute shares
  (`[716.4, 721.9, 732.3]`). Nothing depends on it for MCD any more, but any future consumer
  inherits the bug. (`diluted_shares` from the companyconcept fallback *is* absolute, so
  `financial_series` display mixes conventions — scoring is unaffected, `share_count_cagr` being
  scale-invariant.)
- **Widen the diluted-shares go/no-go beyond the store's 42 tickers** — keyless, costs only
  time, and it is the only thing that further reduces residual risk (another code review would
  not).
- **`_usable_years` (`data/models.py:510`) does not reject an all-`None` `fiscal_years` list.**
  Net observable behaviour is identical to rejection, so it is a docstring-vs-contract gap, not
  a wrong output.
- Parked observations: the `pe_ttm` fallback accepts negative EPS (harmless — `pe_vs_history()`
  guards `> 0` and `pe_ttm` isn't in `--json`); `bridge._close_near` has no max-gap bound (a
  short monthly history can pair a fiscal end with a months-away close);
  `Fundamentals.operating_margin`/`current_ratio` are extracted but consumed nowhere.

---

# 4. Code hygiene (fold in when next touching these files)

- **Optional guardrail:** ruff `C901` with a `max-complexity` (or a soft line ceiling) so
  mega-functions can't silently regrow. Its own small change, not bundled with a refactor.
- `_TRUE` is duplicated between `edgar/dera.py` and `edgar/insider.py`; `n_joint` counts tickers
  pre-filter while labelled "filings"; `edgar/index.py:fetch_daily_records`/`fetch_recent_records`
  are dead code with tests pinning them.
- An `isinstance` assertion-of-convenience in `tests/test_edgar_insider_parse.py` exists only to
  satisfy ruff F401 — fold into a real assertion.
- `docs/PLAN_EDGAR_DILUTED_SHARES.md`'s historical "Step 2"/"Step 3" code blocks quote the
  original signed-off text (including a false "ultimately ABSTAIN" safety claim). Each is
  followed by its `[R…]` correction so a linear reader is fine, but someone skimming would copy
  stale text — annotate them "superseded".
- **Pin the dev Python via `.python-version`** — a fresh 3.11 venv fails
  `test_block_bootstrap_ci_*` on a floating-point boundary that 3.13 doesn't hit, so a fresh
  clone hits a spurious local failure.

---

# 5. Closed with a verdict — do not redo

One line each, so the next session doesn't re-derive them. Evidence is in `docs/audits/`.

- **The autonomous scout is retired (2026-08-11)** — every originator that reached the evaluator
  came back INSUFFICIENT or KILL, the apparatus that could settle the rest was blocked on a paid
  price feed, and the stack was 47% of source LOC and 59% of tests. Decision, evidence, the
  archived 203-pick ledger and the seven committed pre-registration YAMLs:
  `docs/audits/2026-08-11-scout-retirement.md`. **Do not rebuild an originator without new
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
  killed — do not rebuild either (`docs/audits/2026-08-07-wsb-novelty-rule.md`).
- **A market-cap pre-filter in the funnel** deleted the only names there were (13 of 25 sessions
  → zero candidates). Resolved instead by lowering `gates.min_market_cap` to $300M.
- **Cohort levels are structurally unmeasurable on free data** (outcome-correlated attrition;
  22% of events have no price series, monotonic in age). Do **not** build the ABK/value-weighting
  correction; never quote a RAW-cohort alpha. No data purchase indicated.
- **Do not give an EDGAR client its own `SecThrottle`** — a per-client throttle cannot bound the
  process's request rate, which is exactly how the 2026-08-04 cascade happened. Concurrency buys
  nothing here (~17 ms latency; one serial worker already sustains ~57 req/s).
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
  8-K still excludes zero. `docs/audits/2026-08-03-evaluator-rederivation.md` is the current
  record — quote it, not the older audits.


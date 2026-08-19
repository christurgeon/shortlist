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
the sync step is skipped (see below) — with `src/` deleted there is nothing to restore it
from, and step 3's `uv sync` then rebuilds the venv against a package with no code. Recovery is `git checkout -- src/`,
because the repo is already at the right commit.

Two hazards that only bite together, so both are worth keeping in mind: the `SRC == DEST`
skip is *harmless* when the content is already correct (which `git pull` guarantees), and the
missing `--delete` is *harmless* on a git checkout (which handles deletions itself).

**Status:** recipe corrected here and in CLAUDE.md. The installer now detects `SRC == DEST`
(canonical comparison, before any mutation) and skips the rsync with a loud notice instead of
silently reporting success on a self-copy — it does not hard-refuse, since the documented
`git pull` recipe depends on running in-place. One installer improvement is still open: the
smoke test aborts under `set -euo pipefail` *after* the venv rebuild but *before* the unit
changes, which is the worst place to fail.

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

# 2. `/deep` as market research — external-review triage (2026-08-11)

An outside review of the repo argued that `/deep` is **good issuer diligence but not market
research**: it understands the company's filings far better than the company's market, peers,
customers and expectations. Every claim below was **checked against the code before it was
written down** — each carries a verdict, because roughly half of the review restated work that
is already recorded elsewhere, and two of its scoring recommendations conflict with committed
evidence rules.

Ordering here follows the file's own bar: this whole section is *judging a supplied name*, so
it outranks §3's alpha questions.

## 2a. VERIFIED defects — small, worth building

- **Shipped:** the brief cache is now a wide key — `research/cachekey.py:brief_key` composes
  the filing accessions with a prompt/config fingerprint, a bucketed quant/event context
  digest, and an as-of day bucket (`research.cache.{max_age_days, price_band_pct}` in
  `config.yaml`). Editing the prompt, the guards, `research.max_chars`, or a material price
  move now invalidates the cache instead of silently serving a stale brief.
  - **Known gap, no producer yet:** the YoY Lazy-Prices text-similarity computed alongside the
    wide key (`research/filings.py:_prior_year_sections`, `FilingBundle.text_similarity`) only
    ever reaches `/deep` as a prompt-only context line and a rendered `## Filing-text change
    (Lazy Prices)` section. Nothing writes `StockMetrics.filing_text_similarity`, so the
    `filing_text_change` scoring flag still cannot fire — `check_flags` runs inside `score()`
    (`scoring.py:809`) during `run_harness`, and research runs after it (`screen.py:188`,
    `:193`), so a research-layer producer is structurally too late for the screen path.
    Guarded by `tests/test_flag_producers.py::test_declared_flag_inputs_have_a_writer`
    (deliberately `xfail(strict=True)` — delete the decorator, not the test, if a
    collection-time producer ever ships).
- **10-Q Part II Item 1A: SHIPPED 2026-08-14.** The quarter's risk-factor *changes* now reach
  the model as a diff against the 10-K Item 1A (`research/filings.py:_tenq_added_risks`,
  `config.yaml: research.tenq_risk_update`). Design + 25-filing probe evidence:
  `docs/audits/2026-08-14-tenq-part-ii-in-deep-design.md`.
  - **Item 1 (legal proceedings) was deliberately NOT built** — measured, 10 of 15 names are
    200–800 chars of "Refer to Note 24 of this Form 10-Q". The legal substance is in the
    **notes**, so it is §2b work (legal contingencies, item 6 on its list), not an item
    extractor. Do not re-raise it as a 10-Q gap.
- **`_tenq_mda` silently abstains on one filer in 35 (found 2026-08-14, pre-existing).**
  Both symptoms are edgartools item-boundary detection, not our call. Measured over 35 large
  caps (2026-08-14), and **both are now logged to stderr** by `_tenq_mda`
  (`fix/tenq-mda-diagnostics`) — the gap is observable, not fixed:
  - **The real defect: INTC extracts 0 chars (1 of 35)** — its Part I Item 2 heading is not
    detected, so the preceding Part I Item 1 span (135,783 chars) absorbs the MD&A and the
    brief carries no quarterly MD&A at all.
  - **Over-capture (3 of 35) is NOT harmful.** JPM 0.846, MCD 0.644, PFE 0.566 of the whole
    document vs a median 0.230 and p90 0.397 for normal names — a clean gap between <=0.40
    and >=0.566. An earlier revision of this bullet claimed JPM's brief "is fed the first 40K
    of the wrong span"; that was **wrong**. All three spans start at a genuine MD&A heading,
    so the prefix surviving `max_chars.tenq_mda` (40,000) is genuine MD&A prose and the model
    sees correct content. Do not change extraction for this case.
  - **Two measured traps, pinned by regression tests.** `tenq["Item 2"]` is not a fallback —
    on INTC it returns 2,459 chars of *Part II* Item 2 (share repurchases), wrong content
    silently labelled MD&A. `tenq.items` is not a guard — XOM lists an unqualified `Item 2`,
    TSLA three entries, MCD exactly one, yet `get_item_with_part("Part I","Item 2")` returns
    69,820 / 49,879 / 122,045 chars for them, so an `items` check reports phantom failures.
  - **Recovery is deferred**, not forgotten: slicing the containing Part I Item 1 blob at an
    MD&A heading is fitted to n=1 and would inject wrong text into the grounding haystack.
    Needs a wider probe first. Same class of fault as INTC's Part II Item 1 returning 71,869
    chars of Note 14.
- **8-K substance in `/deep`: SHIPPED 2026-08-14, one caveat still open.** Behaviour and
  landmines are in `CLAUDE.md`; evidence is
  `docs/audits/2026-08-13-eightk-text-in-deep-design.md`. What remains open here:
  **Item 4.02 (non-reliance/restatement) is unexercised by real data** — none appeared in the
  60-filing probe, so the highest-priority branch is pinned by a constructed fixture only. Do
  not describe it as verified; the first real restatement is its first real test.
- **Evidence discipline: SHIPPED 2026-08-17 for moat + management.** `moat.sources` is now
  `list[Finding]` and `management_findings` is new; both are quote-verified, and an empty
  quote is a legal *declared inference* counted separately from `unverified_count`. Measured
  defect (45% of moat sources asserted an ungrounded figure; 14–27 bare numbers per
  management paragraph), design, and three non-obvious constraints:
  `docs/audits/2026-08-17-moat-management-evidence-design.md`. Two things stay open:
  - **`business_model_summary` is still bare prose.** Deliberately out of that cut. It is the
    last narrative section with no grounding standard.
  - **Live-verified n=3 (AAPL, JPM, INTC — 2026-08-18): holds on all three.** No truncation
    on either stress filer (`stop=end_turn`; JPM over-captures to 601K chars, INTC's 10-Q
    MD&A extracts 0), zero rule bleed across all 58 strict-list items, zero fabrications in
    either new list, and management prose 575/629/601 chars with **0 numeric tokens** against
    a 900–1,157 char / 14–27 number baseline. INTC answered the open question: on a weak-moat
    filer the inference list does **not** inflate — it shrinks to 4 (cap 6), 2 verified / 2
    declared. Still one run each on three large caps.

- **Evidence is invisible on the Telegram surface (pre-existing, applies to risks too).**
  `/deep` delivers `art.png/html/text` from the viewmodel (`telegram.py:339-341`); the
  markdown brief — the only artifact carrying quotes, `_(unverified)_` marks and provenance —
  is never sent. `viewmodel.py:126-127` reduces risks and red flags to `_claim(x)`, dropping
  evidence entirely. So the whole grounding layer reaches CLI/file readers only. Fixing it is
  a real design question (Telegram message length, `Detail.GLANCE` vs `FULL`), not a
  one-liner. Do it for **all** findings at once or the surfaces disagree about what "verified"
  means.

- **The 2026-08-04 audit's three prompt-only wins SHIPPED 2026-08-18** — materiality bar
  instead of a count quota (D3), a CLOSED `red_flags` enumeration (D7), no cross-section
  quote reuse (D6), and an instruction to do the arithmetic (normalized earnings
  ex-one-offs, cash runway, refinancing coverage). What shipped, the constraint that keeps
  a derived figure out of an `evidence` quote, and the re-measurement recipe:
  `docs/audits/2026-08-18-deep-prompt-materiality-and-arithmetic.md`. **Still open: none of
  it is measured yet.** The pre-change saturation baselines are recorded in that note; the
  briefs must be re-counted against them before the change can be called a win, and the
  failure mode to look for is OVER-application (Sonnet 5 follows instructions more
  literally than the generation the prompt was tuned against).

## 2b. Filing content we do not extract (bigger, genuinely missing)

Statement **notes** never reach the prompt — `assess.py:324-331` sends Item 1, Item 7, Item 1A
and the 10-Q MD&A only. The notes hold segment reporting, revenue disaggregation, customer
concentration, debt maturities/covenants, SBC, restructuring, acquisitions/goodwill, legal
contingencies, tax and leases. Build as **targeted extractors, never "send all notes to
Claude"** — order by decision value: (1) segments + disaggregated revenue, (2) debt & liquidity,
(3) SBC & dilution, (4) concentrations & commitments, (5) acquisitions/goodwill, (6) legal
contingencies. SEC's Financial Statement **and Notes** data sets are the structured route;
edgartools text extraction is the cheap route. Sequence this **after** 2a.

## 2c. Peers / market context — the review's headline gap

A peer bundle (5–10 SIC/NAICS + size + revenue-mix matched names; growth, margins, ROIC,
share-count growth, valuation, relative performance) is the review's top recommendation, and it
is also **the enabling data for `docs/ASSESSMENT_GAPS.md` §2.3** (sector-relative percentiles
replacing absolute bands) — do not re-spec §2.3 from scratch. Take the review's own constraint:
**display it in `/deep`, do not feed it into the score** until it has cross-universe evidence.
Cost is the blocker, not design: N peers × the per-ticker call budget against a 250/day FMP free
cap (§4). The keyless route is SEC `companyfacts`/`frames`, which is also what `--source xbrl`
already reads.

## 2d. Recorded, NOT endorsed — where the review argues against committed rules

- **"Split risk out of the composite."** `weights.risk: 0.10` is the *shipped* design of
  `ASSESSMENT_GAPS.md` §2.9, deliberately a tilt. The review's point (low trailing vol is a
  preference, not an expected-return claim) is fair as a **labelling/display** question —
  expected-return evidence vs fundamental risk vs market exposure, shown separately. Changing
  the composite is a scoring change and needs evidence, not an argument.
- **"Disable `upside_to_target`."** Already recorded at `PREDICTIVE_SIGNALS_RESEARCH.md`
  §Quick wins #1 (Brav & Lehavy: the *level* is negatively related to realised returns; the
  *revision* predicts). The mechanical obstacle is GONE as of 2026-08-18: the leg is now the
  scorer's one **opt-OUT** block (`scoring.py:_upside_to_target_on`, `value.upside_to_target.
  enabled`), default ON and byte-identical when the key is absent, so the counterfactual can
  be run from config. **What is still open is the measurement itself** — nobody has scored the
  leg-off universe against forward returns point-in-time. Flipping the default without that
  test remains the move this file's own bar forbids.
- **"Label the composite heuristic until a survivorship-free, delisting-adjusted, walk-forward,
  multiple-testing-controlled validation exists."** That is already `CLAUDE.md`'s design premise
  verbatim. No action; do not open a work item that restates it.
- **Transcripts / estimate-revision history / 13F / news sentiment** are all already triaged in
  `PREDICTIVE_SIGNALS_RESEARCH.md` (transcripts + estimates paid or no free point-in-time
  source; 13F a Phase-2 candidate; social/news as trigger not valuation). The one live free item
  there remains **recommendation-*change*** — we fetch 4 months of consensus history and keep
  only `trend[0]` (`data/sources/finnhub.py:204`).

## 2e. Net-new source ideas worth keeping (UNVETTED)

Independent, non-issuer-authored industry data — the review's best net-new suggestion, since
everything we read today is written by the company. Census QSS / Economic Census, BEA
input-output, BLS PPI for industry pricing; then sector adapters rather than one universal
feed: EIA (energy/utilities), ClinicalTrials.gov + openFDA (biotech/pharma), FDIC call reports
(banks), DOT/BTS (airlines), FCC (telecom), USAspending backlog (defense — we already have
`gov_contracts`). Cheap landing pattern: an **auxiliary** `Source` whose section is not a
`KEY_OBJECT` (`_AUX_DEFAULTS`, `data/models.py:441`), exactly like `gov_contracts`/`lobbying` —
it reaches the research layer without touching coverage, gates, flags or scores.

---

# 3. Measurement (backtest + snapshot replay)

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

## Pin universe membership by CIK, not ticker (LOW priority — the bleeding is stopped)

`universe_largecap.txt` / `universe_smallmid.txt` key on **tickers, which are not stable
identifiers**. Measured 2026-08-15: 8 of 238 symbols had gone stale unnoticed (4 renamed —
MMC→MRSH, CSWI→CSW, UCBI→UCB, LANC→MZTI; 4 stopped filing), and rot accrues ~3-4%/yr.

Storing `CIK,TICKER` pairs and resolving CIK→current ticker at load (SEC's
`company_tickers.json`, already fetched + month-cached by `xbrl.fetch_company_tickers_raw` /
`build_cik_index`; `edgar/symbology.py` already does this resolution) would make renames
**self-healing** and — the real prize — catch the failure the shipped guard **cannot**:
a ticker REASSIGNED to a different issuer still resolves, so it fails silently.
`B` was Barnes Group (CIK 9984, stopped filing 2024-10-29) and now belongs to **Barrick
Mining** (CIK 756894, a Canadian 6-K filer); it was caught only because Barrick files no 10-Q.
Evidence: `docs/audits/2026-08-14-tenq-mda-recovery-kill.md`.

**Why this is LOW and not urgent:** `shortlist-backtest` now refuses to run a *bundled*
universe containing symbols absent from SEC's map (`universe.py:stale_tickers`,
`--allow-stale-universe` to override), so staleness can no longer silently corrupt a
measurement — it aborts at measurement time. The files are also read by **nothing** except
`backtest/cli.py` and `prize_bound.py`; `/screen`, `/deep`, the bot and accumulate never touch
them. Roughly half a day on a path that runs a few times a year.

**Do NOT "fix" this by generating the universe at run time** from `data/nasdaq_universe.py`
(keyless, 3 requests, ~5,800 names with market caps — the obvious-looking answer). It would
destroy the reproducibility the files exist for: `CLAUDE.md` gates new legs on *reproducible*
cross-universe rank IC, and a membership set that silently differs between runs makes two
verdicts non-comparable — you could no longer separate a decaying signal from drifting
membership. That module also documents itself as undocumented and "the same fragility class as
the Yahoo screener this repo retired". Its correct role is a **deliberate membership-refresh
tool**, never a run-time dependency.

---

# 4. Data layer

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
  (`[716.4, 721.9, 732.3]`). Documented at the one call site as of 2026-08-18
  (`data/sources/edgar.py`), still not fixed: the scalar reaches exactly ONE consumer,
  `extract_financials`' computed-EPS fallback (`ni / shares_diluted`), which fires only when no
  as-reported EPS row matched — so a millions-scaled value yields an EPS 1e6x too small there.
  `ef.diluted_shares` is extracted per-row and does NOT come from it, so `share_count_cagr` and
  the `dilution` flag are unaffected. No sanity bound was added: every candidate threshold is
  fitted to this single observation. (`diluted_shares` from the companyconcept fallback *is* absolute, so
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

# 5. Code hygiene (fold in when next touching these files)

- `edgar/index.py:fetch_daily_records`/`fetch_recent_records` are dead code with tests pinning
  them.
- **Python is pinned to 3.12 (`.python-version`), and the reason on the old bullet was
  STALE.** That bullet said a fresh 3.11 venv fails `test_block_bootstrap_ci_*`; that test no
  longer exists — it came in with the scout validation harness (#105) and went out with the
  scout retirement (`6ed0297`), the same way `n_joint` did. Re-measured 2026-08-18: the full
  suite is **2036 passed on 3.11, 3.12 and 3.13 alike**. The pin was kept anyway, at **3.12
  to match production** (`/opt/shortlist/.venv` is 3.12.3): it makes dev/CI/prod one
  interpreter, and it is the only value that costs the deployed bot nothing — `.python-version`
  is rsynced to `/opt/shortlist`, so pinning 3.13 would have silently rebuilt the live venv on
  a new interpreter as a side effect of a hygiene commit.

---

# 6. Closed with a verdict — do not redo

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
- **The accumulate failure-alert chain is VERIFIED end to end (2026-08-19).** Forced with
  `sudo systemd-run --unit=shortlist-alerttest --property=Type=oneshot
  --property=OnFailure=shortlist-alert-failure@shortlist-alerttest.service /bin/false`. The
  unit failed (`Result=exit-code`, exit 1), systemd started the template instance, it finished
  `Result=success`, and the operator confirmed the Telegram message arrived. Note how to read
  this if you ever re-run it: the script **always** `exit 0` (an `OnFailure` hook must not
  cascade), so the exit code proves nothing — what proves delivery is that journald captured
  NEITHER of its two stderr paths (`missing telegram env vars` / `telegram send failed`).
  Cleanup afterwards: `sudo systemctl reset-failed shortlist-alerttest.service`, or the
  transient unit lingers in `systemctl --failed`.
- **The `net_debt_to_ebitda` axis is re-measured on DE-POLLUTED data and the 2026-07-11
  "leverage tilt NOT earned" verdict STANDS** — no horizon on either committed universe
  clears |t|>=2 (best: smallmid h=6, t=0.94, the only rows clearing both trust floors;
  largecap never clears the breadth floor at all). The negative-EBITDA abstention cost
  smallmid ~10% of its breadth and largecap ~2.5%, and moved the t-stats in both directions —
  it changed WHICH names contribute, not the absence of edge.
  `docs/audits/2026-08-18-net-debt-to-ebitda-remeasure.md`.
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

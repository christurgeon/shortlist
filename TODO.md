# TODO — open follow-ups

**A working set of open work, not a session journal** — when an entry's work ships, delete it
rather than marking it done. The durable record is the code and its docstrings (behaviour +
landmines), `CLAUDE.md` (policy + where the authority lives), `docs/audits/` (evidence) and git
history; a resolved entry left here is pure cost. This file reached 2,133 lines by 2026-08-08
because nobody owned removing anything. **The goal is to empty it and delete it.**

Nothing settled lives here any more. Closed verdicts, and the positions this repo has
considered and declined, are in **`docs/audits/README.md`** — read that before opening a work
item, so a closed question is not reopened from scratch.

Section numbers are stable anchors: `src/`, `tests/` and `config.yaml` cite them (`TODO.md
§2a`), so a retired section leaves a GAP rather than renumbering the rest.

See `docs/PREDICTIVE_SIGNALS_RESEARCH.md` for signal designs and `docs/ASSESSMENT_GAPS.md` for
the scoring roadmap.

> **HOW TO PRIORITISE ANYTHING BELOW.** The bar is **"surface interesting stocks the user
> evaluates, and passes to `/deep` when they want a closer look."** *It is fine for a signal to
> have no measurable edge* — that is `CLAUDE.md`'s design premise, not a defect.
>
> **Discovery is the user's own research**, feeding `/screen` and `/deep`. Work that makes a
> *supplied* name easier to judge outranks work that measures forward returns.

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
  - **Lazy Prices: RETIRED 2026-08-20, TWO defects, both must be fixed to revive it.**
    The `## Filing-text change (Lazy Prices)` render is GONE and the `filing_text_change`
    config block ships `enabled: false`. `FilingBundle.text_similarity` is still computed
    and still stored in the brief JSON. Evidence: `docs/PLAN_INVENTORY_DECOMPOSITION.md` §0.4.
    1. **No producer.** Nothing writes `StockMetrics.filing_text_similarity` —
       `check_flags` runs inside `score()` (`scoring.py:809`) during `run_harness`, and
       research runs after it (`screen.py:188`, `:193`), so a research-layer producer is
       structurally too late for the screen path. Guarded by
       `tests/test_flag_producers.py::test_declared_flag_inputs_have_a_writer`
       (deliberately `xfail(strict=True)`).
    2. **The metric barely discriminates.** `research/textsim.py` retains stopwords, so
       the cosine compresses near the top of its range: unrelated companies' 10-Ks score
       **0.72-0.90** (NVDA vs PBT 0.7216, HDSN vs LULU 0.8973) against same-firm YoY
       **~0.997** — which is why every brief rendered "0% rewritten". A realistic
       same-firm rewrite cannot reach `max_similarity: 0.7`; even a de-SPAC, where the
       registrant's whole business changes between consecutive 10-Ks, scores ~0.90.
       Caution for anyone recalibrating: the low end is only 0.02 above the threshold,
       and a truncated extraction (~150 tokens) DOES cross it — so a naive threshold
       move would fire on extraction bugs. A real fix needs IDF/stopword weighting AND a
       cross-sectional reference distribution — Cohen-Malloy-Nguyen sort on the RANK of
       similarity, so a single-firm absolute cosine is uninterpretable regardless.
- **Brief self-consistency: MEASURED 2026-08-22, detector NOT built.** 2 of 17 briefs
  overall, **0 of 9** under the current 2026-08-18 materiality prompt — at or below the
  rate `TODO.md` itself pre-committed as "prompt-only clause, not a per-brief second call".
  Both confirmed hits are pre-prompt and both involve the **reconciliation** section, not the
  bull/bear boundary the motivating HDSN case suggested. Evidence, the pre-registered
  criterion, the five borderline cases a naive detector would fire on, and the reasons `0/9`
  is weak (rule-of-three upper bound ~33%; effective n≈4-5; the HDSN brief is absent from the
  corpus): `docs/audits/2026-08-22-brief-self-consistency-base-rate.md`.
  **What remains:** re-count once more post-prompt briefs on *fresh* tickers accumulate — 7 of
  the 9 are repeat runs over AAPL/JPM/INTC. Reopen the detector only if that count moves.
- **Cost of revenue from EDGAR — would make the /deep days-inventory leg FMP-independent.**
  `research/inventory.py` derives COGS as `revenue - gross_profit`, and `gross_profit` is
  year-joined in from FMP (`_merge_statements`) rather than extracted from EDGAR — measured
  2026-08-21, EDGAR supplies it on **0 of 1,474** EDGAR-only store snapshots, so this leg is
  dark on 69% of captures, not just on a 402/429 day
  (`docs/audits/2026-08-21-operating-income-edgar-gap.md`). On an
  FMP-gated (402) or rate-limited (429) run the DIO leg abstains and only the balance
  trend renders — the FISV 2026-08-21 run hit exactly this. Not free to fix: filers split
  between `us-gaap_GrossProfit` (LULU, non-dimensional) and `CostOfGoodsAndServicesSold`
  (HDSN, FISV; on LULU every such row is a segment breakdown, dimension=True), and
  supplying gross_profit from EDGAR would change a SCORED input (gross_margin) and its
  merge precedence. Needs its own design + evidence, not a drive-by.
- **`negative_fcf` inventory-build excusal arm — NOT built, measurement stated.** The gate is
  already stage-aware (it excuses fast growers); an inventory-build arm would be the same
  shape. Testable question: among negative-FCF names whose burn is fully explained by an
  inventory build, is forward realized FCF distinguishable from other negative-FCF names?
  Until that is measured the gate stands. NOTE the measurement must use a FULL
  working-capital cut, not inventory alone: for HDSN FY24->FY25, FCF ex-inventory
  "improves" +26.3M -> +32.7M while FCF ex-(inventory+AR+AP) DECLINES +26.7M -> +16.3M.
  Stripping the inventory outflow while keeping a +20.2M payables inflow manufactures
  the improvement. An advisory flag keyed on the inventory-only cut was specified and
  then CUT for exactly this reason. See `docs/PLAN_INVENTORY_DECOMPOSITION.md` §0.2, §1.1.
- **10-Q Part II Item 1A: SHIPPED 2026-08-14.** The quarter's risk-factor *changes* now reach
  the model as a diff against the 10-K Item 1A (`research/filings.py:_tenq_added_risks`,
  `config.yaml: research.tenq_risk_update`). Design + 25-filing probe evidence:
  `docs/audits/2026-08-14-tenq-part-ii-in-deep-design.md`.
- **`_tenq_mda` silently abstains on one filer in 35 (found 2026-08-14, pre-existing).**
  Both symptoms are edgartools item-boundary detection, not our call. Measured over 35 large
  caps (2026-08-14), and **both are now logged to stderr** by `_tenq_mda`
  (`fix/tenq-mda-diagnostics`) — the gap is observable, not fixed:
  - **The real defect: INTC extracts 0 chars (1 of 35)** — its Part I Item 2 heading is not
    detected, so the preceding Part I Item 1 span (135,783 chars) absorbs the MD&A and the
    brief carries no quarterly MD&A at all.
  - **Over-capture (3 of 35) is NOT harmful — do not "fix" it.** Closed verdict, with the
    numbers, in `docs/audits/README.md`.
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
  landmines are in `research/eightk.py`'s docstring; evidence is
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

- **The 2026-08-04 audit's prompt-only wins (D3/D6/D7 + do-the-arithmetic) shipped 2026-08-18
  and are LIVE-VERIFIED at n=3 (AAPL, JPM, INTC — 2026-08-19): HOLDS, with two open items.**
  Design + baselines: `docs/audits/2026-08-18-deep-prompt-materiality-and-arithmetic.md`.
  Measurement: `docs/audits/2026-08-19-deep-prompt-live-verification.md`. `risks` moved off
  the 33/35-at-cap spike (8–10 of 12 on all three) with no material risk category observed
  dropped — JPM specifically (the task's own worry case) still returned 8 well-distributed
  risks, not the feared 2–3. `red_flags` matched its closed category 2/2; JPM's empty list is
  a defensible boundary call (OCC consent order and CET1 thinning correctly routed elsewhere),
  not suppression. Still open: **quote reuse dropped but didn't hit zero** (1 violation in 3
  briefs, AAPL `risks`+`reconciliation`, down from 62/35); **`what_would_change_my_mind` still
  saturates** (2/3 at its cap of 6 — the materiality bar reached `risks` and `reconciliation`
  but not the falsifier list); and **the arithmetic clause is UNTESTED, for a structural reason,
  not a sampling one.** 0/3 briefs computed anything because 2 of its 3 asks were unanswerable:
  the prompt rendered debt WITHOUT cash, so neither cash runway nor even net debt was derivable,
  and the maturity ladder lived in a note we did not extract. **Both inputs have since landed** —
  cash is a rendered column (2026-08-19) and the maturity ladder now reaches the prompt as a
  statement note (2026-08-20, §2b) — so the next live run is the first real test of all three
  asks, refinancing coverage included. Nothing blocks it but a run. n=3 on three large caps
  is not enough to re-certify D3/D6/D7 at the rigor of the original 35-brief corpus — re-run
  the keyword/substring scans over a larger corpus once more new-prompt briefs accumulate.

## 2b. Filing content we do not extract (bigger, genuinely missing)

**Debt & liquidity (was item 2) SHIPPED 2026-08-20** — `research/notes.py`, config
`research.notes`. Behaviour and landmines are in the module docstring; design + the
20-filing probe is `docs/audits/2026-08-20-debt-liquidity-notes-design.md`. Two things
it settled that the remaining items inherit:

- **`TenK.notes` is an XBRL-derived STRUCTURED INDEX**, not a text blob, so a targeted
  note extractor needs no heading detection and carries none of the `_tenq_mda`
  item-boundary fault class. Build the rest the same way.
- **Title matching needs a per-family exclusion filter.** DUK's `Investments in Debt and
  Equity Securities` is an *asset* note that matched `debt`. Expect one per family.

What remains: (1) segments + disaggregated revenue, (3) SBC & dilution, (4) concentrations
& commitments, (5) acquisitions/goodwill, (6) legal contingencies. Still **targeted
extractors, never "send all notes to Claude"**, and still ordered by decision value.
Note that none of these has a shipped prompt instruction waiting on it, which is what made
debt & liquidity first — so they are worth *less* than their position here suggests, and
rank below §2c.

## 2c. Peers / market context — the review's headline gap

A peer bundle (5–10 SIC/NAICS + size + revenue-mix matched names; growth, margins, ROIC,
share-count growth, valuation, relative performance) is the review's top recommendation, and it
is also **the enabling data for `docs/ASSESSMENT_GAPS.md` §2.3** (sector-relative percentiles
replacing absolute bands) — do not re-spec §2.3 from scratch. Take the review's own constraint:
**display it in `/deep`, do not feed it into the score** until it has cross-universe evidence.
Cost is the blocker, not design: N peers × the per-ticker call budget against a 250/day FMP free
cap (§4). The keyless route is SEC `companyfacts`/`frames`, which is also what `--source xbrl`
already reads.

## 2d. Recorded, NOT endorsed — moved

The four positions this repo considered and declined now sit in
**`docs/audits/README.md`** → *Argued by an external review, recorded but NOT endorsed*.
Only the one live item stays open, below.

- **The `upside_to_target` counterfactual is unmeasured.** The leg is now the scorer's one
  opt-OUT block (`scoring.py:_upside_to_target_on`), so leg-off can be run from config, but
  nobody has scored the leg-off universe against forward returns point-in-time. Flipping the
  default without that test is the move this file's own bar forbids.

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
- **`pe_vs_history` cannot validate on this path either, for the same reason (2026-08-21).**
  `monthly_closes` is EMPTY on **all 2,146** stored snapshots: the deployed timer runs
  `--sources fmp,finnhub,edgar` (and the CLI default is narrower still, `fmp,finnhub`), so
  Yahoo — the only source that populates it — never runs in accumulate. `pe_median_5y` is
  derived in the bridge from those closes, so a scored `value` leg is structurally absent from
  every replay. Adding `yahoo` to the chain is the obvious fix and is NOT free: it is a
  per-ticker quota and runtime cost on a timer already over-subscribed (§4), and it changes the
  deployed unit.
  **Status:** measured, not actioned — the fix is a deployed-timer change whose cost sits
  inside the §4 quota decision, so it should be taken with that decision rather than before it.

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
- **DEF 14A pay-vs-performance axis.** The XBRL path is a closed NO-GO
  (`docs/audits/README.md`); it can only be built via snapshot-replay once accumulation
  captures `research/proxy.py`'s PvP extraction point-in-time. Phase 2 also holds
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
**23 of 60 store dates have ZERO fmp-won statements** (re-measured 2026-08-21 over 2,146
snapshots; FMP wins only 672 of them). This is the **sole** driver of the EDGAR-only
`operating_income` gap — 100% of fmp-won snapshots carry it against 62.8% of EDGAR-only ones,
and no EDGAR-side change can close more than 12% of that
(`docs/audits/2026-08-21-operating-income-edgar-gap.md`). EDGAR supplies ~100% of production
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
`data/sources/fmp.py` and `config.yaml:256` both record EDGAR as the free authoritative source
for insider data (FMP's insider endpoint is paid, 402 on free). So enabling a paid
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
- Parked observation: `Fundamentals.operating_margin`/`current_ratio` are extracted but
  consumed nowhere. They ride along in an existing batch call, so they cost no request —
  removing them is churn against the stored snapshot schema, not a saving.

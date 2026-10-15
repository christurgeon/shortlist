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

- **The arithmetic clause fires UNIVERSALLY, and should probably fire selectively
  (2026-08-23).** The 2026-08-22 fix took refinancing coverage from 1/4 to 3/3 on leveraged
  names by making it REQUIRED whenever a maturity ladder is present. The AAPL control shows
  the cost: it computed 12x coverage nobody needed and pushed `reconciliation` — a list
  specified as sparse — from 5 of 6 entries to its cap of 6. The refinement is to require the
  computation but let a comfortably-covered name carry it as a clause in an existing entry or
  the thesis, instead of a dedicated row. Do NOT take this as a cheap prompt tweak: the same
  "REQUIRED, no exceptions" bluntness is what produced the 3/3, and a softened version could
  revert it. Evidence and the exact wording:
  `docs/audits/2026-08-22-deep-arithmetic-clause-verification.md`.
  **Status:** deferred, not blocked on a decision — blocked on *evidence*. Judging whether a
  slot was genuinely displaced needs briefs on cash-rich names that this session could not
  manufacture (n=1 control, one run). Revisit once several accumulate; re-measure the
  leveraged 3/3 in the same pass, because that is what a softened clause would put at risk.

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
- **`_tenq_mda` silently abstains on one filer in 35 (found 2026-08-14, pre-existing).**
  Both symptoms are edgartools item-boundary detection, not our call. Measured over 35 large
  caps (2026-08-14), and **both are now logged to stderr** by `_tenq_mda`
  (`fix/tenq-mda-diagnostics`) — the gap is observable, not fixed:
  - **The real defect: INTC extracts 0 chars (1 of 35)** — its Part I Item 2 heading is not
    detected, so the preceding Part I Item 1 span (135,783 chars) absorbs the MD&A and the
    brief carries no quarterly MD&A at all.
  - **Two measured traps, pinned by regression tests.** `tenq["Item 2"]` is not a fallback —
    on INTC it returns 2,459 chars of *Part II* Item 2 (share repurchases), wrong content
    silently labelled MD&A. `tenq.items` is not a guard — XOM lists an unqualified `Item 2`,
    TSLA three entries, MCD exactly one, yet `get_item_with_part("Part I","Item 2")` returns
    69,820 / 49,879 / 122,045 chars for them, so an `items` check reports phantom failures.
  - **Recovery is deferred**, not forgotten: slicing the containing Part I Item 1 blob at an
    MD&A heading is fitted to n=1 and would inject wrong text into the grounding haystack.
    Needs a wider probe first. Same class of fault as INTC's Part II Item 1 returning 71,869
    chars of Note 14.
- **`business_model_summary` is still bare prose** — the last narrative section with no
  grounding standard, deliberately left out of the 2026-08-17 moat/management evidence cut
  (`docs/audits/2026-08-17-moat-management-evidence-design.md`, which is also the template
  for closing it). That cut is live-verified at n=3 only (AAPL/JPM/INTC, 2026-08-18).

## 2c. Peers / market context — the review's headline gap

A peer bundle (5–10 SIC/NAICS + size + revenue-mix matched names; growth, margins, ROIC,
share-count growth, valuation, relative performance) is the review's top recommendation, and it
is also **the enabling data for `docs/ASSESSMENT_GAPS.md` §2.3** (sector-relative percentiles
replacing absolute bands) — do not re-spec §2.3 from scratch. Take the review's own constraint:
**display it in `/deep`, do not feed it into the score** until it has cross-universe evidence.
Cost is the blocker, not design: N peers × the per-ticker call budget against a 250/day FMP free
cap (§4). The keyless route is SEC `companyfacts`/`frames`, which is also what `--source xbrl`
already reads.

## 2d. ROIC-persistence section — the two open halves

- **A reference-table refresh builder, due before fiscal-2027 10-Ks (about September 2027).**
  After that every name abstains as `reference_out_of_date`. Do not run `probe_durability.py
  fetch` to make one, and do not raise `max_table_gap_years`: `docs/RESEARCH.md` → The ROIC-persistence
  section, and `docs/audits/2026-10-08-moat-durability-phase1.md` → The reference year.
- **Phase 2, the line to the model**, needs its own design and plan:
  `docs/audits/2026-10-08-moat-durability-phase1.md` → For a Phase 2 that gives the line to
  the model.

---

# 3. Measurement (backtest + snapshot replay)

Ordering note: everything here answers an alpha question, which the prioritisation rule at the
top ranks *below* work on judging a supplied name. Take these when they are cheap or when they
unblock something.

## Shipped weights still unvalidated or failing (2026-09-25)

- **`insider` (weight 0.135) and the `heavy_insider_selling` gate.** Neither has ever been
  measured: insider history is not in companyfacts, so the only route is snapshot replay. Both
  are driven mostly by *sales* (Finnhub MSPR), the side the literature finds uninformative
  (Lakonishok-Lee 2001; Cohen-Malloy-Pomorski 2012 — purchases predict, routine sales do not).
  On the 2026-09-24 store the gate removed **6 of 42** large caps, including NVDA, the top
  composite at 77.4, plus COST, CSCO and QCOM; `insider_sentiment` sat at the 0 clamp for 17
  of 42. The candidate change is weight down and gate → flag, but that is an argument, and the
  register's rule needs evidence first.
- **`roe` saturates.** 19 of 42 large caps sit at the 100 clamp (p90 ROE 0.96); buybacks shrink
  equity, so the leg mostly measures capital structure. Measure a replacement band or leg on
  `--source xbrl` before changing it — `quality` IC is ~0 on both universes, and survivorship
  bias works against it, so a band change cannot be judged by IC alone.

- **`moat` (weight 0.18) is negative on both universes at every horizon** (XS t −0.4 to
  −1.6), the same sign failure that zeroed `risk`. It was kept on magnitude and role, which is
  a judgement (`docs/audits/2026-09-25-risk-tilt-disable.md`, "Why risk, and not moat"). A
  weight change needs a pre-registered `no_moat` variant through
  `docs/audits/scripts/probe_composite_weights.py`, not a post-hoc read of the existing runs.

**Status:** open. The insider half is blocked on calendar time: the store holds 42 names and
~3 non-overlapping monthly periods, far below the 24-period floor. The `roe` and `moat` halves
are measurements nobody has run; each is a separate pre-registration, and neither fitted
into this session without becoming a post-hoc variant search. Evidence of the shipped weights' state:
`docs/audits/2026-09-25-risk-tilt-disable.md`.

## Snapshot-replay path: live, with two standing constraints (2026-08-09)

`--source snapshot` is un-gated and smoke-tested. What survives is the part that constrains
*future* runs:

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
(the store then loses `gross_profit`, which only FMP supplies); or paid **Starter**
(~$14–20/mo).

The free window is **not calendar-UTC-day aligned** (measured 2026-07-31: still "Limit Reach"
42 min after UTC midnight, drained by our own 21:30 accumulate timer). Retry probes in the
**mid-day UTC window**.

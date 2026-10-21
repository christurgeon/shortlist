# Qualitative research (`--research N`)

An opt-in layer that enriches the top N non-gated names with a Claude-written brief grounded
in the company's own filings. It **stands alongside** the numeric score and never re-ranks.

```bash
uv run shortlist --tickers GEV,LMT,GOOGL --research 3
```

In the Telegram bot, `/deep TSLA` runs the same layer — see [`TELEGRAM.md`](TELEGRAM.md).

> **Screening triage, not investment advice.** Briefs are LLM-generated aids for a human deep
> dive, labeled as such everywhere they surface.

## How it runs

It shells out to the local **`claude` CLI in headless mode** (`research/claude_cli.py`) — not
the API SDK — so it uses your existing CLI auth and needs **no API key**. Requires `claude` on
`PATH` and the `[edgar]` extra (`uv sync --extra edgar`). The whole module is lazily imported,
so the core screener works without either.

The CLI is locked down to behave as a stateless model call rather than an agent:

| Flag | Why |
|---|---|
| `--tools ""` | No tool access |
| `--strict-mcp-config` | No ambient MCP servers |
| `--max-turns 1` | Single turn, no agent loop |
| neutral `cwd` (tempdir) | No ambient `CLAUDE.md` or hooks leak into the prompt |
| prompt on **stdin** | Avoids argv limits and quoting bugs |

**Never add `--bare`** — it forces `ANTHROPIC_API_KEY` and breaks the keyless path.

The model and its fallback are set in `config.yaml: research` (`model`, `fallback_model`).

## What's in a brief

The bundle sent to the model is the latest **10-K** (business, MD&A, risk factors), the latest
**10-Q's MD&A**, a **YoY Item-1A risk-factor diff** (`riskdiff.py`), the substance of up to
three recent **8-Ks** (`research/eightk.py`), and the **debt & liquidity statement notes**
(`research/notes.py`).

The debt notes feed one prompt instruction: `SYSTEM_PROMPT` asks the model to compute
**refinancing coverage** — debt maturing within twelve months against cash plus operating
cash flow — and the maturity ladder is usually disclosed in a statement note. Notes come from
`TenK.notes` / `TenQ.notes`, an **XBRL-derived structured index**, so there is no heading
detection. They are selected by title (debt, borrowings, credit facilities and similar, minus
investment notes) and capped at 16K chars each. Like the 8-Ks, this is **quotable filing
text that enters the haystack** as its own labelled segment (`10-K note: LONG-TERM OBLIGATIONS`).

- A selected note does **not guarantee a maturity ladder**; some filers put it in MD&A. The
  prompt then tells the model to fall back to balance-sheet short-term debt and name its date.
- **No 10-Q debt note is normal**, not a failure: the quarterly note is often a subset of the
  annual one, and the 10-K is the backbone.
- An over-long note is cut at the last whitespace, never mid-number. The truncation is noted
  in the section header, not inside the note, because anything in the note itself is
  grounding text the model could quote and have "verified".

Design and evidence: `docs/audits/2026-08-20-debt-liquidity-notes-design.md`.

The 8-Ks are what keeps a brief current between quarterly filings — an earnings release and its
guidance, a non-reliance restatement, a material cyber incident, a completed acquisition, an
officer departure. Selected by item priority (`4.02 > 1.05 > 2.02 > 2.01 > 1.01 > 5.02`),
capped at 10K normalized chars, and — unlike everything in the next list — **quotable filing
text that enters the haystack**, as its own labelled segment.
Design and evidence: `docs/audits/2026-08-13-eightk-text-in-deep-design.md`.

An **adverse internal-control conclusion** is included when the 10-K carries one
(`research/controls.py`). This is split deliberately across the grounding boundary: the
filer's own sentence is quotable filing text and enters the haystack as its own segment
(`10-K controls conclusion`), while the derived verdict — which conclusion was adverse,
and the date it anchors to — rides the prompt-only context line, because a computed
verdict a model could quote would pass quote-verification as a filing fact.

The detector reads the **whole filing text**, because the conclusion lives in Item 9A, which
`FilingText` does not extract. That costs about two extra sec.gov requests per brief. Only an
adverse conclusion anchored to the filing's own `period_of_report` counts: a bare "material
weakness" match, or a remediated weakness from a prior period, does not. When there is no
finding the prompt is byte-identical.
Evidence: `docs/audits/2026-08-23-icfr-adverse-conclusion-detection.md`.

Several **prompt-only context lines** ride along. These are deliberately kept out of the
quote-verification haystack, so a computed value can never pass itself off as a filing fact:

- A reverse-DCF "price-implied FCF growth" reframing (`research/reverse_dcf.py`). The reader
  also sees it, as the report's **Implied growth** cell, computed from the same FCF series and
  config; the cell is hidden for sector-masked names and current FCF burn, the prompt keeps it.
- Recent SEC filings and recent insider Form-4 trades
- DEF 14A pay and governance fields (`research/proxy.py`)
- Government contract awards, federal lobbying, earnings execution history, macro regime
- The **options surface** — implied volatility against realized, the move priced into the
  next earnings print, and 25-delta skew (`research/options.py`, fed by
  `research/earnings_moves.py`). Keyless CBOE end-of-day quotes, fetched per deep dive and
  deliberately **not** in `harness_sources`: `/screen` renders none of it and would spend
  a rolling per-IP request budget to fetch it. Every item carries a measured large-cap
  reference distribution, because a single firm's skew or implied/realized ratio is
  uninterpretable alone — the trap that retired the Lazy-Prices cosine. Items abstain
  independently and the whole line abstains on thin quotes, which is most small caps.
  The implied move is a **near-print** signal by construction: weekly expiries run ~6
  weeks out and then jump to monthlies, so a print further out usually has no listed
  expiry that safely brackets it. Evidence and every constant:
  `docs/audits/2026-08-24-options-surface-design.md`.
- Sell-side rating **revision** — the change in buy/hold/sell counts over the
  recommendation window (`research/analyst_revision.py`). Deltas only, never a level:
  the levels merge across sources while the deltas come from one Finnhub payload, so
  printing them together would pair one vendor's analyst panel with another's. The
  flat case renders as "unchanged" rather than abstaining — with no line at all the
  model cannot tell a still consensus from an unfetched one.

The output covers moat read, material risks, red flags, management and capital allocation,
business model, a falsifiable thesis (bull / bear / what-would-change-my-mind), and a
score-vs-filing reconciliation.

### The ROIC-persistence section — computed, not written by the model

On by default (`research.durability.enabled`). Every brief carries one more section,
**"ROIC persistence"**, in the markdown brief and in the Telegram HTML report. The model did
not write it and never sees it (the capital-allocation and recent-news sections below are the
others).

- **What it is.** A pre-registered study measured, among firms with a ROIC in the top fifth,
  which ones still had it three years later
  (`docs/audits/2026-10-04-moat-durability-verdict.md`). Two of six predictors passed: low
  growth of invested capital, and a steady ROIC rank. The section shows the historical
  pattern, and the company's **third** among top-fifth firms on each of the two. It never
  gives a probability for the company and never a raw spread.
- **What it is not.** Not a score, a gate, a flag or a discovery list, and not a claim about
  the business or about returns. The verdict licenses a display line only. The heading is
  "ROIC persistence", never "moat", and each bullet carries the caveat the verdict attaches
  to it: the base rate leaves out firms that stopped reporting, a little over half of the
  capital-growth effect is capital that kept growing slowly, and steadiness is a track-record
  measure whose later result rests on two cohort years.
- **When it is not shown.** Most names are not in the top fifth. A brief then carries one
  sentence, "Not shown: …", with the reason from a closed set (below the cutoff, a bank or
  REIT, no 10-K data, a year the study's rules exclude, SEC data unavailable, and so on;
  `durability_profile.NOT_SHOWN`). Silence would read as "nothing to report".
- **Where it is computed.** `research/__init__.py:_enrich_card`, **after** `assess()`
  returns. `research/assess.py` does not know the section exists, so both prompts are
  byte-identical with the flag on or off, and the section is never a grounding segment.
  Giving the line to the model is a separate decision: its risk is misuse (a "confirmed
  moat" argument), which only a measured A/B can bound
  (`docs/audits/2026-10-08-moat-durability-phase1.md`).
- **The same basis as the study.** One request to `data.sec.gov` company facts per brief,
  through the process-wide throttle, compacted with the study's own `compact_facts`, read with
  the study's own functions (`shortlist/durability.py`) at the study's point in time (the year
  end plus 120 days, or today when that is sooner). A test recomputes the profile of every
  firm in the reference cohort and compares it with what the study path gives.
- **The reference table.** `src/shortlist/durability_table.json` holds the ROIC universe and
  the top-fifth cohort of **fiscal 2025**, and the measured effects, which are copied from the
  study's raw outputs. A name one fiscal year past the table is ranked against it and the
  section says so; further out it is not shown, and neither is a name whose latest year on
  file is before the table's (a company in the weeks before its new 10-K). **A refresh on new
  SEC data is not built.** Do not run `probe_durability.py fetch` to make one: it overwrites
  two evidence files of the study. Do not raise `max_table_gap_years` to keep an old table
  alive. The table lasts until fiscal-2027 10-Ks, from about September 2027
  (`docs/audits/2026-10-08-moat-durability-phase1.md` → The reference year).
- **Failure.** `fetch_section` never raises. A bad response, malformed facts or a missing
  table give "Not shown: SEC data could not be read" and one redacted stderr line. The request
  runs in a daemon thread and the caller waits `deadline_s` for it (at most 45 s, whatever the
  config says): an httpx timeout is per phase and per read, and is not a deadline. A brief makes
  at most one request. A brief is cached for the day with the section it
  was written with, so a section that failed stays until the day bucket turns or `--refresh`.

### The capital-allocation section — computed, not written by the model

Always on. Every brief carries **"Capital allocation (computed from SEC data — not
LLM-generated)"** under the model's "Management & capital allocation" prose, in the markdown
brief and folded in the Telegram HTML report (`research/capital_allocation.py`).

- **What it is.** Accounting data over the fiscal years on the card: operating cash flow,
  capital spending (operating cash flow minus free cash flow) and free cash flow summed over the
  newest run of consecutive years; the latest year's buybacks and dividends and their share of
  that year's free cash flow; the change in diluted share count; ROIC per year; and incremental
  ROIC (ΔNOPAT / Δinvested capital) with the reinvestment rate (Δinvested capital / NOPAT of
  the years after the start). ROIC is the bridge's computed basis (`bridge.py:ROIC_TAX_RATE`,
  debt plus equity), not a fundamentals-source figure.
- **What it is not.** Not a score, gate or flag, and no claim about returns or quality.
- **Abstention.** Each line abstains on its own and says why. Incremental ROIC and the
  reinvestment rate need two consecutive years and abstain when invested capital fell or grew
  by less than 5% of its starting level (`MIN_IC_GROWTH`); a ratio beyond ±100% prints as a
  bound. The payout share abstains when free cash flow is not positive. A bank, insurer or REIT
  (the card's sector mask on the ROIC or FCF-yield leg) gets "Not shown: not meaningful for
  this sector"; a name where nothing resolves gets one "Not shown" sentence.
- **Only the latest year of buybacks and dividends.** The harness extractor
  (`providers/_edgar_facts.py`) keeps one fiscal year of the financing tags, so the section
  cannot sum them over the window. A per-year series is an extraction change.
- **Where it is computed.** `_enrich_card`, after `assess()` returns, from data already on the
  card; no request. It is stored with the brief (`capital_allocation_line` in the JSON record).
  The module is not in `cachekey._PROMPT_MODULES`, so the prompt fingerprint does not cover it:
  a wording change reaches new briefs when the day bucket turns.

### The recent-news section — third-party headlines, not seen by the model

On by default (`research.news.enabled`). Every brief carries a section **"Recent news — third-party, not
verified"**: up to `max_items` event clusters from Finnhub `company-news` over
`lookback_days`, each with its first-report date (UTC), outlet, report count, linked headline
and a short summary. The free feed returns only its newest ~250 articles, which for a
high-volume name is a few days, not the whole window; the section then prints the span it
really covers. Design, evidence and the gate for any model use:
`docs/audits/2026-10-16-deep-news-leads-design.md`.

- **Why.** The bundle's freshest text is the 10-Q and up to three 8-Ks of five item types.
  Most company events of the last month — litigation, regulators, recalls, trial readouts,
  deals below 8-K materiality, analyst rating changes — are in none of them.
- **What it is not.** Not filing text, not verified, not a score, gate or flag. The outlet is
  often a syndicator, so the section says provenance is weak. A named event ranks first, then
  the report count; the count ranks, it does not vouch.
- **The filter** (`research/news.py:select`, deterministic): the headline must name the
  company (a ticker of three characters or fewer only in a form like `(ON)`); opinion sources,
  listicles, roundups, previews, call notices, transcripts, Zacks-rank and price-target notes and
  dividend columns are dropped, while a broker rating change is kept; an opinion-shaped headline
  survives only if it names a strong event; articles about one event become one item, shown
  under its strongest headline: first by shared words, then by one merge pass on similar wording,
  a shared results or layoff event, or a deal with the same counterparty, which never folds a
  denial into the report it answers; a quiet name shows fewer items, never filler.
- **Where it is computed.** `_enrich_card`, **after** `assess()` returns, like the
  ROIC-persistence section. The prompts are byte-identical with the flag on or off, and news
  text is never a grounding segment. Feeding it to the model is gated on the design's
  pre-registration (§9) and is not built.
- **Failure.** One Finnhub request per brief, key in a header, no retry, under a wall-clock
  deadline (`timeout`, at most 45 s) enforced by a daemon thread. Any failure prints
  "unavailable", never an empty section, so an outage cannot read as "no news". A cached
  brief keeps the news as of its own generation and prints that time.

## Quote verification

Factual findings — risks and red flags — must carry a **verbatim quote that is verified to
actually appear in the filing text**. Findings whose quote cannot be located are flagged as
unverifiable rather than presented as fact. Interpretive prose is labeled as interpretation.

Verification is **per document, not per corpus**: a quote is matched against one labelled
segment and the finding records which one, so the brief can say *verified against 8-K
2026-08-10 (Item 5.02, body)* rather than letting "verified" quietly mean "somewhere in the
pile". A quote stitched across two documents therefore does not verify.

## The screening call

Each brief ends with a **buy/hold/avoid stance plus a conviction level**, bounded by three
deterministic guards in `assess.py:apply_guards`. The guards run in code, after the model
returns — the model cannot talk its way past them.

1. **Gate clamp.** A tripped gate imposes a stance ceiling and can only ever move the call
   *bearish*. `negative_fcf` and `over_leveraged` clamp to **AVOID**; any other gate clamps to
   **HOLD**. A clamp also caps conviction at MEDIUM and records which gates caused it.
2. **Conviction cap on thin data.** Confidence below `0.45` (or missing) caps conviction at
   **LOW**; below `0.70` caps it at **MEDIUM**. Deciding without applicable data
   (`decided_without`) independently caps at MEDIUM.
3. **HIGH-conviction corroboration.** A HIGH call that isn't corroborated — or that carries a
   contra-flag such as `value_trap` — is demoted to MEDIUM.

The guard *input* (confidence) is snapshotted next to the guard *output*
(`conviction_capped`), so a later retrospective can attribute a conviction to a rule instead
of inferring it. Tune under `config.yaml: research.screening_call`.

## Caching and output

Briefs are written to `research/<TICKER>/<key>.md` (plus a `.json` sibling), where `<key>` is
the **WIDE cache key** (`research/cachekey.py:brief_key`), not the bare filing accession: it
folds in the filing accessions, a fingerprint of the prompt-shaping module sources and the
`research` config block, a bucketed quant/event context digest off the `ScoreCard`, and an
as-of day bucket (`research.cache.{max_age_days,price_band_pct}`). A
re-run against the same filings is only served from cache when the prompt, config and context
all still match — so editing the prompt/guards/`research.max_chars`, or a material price move,
invalidates the cache instead of serving a stale brief. `--refresh` always regenerates. The
`research/` output directory is gitignored.

## Kill-switch

To skip the research phase without redeploying:

```bash
touch research/STOP_RESEARCH     # file-based; persists across restarts
SHORTLIST_NO_RESEARCH=1 ...      # env var; one process
```

Both are checked by `research/phase.py`.

## Known limitation

Briefs are **10-K only**. Foreign private issuers that file a 20-F (NVO and similar) get an
ADR-aware skip rather than a brief.

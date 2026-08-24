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

Default model is `claude-sonnet-5` with `claude-opus-5` as fallback (`config.yaml: research`).

## What's in a brief

The bundle sent to the model is the latest **10-K** (business, MD&A, risk factors), the latest
**10-Q's MD&A**, a **YoY Item-1A risk-factor diff** (`riskdiff.py`), the substance of up to
three recent **8-Ks** (`research/eightk.py`), and the **debt & liquidity statement notes**
(`research/notes.py`).

The debt notes exist to feed one specific prompt instruction. `SYSTEM_PROMPT` asks the model to
compute **refinancing coverage** — debt maturing within twelve months against cash plus
operating cash flow — and the maturity ladder is disclosed in a statement note, which nothing
used to send. They are read from `TenK.notes` / `TenQ.notes`, an **XBRL-derived structured
index** of individually addressable notes, so there is no heading detection and no span slicing.
Selected by title (`debt|borrow|credit facilit|credit agreement|financing arrangement|notes
payable|long[- ]term obligation`, minus an `investment|marketable securit` exclusion), capped at
16K chars per note. Like the 8-Ks, this is **quotable filing text that enters the haystack** as
its own labelled segment (`10-K note: LONG-TERM OBLIGATIONS`).

A selected note does **not guarantee a maturity ladder**. Boeing's `Debt` note — the only one
in its 28-note index matching the title rule, picked from both forms and untruncated — carries
a finance-lease schedule and undrawn credit capacity but no principal-maturity table, which
lives in its MD&A liquidity discussion instead. The 20-filing probe measured selection, not
contents, so the rate is unknown. `SYSTEM_PROMPT` covers the case: the model falls back to
balance-sheet short-term debt and names the date it applies to
(`docs/audits/2026-08-22-deep-arithmetic-clause-verification.md`).

Two behaviours worth knowing. A filer that files **no 10-Q debt note is the normal case**, not a
failure — 5 of 20 measured file none, because the quarterly note is a legitimate subset of the
annual one; the 10-K is the backbone. And an over-long note is cut at the last whitespace — never
mid-number — with the truncation flagged in the prompt's section header rather than inside the
note text, so the model can tell a severed ladder from a complete one and name the missing input
rather than estimate it. Nothing but filing text goes in the note itself: it is a grounding
segment, and a marker mixed into it would be non-filing text a model could quote and have
"verified".
Design + 20-filing evidence: `docs/audits/2026-08-20-debt-liquidity-notes-design.md`.

The 8-Ks are what keeps a brief current between quarterly filings — an earnings release and its
guidance, a non-reliance restatement, a completed acquisition, an officer departure. Selected by
item priority (`4.02 > 2.02 > 2.01 > 1.01 > 5.02`), capped at 10K normalized chars, and — unlike
everything in the next list — **quotable filing text that enters the haystack**, as its own
labelled segment. Measured on NKE it surfaced a chief accounting officer's resignation and a
one-time benefit behind a 407% net-income jump, neither present in the 10-K.
Design + evidence: `docs/audits/2026-08-13-eightk-text-in-deep-design.md`.

An **adverse internal-control conclusion** is included when the 10-K carries one
(`research/controls.py`). This is split deliberately across the grounding boundary: the
filer's own sentence is quotable filing text and enters the haystack as its own segment
(`10-K controls conclusion`), while the derived verdict — which conclusion was adverse,
and the date it anchors to — rides the prompt-only context line, because a computed
verdict a model could quote would pass quote-verification as a filing fact.

The detector reads the **whole filing text**, not the narrative sections: the conclusion
lives in Item 9A, which `FilingText` does not extract, and edgartools' Item 9A accessor
returns nothing at all for some filers. That costs ~2 extra sec.gov requests per brief.
A bare `"material weakness"` search is worthless (it matched 226 of 228 filers), and the
adverse phrasing alone is not enough either — the dominant false positive is a
prior-period weakness, since remediated, restated in a later filing. Only a conclusion
anchored to the filing's own `period_of_report` counts. Roughly 5% of large and
small/mid caps and 10% of $300M-$5B names carry one; everyone else pays nothing, because
the absent finding leaves the prompt byte-identical.
Evidence: `docs/audits/2026-08-23-icfr-adverse-conclusion-detection.md`.

Several **prompt-only context lines** ride along. These are deliberately kept out of the
quote-verification haystack, so a computed value can never pass itself off as a filing fact:

- A reverse-DCF "price-implied FCF growth" reframing (`research/reverse_dcf.py`)
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
  independently and the whole line abstains on thin quotes, which is most small caps
  (the implied move clears its guards on 71 of 80 large caps against 10 of 77 small/mid).
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

## Quote verification

Factual findings — risks and red flags — must carry a **verbatim quote that is verified to
actually appear in the filing text**. Findings whose quote cannot be located are flagged as
unverifiable rather than presented as fact. Interpretive prose is labeled as interpretation.

Verification is **per document, not per corpus**: a quote is matched against one labelled
segment and the finding records which one, so the brief can say *verified against 8-K
2026-08-10 (Item 5.02, body)* rather than letting "verified" quietly mean "somewhere in the
pile". A consequence worth knowing: a quote stitched across two documents no longer verifies —
that is stricter than before, and deliberately so.

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

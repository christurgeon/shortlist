# Adversarial assessment of the `/deep` research brief

> **STATUS (2026-08-04): the six `cheap-and-certain` items in §7 are WRITTEN BUT UNCOMMITTED** —
> working-tree changes on local branch `feat/deep-brief-improvements` (0 commits ahead of
> `main`; not merged, not pushed, not deployed). D1 (`_norm` fold), D2 (valuation line), D4
> (persist `confidence`), D5 (8-K item codes), D8 (macro line), D10 (`--fallback-model`).
> Ruff clean, 2315 tests pass. Verified end-to-end against the real AAPL 10-K: the production `_norm`
> now recovers **8 of 11** persisted-unverified findings (the other 3 are 10-Q-sourced and
> untestable — that 10-Q has been superseded), matching the independent measurement in D1.
> **Everything in `worth-building` / `needs-measurement` is NOT built.** Briefs are cached by
> accession, so existing briefs are unchanged until re-run with `--refresh`.

**Date:** 2026-08-04 · **Scope:** the `/deep` brief — documents assembled, prompt, guards, rendered output.
**Out of scope:** the scorer, composite legs, discovery signals (see `CLAUDE.md` → "Design premise").
**Corpus:** 35 brief artifacts, 2026-06-07 → 2026-08-04, 31 unique tickers.

Method note: every number below is reproducible. Measurements were produced by re-reading the
persisted artifacts and, for the causal grounding test, by re-fetching live filings through the
repo's own `research.filings.fetch_bundle`. Where a test was invalid or a claim rests on n=1, it
is flagged **inline**, not in a footnote.

---

## 1. Verdict

**`/deep` is good enough to inform real-money decisions today, but only as the *qualitative* half
of a decision — and it is currently missing the other half so completely that the omission is the
headline finding.** The brief is genuinely strong at what it was built to do: extract material,
company-specific, quote-grounded facts from a 10-K and 10-Q and reconcile them against the
screener's numbers. The reconciliation section is not decoration — on WDC it caught that the
screener's 55.3% net margin was an artifact of non-cash Sandisk mark-to-market gains and named the
35.7% HDD operating margin as the real number, which is a screener defect a human would plausibly
have missed. On ASTS it surfaced going-concern language, a $155–160M satellite write-off, penny
warrants, a ~$100M induced-conversion expense, and a $1.075B post-period convertible issuance — a
distress inventory that would take an experienced analyst an hour to assemble by hand. That is real
value and it is being delivered for a median of $0.63 per brief.

**Where it is thin: the brief never tells you what the company costs.** `_quant_context` renders
nine fundamentals and not one of them is a valuation input — no P/E, no FCF yield, no PEG, no market
cap, no price — even though all of those already exist on `StockMetrics` and are already fetched.
The model is asked to reconcile a `value` sub-score while seeing only an opaque `value 0`, and it
shows: **1 of 35 briefs cites any valuation multiple.** A document that ends in a buy/hold/avoid
call while structurally unable to discuss price is not a complete decision aid, and this is the
cheapest defect in the report to fix. **Where it is actively misleading:** the `_(unverified)_`
marker, which is the brief's only integrity signal, is wrong most of the time it fires — folding
typographic punctuation recovers 73% of unverified findings, and the dominant cause is a single
character (U+2019) in the *filing* text. Readers who learn to ignore that marker will also ignore it
on the cases where it is correctly flagging a fabricated composite quote, of which this corpus
contains at least two. Separately, **conviction is not a signal**: it is MEDIUM on 28 of 32 calls,
and — contrary to the hypothesis that the guards are compressing it — `conviction_capped` is `false`
on **all 32**. The guards never once changed a conviction. The model self-selects MEDIUM. You should
read conviction as decoration until that changes.

---

## 2. The three-brief read

Read end to end as an operator would: AAPL (mega-cap), WDC (cyclical), ASTS (speculative).

### AAPL — `0000320193-25-000079+0000320193-26-000013`

**What I'd do differently having read it:** little. The brief's own conclusion — a quality franchise
where the market embeds ~8%/yr perpetual FCF growth against a realized −0.41% 3-year FCF CAGR — is
the right tension to hold, and it is stated crisply. But the brief cannot tell me whether AAPL at
today's price is a 28× or a 38× multiple, so "limited upside margin of safety at current prices" is
an assertion I cannot audit from the document. That is the valuation gap in miniature.

**What I couldn't have gotten in five minutes from a free finance site:** the Q2-2026 supply-constraint
disclosure (advanced semis / NAND / DRAM, management explicitly saying it expects the trend to
*intensify*), and the observation that R&D rose 34% YoY against 17% revenue growth. Both are
10-Q-sourced and neither is on a summary page.

**The single most decision-relevant fact it missed:** the $10.7B State Aid tax benefit appears as a
red flag ("materially distorts year-over-year earnings comparisons") but the brief never quantifies
what FY2025 EPS looks like without it. It flags the distortion and then declines to remove it —
exactly the arithmetic a human wants done.

**Where it sounds confident and shouldn't:** the moat trajectory is asserted "widening" on the
strength of Services gross margin expanding 70.8% → 76.7%. That is a mix-shift observation, not a
moat observation, and the brief does not distinguish them.

**Structural observation:** the same supply-constraint quote appears **three times** — as risk #3,
red flag #1, and the `risk` reconciliation entry. One fact, three slots, and it reads as three
findings.

### WDC — `0000106040-25-000038+0001628280-26-029054`

**The strongest brief of the three, and the one that best justifies the whole feature.** The red-flag
section is a genuine near-term cash-claims inventory: $1.6B convertibles trigger-classified as
current, $331M mandatory repatriation tax due within twelve months, $498M of unrecognized tax
benefits with $332M expected inside a year, and the Sandisk separation's tax-free status explicitly
not assured. Assembled together, that is a liquidity picture no screener produces and no summary
page carries.

**What I'd do differently:** this is the one brief that would have changed my sizing. The
reconciliation flagged that the screener's `value` score of 0 was computed off a single positive
FCF year ($1,279M) while the 10-Q showed 9-month OCF of $2,540M — i.e. the reverse-DCF's own input
base was stale, so the "~9% implied growth" bar was overstated. **The brief critiqued its own quant
context and was right to.**

**The most decision-relevant miss:** the brief never states the debt maturity schedule beyond the
convertible. For a cyclical at a cycle peak with a transformed balance sheet, "when does the rest
come due" is the question — and §6 below shows that schedule is freely available from XBRL and not
currently fetched.

**Where it sounds confident and shouldn't:** "the ~9% market-implied FCF growth rate appears
conservative relative to actual FCF trajectory" extrapolates a peak-cycle run-rate forward. The
brief elsewhere correctly notes WDC "swung from zero to severe losses in a single year." Those two
statements are in tension and the brief does not resolve them.

**Staleness note:** the primary 10-K was **319 days old** at brief time. The 10-Q carries nearly all
the current information, yet the header credits the year-old accession as the source.

### ASTS — `0001780312-26-000006+0001193125-26-216950`

**Excellent, and the clearest demonstration that the machinery works when there is real signal.** The
red-flag list is dense and specific: prospective going-concern language, the BB7 launch loss with a
quantified $155–160M carrying value, three successive ATM programs raising ~$1.27B, penny warrants
struck at $0.01, ~$96M/yr spectrum payments payable in stock, a ~$100M induced-conversion expense,
and a $1.075B convertible issued after period end. For a pre-revenue name that is the correct
analysis, and the AVOID call follows from it.

**What I'd do differently:** nothing on the qualitative side. This brief is better than what most
retail investors would assemble in an afternoon.

**The most decision-relevant miss:** the brief never states the cash runway as a number. It has the
inputs — $2.8B cash, the burn, the $96M/yr spectrum obligation — and stops short of dividing. Same
pattern as AAPL's tax adjustment: facts assembled, arithmetic declined.

**Where it sounds confident and shouldn't:** the takeaway calls it "a high-conviction binary
technology bet" while the persisted conviction is MEDIUM. The prose and the structured field
disagree, and only the structured field is guarded.

**Honest coda:** ASTS returned **+27.8% vs SPY +2.8%** in the 19 days after this AVOID call. n=1,
one regime, and the brief's reasoning was not thereby wrong — but it belongs here rather than buried
in §7.

### Cross-cutting read

The pattern across all three is consistent: **fact extraction is strong, arithmetic on those facts
is absent, and price is absent entirely.** Every brief assembles the inputs for a calculation a human
wants — normalized EPS, cash runway, refinancing wall — and then hands over the inputs. The
mega-caps are the weakest case because abundant Item-1A boilerplate lets every quota fill; ASTS and
WDC are the strongest because evidence scarcity forces selection.

---

## 3. Confirmed defects

### D1 — The grounding verifier mislabels faithful quotes (finding A: **CONFIRMED**, blast radius **corrected**)

`assess.py:170-171`:
```python
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()
```
Whitespace collapse and lowercasing only; no typographic folding. `_verify_grounding`
(`assess.py:184`, `:195`) marks a finding verified iff `_norm(evidence) in _norm(haystack)`.

**Not a truncation artifact.** `cap_bundle` runs before `assess` (`research/__init__.py:63`), so the
prompt and the haystack are built from the same capped bundle. The mismatch is genuinely
normalization.

**Measured:** 159 / 919 findings unverified = **17.30%**, reproducing the seeded figure exactly.
Per-ticker spread is wide (AAPL 12/26, CVX 12/27, IBM 9/21 — versus NFLX 0/28, INTC 1/30).
Unverified quotes are longer (median 333 vs 274 chars; p90 632 vs 454). Persisted
`unverified_count` / `silent_count` agree with a from-scratch recount on **all 35** files.

**Causal test.** Seven tickers re-fetched live and re-verified under a folding normalizer. Two were
invalidated by filing drift and are excluded rather than quietly counted: GOOG (brief had no 10-Q at
generation; one exists now) and AAPL's four 10-Q-sourced findings (a newer 10-Q superseded the one
used). On the clean population:

| | n |
|---|---|
| Unverified, valid test population | 30 |
| **Recovered by folding** | **22 (73.3%)** |
| Still unverified after folding | 8 (26.7%) |

**Root cause, confirmed by direct inspection:** the dominant character is **U+2019 RIGHT SINGLE
QUOTATION MARK** in the *filing* text (`Company's`, `Firm's`, `JPMorganChase's`) against a pure-ASCII
apostrophe in the model's quote. Verified per-character with `unicodedata` on three recovered items —
the evidence strings contained **zero** non-ASCII characters, so the mismatch is entirely filing-side.

**The residual 8 are the interesting part, and they split into two further categories:**

- **3 are filing-*extraction* artifacts — a third bug class that folding cannot fix.** CRWD's 10-K
  text contains bare page numbers injected mid-sentence (`"...superior access 26 to certain ai
  technologies..."`, `"...cause our 24 current and prospective customers..."`); JPM's contains a
  literal `-` bullet marker mid-clause. AAPL's 10-Q MD&A (fetched with `markdown=True`) injects a
  running page header inline: `"...financial condition.\n\nApple Inc. | Q3 2026 Form 10-Q |
  13\n\nTariffs and Other Measures..."`. A model quoting the *sentence* correctly omits these; the
  verifier then calls it unverified. **This is filer/template-dependent** — JPM, AXON, BMI and CRWD
  showed zero `|` or `**` in their `tenq_mda`.
- **5 are genuine model verbatim-rule violations, and two of them are fabricated composite quotes.**
  AXON's Carbyne red flag opens with a paraphrase absent from the filing spliced to a real
  non-adjacent clause; BMI's moat reconciliation opens with "leading technologies that span the full
  water cycle," which **does not appear in the filing at all** (actual text: "leading technology in
  water meters and radio systems for water utilities"). The other three are quote-style
  substitution (single vs double quotes) and clause reordering.

**Blast radius — the seeded claim is half right and I am correcting it.** The code path is real: at
`assess.py:416`, `_high_corroborated` requires `c.verified and c.verdict == "confirms"`, so a
correct-but-mismatched entry *can* demote HIGH → MEDIUM. **But across all 32 briefs with a call,
`conviction_capped` is `false` — zero conviction changes of any kind, from any guard, so zero
attributable to this bug.** The measured harm is therefore to display and trust, not to decisions.
That does not make it unimportant: with 73% of the marker's firings being false, the marker is
noise, and the two fabricated composite quotes it *correctly* caught are invisible in that noise.
Fixing `_norm` is what makes the signal usable.

**Proposed diff** (safe against `tests/research/test_assess.py` — the ellipsis test uses a *stitched*
quote whose intervening text is absent, so it stays unverified; folding is applied to both sides and
can therefore only convert false negatives):

```python
# Typographic characters SEC filings use and models transcribe as ASCII. Applied to
# BOTH the quote and the haystack, so folding can only recover a true match — it can
# never manufacture one. Does not fix page-number / bullet-marker extraction artifacts
# (see docs/audits/2026-08-04-deep-brief-assessment.md D1).
_FOLD = str.maketrans({
    "’": "'", "‘": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "−": "-",
    " ": " ", " ": " ", " ": " ",
    "­": "",
})
_LIGATURES = (("ﬁ", "fi"), ("ﬂ", "fl"), ("ﬀ", "ff"))


def _norm(s: str) -> str:
    s = s.translate(_FOLD)
    for lig, plain in _LIGATURES:
        s = s.replace(lig, plain)
    return re.sub(r"\s+", " ", s).strip().lower()
```

**Not applied — awaiting your go-ahead.**

### D2 — The brief never sees a price (**new**)

`assess.py:350-355` builds the `Fundamentals:` line from exactly nine scalars: `revenue_cagr`,
`fcf_cagr`, `eps_cagr`, `revenue_growth_persistence`, `gross_margin`, `net_margin`, `roic`,
`debt_to_equity`, `interest_coverage`. **No valuation input of any kind.** Meanwhile
`models.py:19-29` already carries `price`, `market_cap`, `pe_ttm`, `pe_median_5y`, `fcf_yield`,
`peg`, `ebit_ev_yield` — all fetched, all discarded before the prompt.

**Measured consequence:** 1 of 35 briefs mentions any valuation multiple, and that one is
"0x EBITDA" (almost certainly a net-cash artifact). The model infers meaning from the score alone —
WDC's brief reads "Value score = 0 (maximum richness)," which is the model reverse-engineering the
scale rather than reading a number.

**Blast radius:** every `value` reconciliation entry in the corpus, and the price-sensitivity clause
of every screening call. The only valuation anchor the model has today is the reverse-DCF line, which
is a single derived number with a known stale-base failure mode (WDC caught it).

### D3 — The cap functions as a quota wherever source material is abundant (finding B: **CONFIRMED and generalized**)

| section | cap | at cap | distribution |
|---|---|---|---|
| `risks` | 12 | **33/35 (94%)** | {8:1, 11:1, 12:33} |
| `what_would_change_my_mind` | 6 | **34/35 (97%)** | {3:1, 6:34} |
| `reconciliation` | 6 | **25/35 (71%)** | {3:1, 4:3, 5:6, 6:25} |
| `red_flags` | 12 | **0/35** | {2:1, 3:3, 4:3, 5:3, 6:9, 7:8, 8:7, 10:1} |
| `added_risks` | 8 | **0/35** | {0:6, 1:3, 2:4, 3:4, 4:10, 5:4, 6:3, 7:1} |

The split is clean and mechanical: lists whose supply is abundant saturate; lists gated on scarce
evidence (a distress signal must exist; a YoY diff must produce blocks) never do. Note the
anti-padding instruction in `SYSTEM_PROMPT` is scoped to `risks` and `red_flags` only — and
`reconciliation` saturates at 71% *despite* an explicit instruction that "this list is sparse, not
one row per score."

**Important correction to the seeded hypothesis.** I checked whether the tail is boilerplate and **it
largely is not.** Reading ranks 6–12 across five briefs, they are company-specific: CVX's Venezuela
sanctions exposure, JPM's private-credit non-bank spillover, PLTR's multi-class voting structure,
TACT's three-end-market manufacturing forecast risk. A specificity proxy (presence of a number, year,
or mid-sentence proper noun) shows only mild decay — 65% for ranks 1–6 vs 56% for ranks 7–12.

The sharper comparison is across lists: **`red_flags`, which never saturates, is 94% specific;
`risks`, which saturates 94% of the time, is 65%.** The cost of the quota is therefore attention
dilution and false precision — "exactly 12 material risks" every time is not a credible output — not
fabrication. The `reconciliation` saturation is the worse offender because it directly contradicts a
prompt instruction and because reconciliation is the section carrying the most decision value.

### D4 — Conviction carries no information, and the guards are not why (finding D: **CONFIRMED, mechanism refuted**)

| | |
|---|---|
| Stance | HOLD 17, BUY 8, AVOID 6, STRONG_AVOID 1, STRONG_BUY 0 |
| Conviction | **MEDIUM 28, HIGH 2, LOW 2** |
| `conviction_capped: true` | **0 / 32** |
| `stance_clamped: true` | 4 / 32 (ORCL, SNDK → `negative_fcf`; RBKB, TACT → `below_min_mktcap`) |
| `decided_without` non-empty | 5 / 32 (EWSB, JPM, QBTS, RBKB, TACT — all FMP 402/429 on growth/value axes) |

**Every guard in `apply_guards` executed as designed and none of them ever changed a conviction.**
Rule 3 (`decided_without` → cap MEDIUM) fired on 5 briefs and altered none — all were already ≤
MEDIUM. Rule 4 (HIGH corroboration) demoted zero — both HIGH briefs (NVDA, INTC) carry verified
`confirms` entries and fully verified red-flag lists, and pass legitimately. RBKB and TACT triggered
*both* a gate clamp and the `decided_without` cap, and still show `conviction_capped: false` because
they had self-reported LOW.

So the MEDIUM concentration is **the model's own self-selection**, not guard compression. The guards
are currently redundant safety nets, correctly built but never load-bearing on this cohort.

**Caveat, stated because it bounds the claim:** `card.confidence` is not persisted in the brief JSON,
so rule 2 (the confidence-threshold cap) cannot be reconstructed from artifacts. What *is*
determinable is the outcome — `conviction_capped` is the recorded fact that no rule changed the
value. A code-grounded inference: since rule 2 runs before rule 4 and would have intercepted a
low-confidence HIGH, `confidence ≥ 0.70` for NVDA and INTC at generation time.

**Recommendation: persist `confidence` in the brief JSON.** It is one field and it is the only reason
this analysis had to reason indirectly.

### D5 — 8-K item codes are fetched and thrown away (**new**)

`data/sources/edgar.py:302-306` normalizes each filing to `{form, filed, accession, url}` and
discards everything else. I verified live that `edgartools` already returns the item codes:

```
CMCSA latest 8-K: form=8-K  filed=2026-07-23  accession=0001628280-26-049274
  .items -> '2.02,9.01'
index columns: [... 'form', 'fileNumber', 'items', 'size', ...]
```

So `assess.py:257` renders "8-K filed 2026-07-23" when it could render "8-K (item 2.02 — results of
operations) filed 2026-07-23", at **zero additional network cost**. Your Phase-1 table listed this as
"veto sweep knows, brief doesn't"; in fact the brief's *own fetch* has it in hand.

This also matters because the repo already treats items {1.03, 2.04, 2.05, 2.06, 3.01, 4.02, 5.01} as
reliably negative in the veto sweep. A held name filing an Item 4.02 (non-reliance restatement) is
the single most decision-relevant 8-K event there is, and today the brief shows it as an undated
form label.

### D6 — Evidence is reused across sections (**new**)

**31 of 35 briefs** reuse at least one ≥40-char evidence quote across sections; 62 instances total.
The dominant pattern is `reconciliation` + `red_flags`. AAPL states one supply-constraint quote three
times (risk, red flag, reconciliation) with near-identical claims. Nothing in `SYSTEM_PROMPT` forbids
it, and the rendered brief gives no indication that three bullets are one fact.

### D7 — `red_flags` has drifted off its own definition (**new**)

`SYSTEM_PROMPT` defines red flags as "signals of elevated concern — going-concern doubt, material
weakness in internal controls, restatements, covenant or liquidity stress, auditor changes, material
litigation, or heavy dilution." That reads as a definition followed by exemplars, and the model
treats the list as exemplary: **only 24% of the 214 red flags across the corpus match any enumerated
category** (punctuation-insensitive keyword match on claim *or* evidence).

This is a prompt-ambiguity defect, not a model failure — and the machinery works where it matters:
ASTS and EWSB both surface going-concern language correctly. On mega-caps, though, `red_flags`
degenerates into a second bear case, which is why it overlaps `risks` (D6) and why genuine distress
markers are diluted when they do appear.

### D8 — Macro never reaches the brief (finding C: **CONFIRMED**)

`grep -rn "macro" src/shortlist/research/` returns nothing. `bot.py:340` fetches `MacroContext` and
threads it into `_screen_fn` (`:341`) and `_report_fn` (`:350`) — but `_research_fn` at `:342` is
called without it. The brief reasons about a reverse-DCF discount rate, leverage, and cyclicality
with no rate or credit context.

### D9 — The model is a generation behind (**new**)

`config.yaml: research.model: claude-sonnet-4-6`. Current models are `claude-sonnet-5` and
`claude-opus-5`; the `claude` CLI's `--model` accepts both full IDs and aliases, so this is a
one-line change. Measured cost today: median **$0.626**/brief, p90 $0.96, max $1.65 (AXON), **$22.76
total across all 35 briefs.**

**One thing I could not measure and will not assert:** Sonnet 5 ships a new tokenizer that produces
materially more tokens for the same input text than Sonnet 4.6. On a 178K-char prompt that shifts the
cost calculus enough that Sonnet 5 is *not* obviously cheaper than Opus 5 for this workload. There is
no `ANTHROPIC_API_KEY` and no `ant` CLI on this box, so `count_tokens` could not be run against both
tokenizers. Treat the relative cost as unmeasured. Note also that briefs are billed through CLI auth,
so the persisted `cost_usd` is the CLI envelope's figure, not necessarily an API-rate invoice.

### D10 — No `--fallback-model` on a path that retries three times (**minor, new**)

`claude_cli.py:48-55` builds argv without `--fallback-model`, while `assess()` retries up to three
times on transient failures. If the primary model is overloaded, all three attempts fail the same way
and the brief is dropped ("research unavailable"). The CLI supports `--fallback-model` natively.

---

## 4. Input gaps, ranked by decision impact

Your checklist was accurate. Corrections: "Recent SEC filing list (form + date only)" should be
**"form + date, with item codes discarded"** (D5), and the ✅ on the quant context needs the
qualifier that it contains **no valuation data** (D2).

Ranked by decision impact for pre-purchase triage, not by ease:

| # | Gap | Endpoint / auth | Rate limit & ToS | Fields & latency | Attaches at | Visible change | Cost |
|---|---|---|---|---|---|---|---|
| 1 | **Valuation inputs** (D2) | none — already on `StockMetrics` | n/a | `pe_ttm`, `pe_median_5y`, `fcf_yield`, `peg`, `market_cap`, `price`; per screen | `assess.py:_quant_context` scalars list | brief can state and reconcile the multiple | $0 |
| 2 | **8-K item semantics** (D5) | already fetched — `edgartools` `.items` | none extra | item codes per filing; per screen | `edgar.py:_fetch_filings_index` → `FilingEvent` → `assess.py:257` | "8-K (item 4.02 — non-reliance)" not "8-K filed" | $0 |
| 3 | **Debt maturity ladder / runway** | `data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/LongTermDebtMaturitiesRepaymentsOfPrincipalInYear{One..Five}` — keyless, `SEC_IDENTITY` | SEC fair-access ~10 req/s; official filing data, no ToS risk | $ due yr 1–5; refreshed per 10-K | `providers/_edgar_facts.py` (shared leaf) → new `Statements` fields | "$8.4B due within 12mo vs $X cash + FCF" | free; 5 extra companyconcept calls/ticker |
| 4 | **Guidance issued vs delivered** | 8-K Item 2.02 → `index.json` → `EX-99.1` exhibit; keyless | SEC fair-access; official filing | guidance tables when the filer publishes them; same-day | research-layer, per deep-dive | "raised FY27 revenue guidance ~2% on 2026-07-30" | free; real code cost in exhibit-filename heuristics |
| 5 | **Peer set / relative multiples** | `data.sec.gov/api/xbrl/frames/{taxonomy}/{concept}/{unit}/{period}.json`; keyless | SEC fair-access; one bulk pull per concept+period, cacheable forever (period immutable) | one concept across all filers; ~833KB / 6,251 companies per call | new `research/peers.py`, filtered by existing `m.sic` | "14× vs same-SIC peer median 21×" | free; frames rows carry no SIC, so peer SICs need caching |
| 6 | **Macro / rates / credit** (D8) | `api.stlouisfed.org` via existing `data/macro.py:fetch_macro` + `FRED_API_KEY` | already in use, day-cached | `BAMLH0A0HYM2` (HY OAS), `DGS10`, `T10Y2Y`, `NFCI`, `DFF`; daily/weekly | thread `macro` into `_research_fn`, render one prompt line | reverse-DCF and leverage read against the rate regime | free |
| 7 | **Segment revenue / margin** | `Filing.xbrl().query().by_concept(...).with_dimensions()` (edgartools, already a dep) | SEC fair-access; several sub-fetches per filing | segment revenue, op income, capex, D&A; per 10-K/10-Q | research-layer, per deep-dive (the `proxy.py` pattern) | "Commercial Engines carries 87% of op income on 55% of revenue" | free; **225MB peak RSS measured** — real on a 1.9GB box |
| 8 | **Analyst consensus direction** | `finnhub.io/api/v1/stock/recommendation` — existing free key | within existing 60/min | buy/hold/sell counts by month; ~monthly | extend `sources/finnhub.py` → research context line | "consensus moved 8 buy/2 hold → 5 buy/5 hold" | free |

**Verified dead ends — do not build:**

- **Earnings-call transcripts.** No free, ToS-clean, small-cap-covering source exists as of 2026-08.
  FMP gates them to **Ultimate at $149/mo** (7× your stated ceiling); Finnhub's `/stock/transcripts`
  returns `"premium":"Premium required."` in their own Swagger; Motley Fool's ToS explicitly
  prohibits scraping and abstracting; Seeking Alpha's is not permissive of automated harvesting;
  Alpha Vantage's free tier is 25 requests/**day** across all endpoints. roic.ai and
  discountingcashflows.com have genuine free tiers but neither discloses where the transcript text is
  licensed from — transcripts are not an SEC artifact, so any API serving them is relaying someone
  else's transcription. **`ASSESSMENT_GAPS.md` §3.1 calls transcripts "the single biggest win"; that
  is still true as analysis and now false as a plan.** That section should be updated to record the
  feed as unobtainable under these constraints rather than deferred.
- **Ticker-side 13F ownership.** All Finnhub ownership endpoints are premium. The free path requires
  parsing the full quarterly 13F universe — exactly the scale problem the existing curated-7-fund
  `EdgarThirteenFSignal` design deliberately avoids. Inverting it to "who holds ticker X" needs the
  full-universe parse. Drop it.
- **Sell-side numeric estimates / price targets.** `/stock/price-target`, `/stock/revenue-estimate`,
  `/stock/eps-estimate`, `/stock/upgrade-downgrade` are all premium on Finnhub. Only the
  recommendation-trend counts (#8 above) clear the free bar.

**Unvetted breadcrumbs** (could not fill all six fields — file, don't build):
FMP `analyst-estimates` tier requirement (JS-hydrated page; unconfirmed whether Starter-reachable);
WhaleWisdom / Fintel / Dataroma ticker-side 13F (websites, no documented free API or ToS position);
`DebtInstrumentMaturityDate` and covenant text (dimensional/textual — same flattening problem as
segments, would need the raw-XBRL path proven for #7).

---

## 5. Prompt and guard critique

### `SYSTEM_PROMPT`

**Doing no work / actively harmful:**

1. **"never pad to the maximum or invent items"** is scoped to `risks` and `red_flags` and sits in the
   system prompt, while `"Return at most 12 risks, ... most material first"` sits in the user prompt.
   The two fight, and the cap wins: 33/35, 34/35, 25/35 (D3). "At most N, most material first" reads
   as a target. **Replace with a materiality bar:**
   > `Return only risks that would change a buy/sell decision — a reader who already knows the
   > industry should learn something from each one. Fewer, sharper items are better than a full
   > list; there is no target count. Hard ceiling: 12.`
2. **The `red_flags` definition is ambiguous** (D7). Make the enumeration closed, or rename the field
   to what it has become. Closed version:
   > `'red_flags' are limited to: going-concern doubt, material weakness in internal controls,
   > restatement or non-reliance, covenant breach or liquidity stress, auditor change, material
   > litigation, or heavy dilution. If the filing discloses none of these, return an empty array —
   > general bearish considerations belong in 'risks' or the bear case, not here.`
3. **Nothing forbids reusing a quote across sections** (D6). One clause fixes it:
   > `Each evidence quote may support only one item across risks, red_flags, added_risks, and
   > reconciliation. If one passage supports several, place it where it matters most.`
4. **The verbatim rule is stricter than the verifier can enforce.** The prompt forbids ellipses,
   bracketed edits, and stitched non-adjacent sentences; `_norm` can only do substring matching. So a
   Unicode mismatch, an extraction artifact, and a genuine fabricated composite all render
   identically as `_(unverified)_`. After the D1 fix the marker becomes meaningful; consider also
   splitting the rendered label so a reader can tell "we could not match this" from "this violates
   the quote rule" — the verifier cannot currently distinguish them, so v1 should just say the
   former honestly.
5. **"Respond with ONLY a JSON object"** (finding E) is contradicted by `_salvage_json`, which
   already tolerates a preamble. Harmless today; see §7 for whether to relax it.

**Missing:** valuation inputs (D2), macro (D8), and an instruction to *do the arithmetic* the briefs
consistently decline (normalized EPS ex-one-offs, cash runway, refinancing coverage) — the single
most consistent qualitative gap in the three-brief read.

### Guards (`apply_guards`)

**Well-designed, correctly implemented, and currently inert.** The gate clamp is monotone-bearish
(it can only move a stance toward AVOID), the conviction cap is conservative, and the HIGH
corroboration requirement is exactly right in shape. None of them fired to any effect on 32 briefs
(D4). They do not mislead — but they also do not currently earn their complexity, and the reason is
that the model self-selects MEDIUM. **The fix is not more guards; it is to stop treating conviction
as a signal** until there is evidence it varies for a reason. Persist `confidence` so this is
measurable next time.

One real asymmetry worth noting: `_high_corroborated` treats `HOLD` as satisfied by "any non-silent
corroboration" (`assess.py:424`) while bullish requires a verified `confirms` and bearish a verified
`contradicts` or red flag. That is a defensible design, but combined with HOLD being 17/32 it means
the most common stance faces the weakest corroboration bar.

### Model choice

`claude-sonnet-4-6` is a generation behind (D9). For a real-money decision at ~$0.63/brief and a
handful of briefs a month — **$22.76 total to date across the entire corpus** — the cost ceiling is
not the binding constraint; latency is (900s timeout, 3 attempts, WDC measured ~490s). A stronger
model would most plausibly help exactly where the briefs are weakest: doing the arithmetic instead of
assembling the inputs. Recommendation is in §7, gated on the unmeasured tokenizer question.

### One-shot vs two-pass

Given that ~73% of "tainted" findings are a normalization bug rather than model error, **the two-pass
argument is much weaker than it looked.** Fix `_norm` first; the honest residual is ~5% of findings
(genuine violations) plus ~3% extraction artifacts. A two-pass redesign to address 5% is not
justified yet. Re-evaluate after D1 lands and the residual is measured cleanly.

---

## 6. Retrospective statistics

**These are descriptive. A return-based verdict is not available at this n and none is offered here.**
35 observations over ≤2 months, heavily overlapping market exposure, one regime, and survivorship in
which names were chosen for `/deep` in the first place. This repo has a documented history of
retracted claims from exactly this mistake (`docs/audits/2026-08-03-evaluator-rederivation.md`).
**Do not quote a hit rate from this section; there isn't one.**

**Corpus hygiene.** `WDC/0000106040-25-000038+0001628280-26-029054.json` is byte-identical
(SHA-256-confirmed) in both `research/` trees — one file copied, not two runs. NVDA (3) and IBM (2)
are genuinely distinct briefs. Headline percentages use the raw 35; deduplicated, D1's headline is
155/891 = 17.40%.

**Cost.** min $0.301 · p25 $0.450 · **median $0.626** · p75 $0.695 · p90 $0.960 · max $1.650 (AXON).
Total **$22.76**. `stop_reason` is `end_turn` on all 35 (truncations are dropped before persisting,
as designed). `notes` is empty on all 35.

**Coverage.** `decided_without` non-empty on only 5/32, and every instance is an FMP 402/429 on the
`growth`/`value` axes. Whether the multi-year `financial_series` actually rendered **cannot be
determined from the artifacts** — `decided_without` is driven by sub-score gaps in
`coverage_caveat.py`, a different code path from `_render_series`. A brief can have empty
`decided_without` and an empty series. Answering your Phase-1 question properly requires either
persisting the prompt or logging series length; I flag it as unmeasured rather than infer.

**`as_of_price` semantics — checked, not assumed.** Traced `assess.py:436` → `bridge.py:123` →
`yahoo_prices.py:_normalize_yahoo`, where `price = closes[-1]` and `closes` comes from
`result["indicators"]["adjclose"]`. **It is the split- and dividend-adjusted close** — the same series
`pick_performance` uses, so the `CLAUDE.md` warning about the picks ledger applies here identically.
Forward returns were therefore computed with `scout/picks.py:pick_performance` unmodified rather than
against the stored scalar.

**Forward returns by stance** (as of 2026-08-04; 3 same-day briefs — AXON, BMI, CRWD — excluded from
means as `days_held=0`; WDC counted once):

| Stance | n usable | mean excess vs SPY | range |
|---|---|---|---|
| BUY | 8 | +3.19% | −17.59% (ISRG) → +20.82% (MSFT) |
| HOLD | 15 | +2.98% | −19.94% (WDC) → +35.47% (IT) |
| AVOID | 5 | +0.50% | −20.82% (SNDK) → +25.07% (ASTS) |
| STRONG_AVOID | 1 | +6.96% | EWSB only |

**What this does and does not show.** The ordering (BUY > HOLD > AVOID) is in the intuitive direction
and is *not evidence of anything* — dispersion inside every bucket dwarfs the gap between buckets,
holding periods run 2–53 days, SPY rose 2–6% over most windows so nearly every stance shows a
positive raw return from beta alone, and n per bucket is 1–15. The two most striking individual rows
point opposite ways (ASTS AVOID +25.07%, SNDK AVOID −20.82%), which is what noise looks like.

**Is HOLD a dumping ground?** Structurally, somewhat: 17/32 stances and 15 of the usable return rows,
with the widest range of any bucket (−19.94% to +35.47%). Combined with STRONG_BUY never being used
at all and conviction being constant, the effective output alphabet is narrower than the five-point
scale implies.

**Does conviction track anything?** It cannot — 28/32 are the same value (D4). At this concentration
the field carries no information regardless of what returns did.

**How often did guards fire?** Gate clamp 4/32; `decided_without` cap 5/32; HIGH-corroboration
demotion 0/32; **net conviction changes 0/32.**

---

## 7. Ranked recommendations

### `cheap-and-certain`

1. **Add valuation to `_quant_context`** (D2). Append `pe_ttm`, `pe_median_5y`, `fcf_yield`, `peg`,
   `market_cap`, `price` to the `scalars` list in `assess.py:350`. No new feed, no new fetch, no
   latency. Largest decision-impact-per-line change in this report.
2. **Fold typographic punctuation in `_norm`** (D1). Diff in §3, tests checked. Recovers ~73% of false
   unverified marks and makes the residual — including two fabricated composite quotes — legible.
3. **Carry 8-K item codes into `filing_events`** (D5). Add `items` to `FilingEvent`, read it in
   `_fetch_filings_index`, render it at `assess.py:257`. Zero additional requests.
4. **Persist `confidence` on the brief JSON** (D4). One field; the only reason §6 had to reason
   indirectly about the guards.
5. **Add `--fallback-model` to the CLI invocation** (D10).
6. **Thread `macro` into `_research_fn`** (D8) and render one line — rate regime, HY OAS, curve. Keep
   it to facts that change how *this company's* numbers read (financing cost, discount rate,
   cyclicality); do not let it invite market-timing prose. Guard by making the line a fixed template
   rather than free text.

### `worth-building`

7. **Materiality bar instead of a count cap** (D3) — prompt edit in §5, plus the same treatment for
   `reconciliation`, which saturates *against an explicit instruction*.
8. **Close the `red_flags` enumeration** (D7) and **forbid cross-section quote reuse** (D6). Both are
   prompt-only.
9. **Debt maturity ladder** (§4 #3) — reuses the proven `companyconcept` pattern in
   `_edgar_facts.py`; 1,636 filers tag the 1-year concept in a single quarter; verified live on
   Boeing ($8,351M due within 12mo). Turns an abstract leverage gate into a dated obligation.
10. **Ask the model to do the arithmetic.** The most consistent qualitative gap: normalized earnings
    ex-one-offs, cash runway, refinancing coverage. All three briefs assembled the inputs and stopped.
    Prompt-only.
11. **Guidance issued vs delivered** (§4 #4) — unlocked by #3 above, since Item 2.02 is the marker.
12. **Peer multiples via `frames`** (§4 #5) — the brief's only current valuation lens is a company's
    own history; this adds the cross-section.

### `needs-measurement`

13. **Upgrade the model** (D9). `claude-opus-5` is the capability answer; `claude-sonnet-5` is the
    nominal cost answer but its new tokenizer makes that non-obvious on a 178K-char prompt. **Measure
    before choosing:** run `count_tokens` on one real prompt against `claude-sonnet-4-6`,
    `claude-sonnet-5`, and `claude-opus-5`. Then re-run 3–5 cached tickers with `--refresh` and diff
    the briefs. Note this needs an API key or `ant`, neither of which is on this box.
14. **Segment revenue/margin via raw XBRL** (§4 #7). Real and retrievable — proven live on GE
    (Commercial Engines $33,252M revenue / $8,861M op income). But 225MB peak RSS on a 1.9GB box, and
    segment concepts are not standardized across filers (GE mixes `ge:` extensions with `us-gaap` +
    axis), so extraction must search by dimension-member presence, not a concept whitelist. Gate it
    the way `proxy.py` is gated: per-deep-dive only, never on the harness path, failure-isolated.
15. **Two-pass generation.** Deferred, not rejected — re-evaluate once D1 lands and the true residual
    error rate is known. The current case for it rests on a 17% figure that is ~73% artifact.

### `rejected-and-why`

16. **Earnings-call transcripts.** No free, ToS-clean, small-cap-covering source in 2026. Cheapest
    legitimate path is FMP Ultimate at $149/mo — 7× your stated ceiling for one input. Update
    `ASSESSMENT_GAPS.md` §3.1 to record this rather than leaving it as "deferred."
17. **Ticker-side 13F ownership.** All free APIs gate it; the DIY path needs the full-universe
    quarterly parse the existing curated-fund design exists to avoid.
18. **Numeric sell-side estimates / price targets.** Premium on Finnhub; not confirmed reachable on
    FMP Starter. Only free recommendation-trend counts survive, and those are ranked 8th for a
    reason.
19. **Relaxing "ONLY a JSON object" to allow a reasoning preamble** (finding E). Free to do, but the
    justification was the 17% tainted-findings figure, which is mostly artifact. Leave the instruction
    as-is; `_salvage_json` already provides the safety margin. Revisit with #15.
20. **Raising the context caps.** The 178K-char prompt is already near the useful limit and the
    briefs' weakness is not missing text — it is missing *numbers* (D2) and missing arithmetic. Every
    recommendation above adds tens of characters, not tens of thousands. Do not spend budget here.

---

## 8. What I checked and found genuinely fine

- **The reconciliation mechanism is the best part of the brief and is not ritual.** WDC's caught a
  real screener artifact (net margin inflated by non-cash Sandisk mark-to-market) and a real
  reverse-DCF staleness bug (base FCF from one year vs the 10-Q run-rate). AAPL's value entry is a
  clean, correct statement of the central tension. This is the feature earning its keep.
- **Quote grounding as a design is right.** The prompt/verifier contract is explicit, the haystack
  correctly excludes the prior-year 10-K and every computed context line, and the prompt-only
  discipline for interpretive values (reverse-DCF, proxy, insider, gov contracts, lobbying) is
  consistently applied. The bug in D1 is in one function, not in the architecture.
- **`cap_bundle` genuinely keeps prompt and haystack identical** (`__init__.py:63`), which eliminated
  truncation as an explanation for D1 and let the causal test be clean.
- **Persisted counts are trustworthy.** `unverified_count` and `silent_count` match a from-scratch
  recount on all 35 files — zero drift.
- **Failure isolation works as documented.** 10-Q and risk-diff degrade to `""` independently;
  `_enrich_card` never lets one ticker abort a batch; `no_10k_reason` correctly distinguishes a 20-F
  foreign issuer. `stop_reason` is `end_turn` on all 35 — no truncated brief ever reached disk.
- **The guards are correctly built even though they never fired.** Monotone-bearish gate clamp,
  Python-owned coverage caveats (never the LLM), `_screening_call` parsing leniently so a malformed
  capstone never sinks a brief. The problem in D4 is that conviction has no variance to guard, not
  that the guards are wrong.
- **The commit-order discipline in `report.write`** (JSON first, `.md` last as the atomic cache
  marker, PID-unique temp + `os.replace`) is careful and correct.
- **`as_of_price` semantics match `pick_performance`'s expectations** — I checked rather than assumed,
  and the `CLAUDE.md` split-adjustment warning transfers cleanly.
- **The `added_risks` YoY diff behaves exactly as designed** — never saturates, 6 briefs correctly
  return zero, and WDC's five entries are all tariff-related because WDC genuinely added a tariff
  block that year.

---

## 9. Deferred — scorer surface

Recorded and *not* pursued, per scope:

- `pay_for_performance_alignment` as a backtest axis (the Item 402(v) PvP table is structured XBRL, so
  it is a legitimate future candidate — already noted in `ASSESSMENT_GAPS.md` §3.1).
- Segment-level margin dispersion as a quality input. Only becomes measurable if §4 #7 ships and
  accumulates; USAspending-style "not in companyfacts" problem does not apply here, so a snapshot-replay
  path would eventually work. Not proposed now.
- Debt-maturity-derived refinancing pressure as a gate refinement. The existing `over_leveraged` gate
  is a point-in-time ratio; a dated wall is a different shape. Measure-first applies — this is a note,
  not a proposal.

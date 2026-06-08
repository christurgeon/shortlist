---
name: run
description: >
  End-to-end shortlist screener skill. Invoked when the user asks to screen,
  analyze, or rank tickers — or types /run. Gathers tickers, checks the
  environment, runs `uv run shortlist --json`, then interprets the results:
  ranking, opportunity axis, gates in plain English, null sub-score warnings,
  and coverage gaps.
---

# run

## When to invoke

Trigger phrases: "run the screener", "analyze [tickers]", "screen X,Y,Z", `/run`.

---

## Step 1 — Gather tickers

- If tickers appear in the user's message, use them (comma-separated symbols, e.g. `AAPL,LMT,GEV`).
- If the user says "demo", jump to **Demo mode** in Step 3.
- If no tickers were given, ask before proceeding.

---

## Step 2 — Environment check

Check which API keys are set:

```bash
echo "FMP=$FMP_API_KEY" && echo "FINNHUB=$FINNHUB_API_KEY" && echo "EDGAR=$SEC_IDENTITY"
```

| Variable | Provider | Impact if missing |
|---|---|---|
| `FMP_API_KEY` | fmp | Primary fundamentals lost; screener may return thin or empty results |
| `FINNHUB_API_KEY` | finnhub | Insider sentiment (MSPR) + analyst revisions unavailable |
| `SEC_IDENTITY` | edgar | EDGAR provider skipped at runtime; insider sub-score goes `null` silently |

If **all keys are absent**, offer demo mode rather than running live.  
Providers with missing keys are skipped gracefully — no crash — but their sub-scores will be `null`.

---

## Step 3 — Build and run the command

**Live run (default — harness engine):**
```bash
uv run shortlist --tickers <TICKERS> --json
```
The default engine is now the **harness** (sources `yahoo, fmp, finnhub, edgar, finra`
from `config.yaml: harness_sources`). It recovers `value`, `growth`, and the `risk`
axis from free EDGAR + Yahoo data when FMP gates a symbol. **Do not pass `--provider`
on this path** — it overrides `harness_sources` and would drop the keyless `yahoo`
(momentum/risk) and `finra` (short interest) sources.

**Lean screener engine** (synchronous, FMP-centric; no free-source fallback when FMP gates):
```bash
uv run shortlist --tickers <TICKERS> --engine screener --provider fmp,finnhub,edgar --json
```

**Demo mode** (no keys needed — hardcoded basket `GEV,LMT,SCHW,TMO,GOOGL`, mock data):
```bash
uv run shortlist --demo --json
```
⚠ `--demo` ignores any `--tickers` arg and uses the mock provider. Research briefs are not available in demo mode (no real filings).

**Optional flags:**
- `--csv <path>` — write ranked results to a CSV (`rank,ticker,composite,quality,moat,growth,momentum,value,opportunity,insider,risk,upside_to_target,gates,scored,confidence,sic_bucket,piotroski_f,share_count_cagr,net_debt_to_ebitda`; gates are pipe-joined, `piotroski_f` is `won/legs`, `net_debt_to_ebitda` is floored at 0 for net-cash names)
- `--research N` — generate Claude-written 10-K briefs for the top-N non-gated names; requires `claude` CLI on PATH and `SEC_IDENTITY` set
- `--refresh` — force regeneration of cached research briefs (cached by filing accession, not date)
- Omit `--provider` to use the defaults from `config.yaml`

Run from the **repo root** so `.env` is found.

**stdout** = JSON array; **stderr** = provider warnings + research summaries. Capture both.

**Empty-output fallback:** If the JSON result is `[]`, every provider was skipped. Tell the user and offer demo mode.

---

## Step 4 — Interpret the output

The JSON array contains one object per ticker:

```
ticker, composite, quality, moat, growth, momentum, value, opportunity,
insider, risk, upside_to_target, gates[], flags{}, sic_bucket, confidence,
scored, thin, piotroski_f, piotroski_f_legs, share_count_cagr,
ebitda, net_debt_to_ebitda (floored to 0 for net-cash names in output),
abstentions[] (when any), events{} (when filing events present),
coverage{} (when a provider had trouble), research_path (if --research used)
```

`risk` is the 7th sub-score (realized volatility + max drawdown, inverted so
safer scores higher). It feeds the composite as a tilt but is deliberately
**excluded from `confidence`/`scored`**. `scored` (above the validity floor),
`confidence` (present-applicable weight ÷ applicable weight), and `sic_bucket`
report sector-aware abstention — `passed` is `not gates and scored`, so a
not-scored name can't rank to the top or be selected for research.

Read the current weights and gate thresholds from `config.yaml` before narrating — do not hardcode values.

**Narrate the following:**

### Ranking
State the top ticker(s) by composite score and identify the leading sub-score(s).

### Opportunity axis
`value` and `momentum` are weighted independently in the composite (value-tilted,
~3:1); `opportunity = max(momentum, value)` is reported but is **display-only**.  
Example: "GOOGL ranks on value (65) more than momentum (78); the composite weights value above momentum."

### Growth
`growth` is a separate sub-score (revenue/FCF/EPS CAGR + revenue-growth persistence) — it measures fundamental compounding, distinct from price momentum and from PEG. Mention it when it materially helps or drags the composite.

### Null sub-scores
If a sub-score field is `null`, it had no data inputs and its weight was redistributed to the remaining components. Call this out explicitly.  
Example: "Insider score was unavailable for SCHW — no EDGAR data; its weight (13.5% by default) was redistributed."

### Gates
Translate each triggered gate to plain English, referencing actual thresholds from `config.yaml`:

| Gate key | Plain-English meaning |
|---|---|
| `negative_fcf` | Negative free cash flow |
| `below_min_mktcap` | Market cap below the configured minimum (`gates.min_market_cap` in config.yaml) |
| `over_leveraged` | Debt/equity above the configured maximum (`gates.max_debt_to_equity`) |
| `heavy_insider_selling` | Insider sentiment (MSPR) below the configured floor (`gates.min_insider_sentiment`) |

Gates flag a ticker for scrutiny — they don't exclude it from ranking.

### Standouts
1–2 sentences on the top name with specific score reasoning.

### Coverage gaps
If a ticker had `402` responses from FMP, note it — that symbol is gated on the free plan and data will be thin.

**FMP `402`s = symbol gating, not a bug.** When you see `402` warnings on stderr for a symbol (and `coverage.providers.fmp == "gated_402"`), don't hunt for a code fault. Confirm it's per-symbol gating (the symbol 402s while AAPL/MSFT/LMT return data on the same key) rather than an exhausted quota. What stays `null` depends on the engine:

- **On the default harness engine:** `fcf_yield` and `pe_vs_history` are rebuilt from free EDGAR 10-K financials + Yahoo closes, and `growth`/`risk` come from EDGAR/Yahoo too — so a gated symbol usually still scores `value`, `growth`, and `risk`. Only `upside_to_target` and PEG stay `null` (both FMP-only). This is the normal, expected output for a gated small/mid-cap — narrate it as recovered coverage, not a gap. (If `growth` is also `null`, the symbol likely has no XBRL 10-K financials — e.g. a recent IPO or Form 20-F filer — so that leg abstained and its weight redistributed; `confidence` drops below 1.0.)
- **On the lean `--engine screener` path:** all four value legs live on FMP, so a gated symbol's `value` (and `upside_to_target`) come back `null` and the (display-only) `opportunity` column then equals `momentum`. Two fixes: (a) just use the default harness engine; (b) FMP's paid Starter tier (~$14–20/mo) lifts the gating entirely.

Either way, `market_cap` and the `insider` sub-score still populate because Finnhub backfills the market cap — so a non-null `insider` alongside `fmp: gated_402` is the signature of FMP gating, not missing data.

The screener now emits this machine-readably: each affected card carries a `coverage` block (`providers` map with per-provider status — `ok`/`gated_402`/`empty`/`error` —, the `unavailable` output fields, and an interpretive `note`), and a `Coverage notes` summary prints to stderr. Read `coverage` directly instead of inferring the cause from a null `value`; a `gated_402`/`empty` status on `fmp` with a non-null `insider` is the FMP-gating signature.

**`429 Too Many Requests` is NOT gating — it's a quota/rate limit, diagnosed and fixed differently.** A `429` collapses to coverage status `"error"` (not `gated_402`), with the generic note "fmp: supplied no usable data for this symbol" — so you can only distinguish it from a true fault by **reading the stderr warning**, which names the HTTP code (`429 Client Error: Too Many Requests`). Detection: several symbols in one run come back with `fmp: "error"` + null `value`/`growth` while stderr shows `429`s. There are two flavors, and they have different fixes — tell them apart by **re-running just the affected tickers as a tiny (1–2 symbol) batch**:

- **Per-minute burst throttle** (free tier is ~5 calls/min; the screener spends a few calls per ticker, so a 5-ticker basket can trip it even though earlier symbols in the same run returned `200`). The small re-run *succeeds*. Fix: just space the work out — re-run in smaller batches or wait ~60s.
- **Daily quota exhaustion** (free tier is 250 calls/day). The small re-run **still `429`s immediately on the first, cheapest `/stable/quote` call**. A short wait does nothing — the allowance is spent until the daily reset. Fix: wait for the next-day reset, or FMP's paid Starter tier (~$14–20/mo) which raises the ceiling.

Either way the data is missing because of throttling, **not** per-symbol gating, so the `402` "needs Starter to ever work" framing does not apply — the symbol itself is fine. **Telltale contrast in one run:** AXON `gated_402` (structural — that symbol needs Starter) vs. KO/NVDA `error`+`429` (quota — every symbol comes back once quota resets). If a same-minute tiny re-run still `429`s on the bare quote endpoint, say plainly the daily FMP quota is exhausted, not that there's a bug.

---

## Score reference

| Sub-score | Default weight | Driven by |
|---|---|---|
| Quality | 18% | ROE, net margin, interest coverage, leverage (inverted) |
| Moat | 18% | Gross margin level + 5-year stability + persistent ROIC |
| Growth | 13.5% | Revenue / FCF / EPS CAGR + revenue-growth persistence |
| Value | 22% | upside to analyst target + FCF yield + P/E vs own 5y median + PEG |
| Momentum | 8% | price vs 200DMA + 6m relative strength + EPS revision |
| Insider | 13.5% | Net Form-4 flow (6m) + MSPR sentiment (−1..1) |
| Risk | 10% | Realized volatility + max drawdown (both inverted — safer scores higher) |

`value` and `momentum` are weighted **independently** (value-tilt: ~3:1); the `opportunity` column is `max(momentum, value)`, retained for display only and **not** fed into the composite. `value` = upside to analyst target + FCF yield + P/E vs own 5y median + PEG. The **`risk`** axis is a composite-only tilt — it feeds the weighted blend but is excluded from `confidence`/`scored`, and its weight is an unfitted prior (trailing vol/drawdown can be anti-predictive at turning points). All scores are 0–100. **These are the defaults — always read the actual weights and gate thresholds from `config.yaml` before narrating; do not hardcode them.**

---

## Edge cases

- `SEC_IDENTITY` unset → `edgar` skipped; insider sub-score is `null`. Mention this.
- `--research` requested but `claude` CLI not on PATH → screener warns on stderr and continues without briefs.
- `--research` skips gated tickers regardless of their rank.
- Banks/insurers (e.g. SCHW) will have blank moat/quality proxies by design — note it.
- Margins and returns are stored as fractions (0.42 = 42%) — don't re-convert when narrating.

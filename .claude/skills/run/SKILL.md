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

**Live run (default):**
```bash
uv run shortlist --tickers <TICKERS> --provider fmp,finnhub,edgar --json
```

**Demo mode** (no keys needed — hardcoded basket `GEV,LMT,SCHW,TMO,GOOGL`, mock data):
```bash
uv run shortlist --demo --json
```
⚠ `--demo` ignores any `--tickers` arg and uses the mock provider. Research briefs are not available in demo mode (no real filings).

**Optional flags:**
- `--csv <path>` — write ranked results to a CSV (`rank,ticker,composite,quality,moat,momentum,value,opportunity,insider,upside_to_target,gates`; gates are pipe-joined)
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
ticker, composite, quality, moat, momentum, value, opportunity,
insider, upside_to_target, gates[], research_path (if --research used)
```

Read the current weights and gate thresholds from `config.yaml` before narrating — do not hardcode values.

**Narrate the following:**

### Ranking
State the top ticker(s) by composite score and identify the leading sub-score(s).

### Opportunity axis
`opportunity = max(momentum, value)` — always state which one won.  
Example: "GOOGL's opportunity score of 78 was driven by momentum (78) rather than value (65)."

### Null sub-scores
If a sub-score field is `null`, it had no data inputs and its weight was redistributed to the remaining components. Call this out explicitly.  
Example: "Insider score was unavailable for SCHW — no EDGAR data; its 20% weight was redistributed."

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

**Null `value` + FMP `402`s = symbol gating, not a bug.** When `value` (and `upside_to_target`) come back `null` *and* you saw `402` warnings on stderr for that symbol, don't hunt for a code fault. PE-vs-history, FCF yield, and analyst-target upside all live on FMP, so a gated symbol has no value-axis inputs and `opportunity` collapses to `momentum`. Confirm it's per-symbol gating (the symbol 402s while AAPL/MSFT/LMT return data on the same key) rather than an exhausted quota. State plainly that the **only** fix is FMP's paid Starter tier (~$14–20/mo) — no code change recovers `value`. Note that `market_cap` and the `insider` sub-score still populate because Finnhub backfills the market cap, so a `null` `value` with a non-null `insider` is the signature of FMP gating.

---

## Score reference

| Sub-score | Default weight | Driven by |
|---|---|---|
| Quality | 25% | ROE, net margin, interest coverage, leverage (inverted) |
| Moat | 25% | Gross margin level + 5-year stability + persistent ROIC |
| Opportunity | 30% | `max(momentum, value)` — qualifies on either axis |
| Insider | 20% | Net Form-4 flow (6m) + MSPR sentiment (−1..1) |

All scores are 0–100. Actual weights and gate thresholds come from `config.yaml`.

---

## Edge cases

- `SEC_IDENTITY` unset → `edgar` skipped; insider sub-score is `null`. Mention this.
- `--research` requested but `claude` CLI not on PATH → screener warns on stderr and continues without briefs.
- `--research` skips gated tickers regardless of their rank.
- Banks/insurers (e.g. SCHW) will have blank moat/quality proxies by design — note it.
- Margins and returns are stored as fractions (0.42 = 42%) — don't re-convert when narrating.

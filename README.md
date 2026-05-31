# shortlist

A quantitative pre-screen that automates Phase 2–3 of the `investment-research`
skill: pull fundamentals, score business quality + moat, flag momentum **or**
deep undervaluation, and penalize insider selling — then hand the ranked
shortlist to the skill's filing-level deep dive.

It does the mechanical part so the judgment part is spent on fewer, better names.

## Quick start

```bash
# Recommended: uv (reproducible via uv.lock; installs core + dev deps)
uv sync
uv sync --extra edgar            # + SEC EDGAR insider source

# Or plain pip:
#   pip install -e .             # core (requests, pyyaml, rich, python-dotenv)
#   pip install -e ".[edgar]"    # + SEC EDGAR insider source

# Offline demo on the May-2026 candidate basket (no keys needed):
uv run shortlist --demo

# Live run — keys come from the environment or a .env file:
cp .env.example .env             # then fill in your keys (.env is gitignored)
uv run shortlist --tickers GEV,LMT,SCHW,TMO,GOOGL --provider fmp,finnhub,edgar --csv out.csv
```

Keys can be set either way; an explicit `export` always wins over `.env`:

```bash
export FMP_API_KEY=...            # primary fundamentals
export FINNHUB_API_KEY=...        # insider sentiment + revisions
export SEC_IDENTITY="you@you.com" # required by SEC for EDGAR
```

A missing key just skips that provider with a warning, so set only what you need.

## Why these data sources (the part that adds the value)

The design principle is **each source contributes only the fields it's genuinely
best at**, merged by priority (`merge.py`). Stacking sources beats any single API.

| Source | What it's best at here | Why it's in the chain |
|---|---|---|
| **FMP** (primary) | ratios, key metrics, price-target consensus, recommendations, insider tx | broadest coverage in the fewest calls — the backbone |
| **Finnhub** (complement) | insider **sentiment** (MSPR), recommendation-trend **deltas**, free real-time quote | clean revision direction + a normalized insider signal FMP doesn't expose as cleanly |
| **SEC EDGAR** via `edgartools` (authoritative) | Form 4 insider buys/sells, 10-K risk/material-weakness text | the *source of record* the paid APIs are derived from; free, no rate limits — best for your "minimal insider selling" criterion |
| **Quiver Quantitative** (optional edge) | congressional trades, **government-contract awards**, lobbying | gov-contract flow is a real, uncorrelated signal for defense/industrial names (LMT, GEV) that no fundamentals feed captures |
| **FRED** (optional macro) | 10y yield, fed funds, 2s10s curve | overlay to tilt the whole run when rates move against rate-sensitive names — not per-stock |
| **yfinance** (optional fallback) | price history for DMA/relative-strength | compute momentum without burning paid quota |

FMP / Finnhub / EDGAR are fully wired. Quiver and FRED are scaffolded in
`providers/extensions.py` with the interface and the specific signals to add —
they're the highest-leverage next additions, in that order.

## How scoring works (`scoring.py`)

Five sub-scores, each 0–100, every metric normalized over a configurable
`[low, high]` band in `config.yaml`:

- **Quality** — ROE, net margin, interest coverage, (inverted) leverage
- **Moat** — gross-margin level + 5y stability + persistent ROIC (excess returns)
- **Momentum** — price vs 200DMA, 6m relative strength vs SPY, estimate-revision trend
- **Value** — upside to analyst target, FCF yield, P/E vs own 5y median
- **Insider** — net Form-4 flow (scaled by market cap) + insider sentiment

`opportunity = max(momentum, value)` so a name qualifies on **either** axis
rather than being averaged down. Composite is a weighted blend (default
quality 0.25 / moat 0.25 / opportunity 0.30 / insider 0.20). **Gates** are hard
filters (negative FCF, sub-threshold market cap, over-leverage, heavy insider
selling) that flag a name regardless of score.

Tune everything in `config.yaml` — no code changes needed to re-weight.

## Reading the output

The composite ranks **business quality + opportunity**. It deliberately does
*not* know your existing portfolio — so a name can top the screen on merit yet
still be a poor *addition* if it doubles an exposure you already hold. Use the
ranking to surface candidates; use the skill (and your own allocation judgment)
to decide what actually goes in.

## Limitations

- Moat/quality proxies are equity-centric and misfire on banks/insurers
  (SCHW shows blanks for gross margin / ROIC). Add sector-aware thresholds before
  trusting financials cross-sector.
- `--demo` data in `providers/mock.py` is **illustrative**, not verified — prices
  and targets are ~accurate for late May 2026; margins/ROIC/insider are
  placeholders. Run a live provider for real figures.
- This is a pre-screen, not advice. It points the deep dive; it doesn't replace it.

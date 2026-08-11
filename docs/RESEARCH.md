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
**10-Q's MD&A**, and a **YoY Item-1A risk-factor diff** (`riskdiff.py`).

Several **prompt-only context lines** ride along. These are deliberately kept out of the
quote-verification haystack, so a computed value can never pass itself off as a filing fact:

- A reverse-DCF "price-implied FCF growth" reframing (`research/reverse_dcf.py`)
- Recent SEC filings and recent insider Form-4 trades
- DEF 14A pay and governance fields (`research/proxy.py`)
- Government contract awards, federal lobbying, earnings execution history, macro regime

The output covers moat read, material risks, red flags, management and capital allocation,
business model, a falsifiable thesis (bull / bear / what-would-change-my-mind), and a
score-vs-filing reconciliation.

## Quote verification

Factual findings — risks and red flags — must carry a **verbatim quote that is verified to
actually appear in the filing text**. Findings whose quote cannot be located are flagged as
unverifiable rather than presented as fact. Interpretive prose is labeled as interpretation.

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

Briefs are written to `research/<TICKER>/<accession>.md` (plus a `.json` sibling) and **cached
by filing accession**, so re-runs against the same filing are free. `--refresh` regenerates.
The `research/` output directory is gitignored.

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

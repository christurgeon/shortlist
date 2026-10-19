# Scoring (`scoring.py`)

How a `TickerSnapshot` becomes a ranked `ScoreCard`. See [`../README.md`](../README.md) for
the overview and [`ASSESSMENT_GAPS.md`](ASSESSMENT_GAPS.md) for what is **not** yet validated.

> **Design premise.** This is a triage funnel for a human deep dive, **not** a return-predicting
> alpha model. New scoring legs are gated hard on reproducible cross-universe rank IC — not
> added on hope. Disabling a leg that can't earn its slot is a win, not a regression.

## The seven axes

Seven sub-scores, each 0–100. Every metric is normalized over a configurable `[low, high]`
band in `config.yaml`, so tuning never requires a code change.

| Axis | Weight | Built from |
|---|---|---|
| **Quality** | 0.18 | ROE, net margin, interest coverage, leverage (inverted) |
| **Moat** | 0.18 | Gross-margin level + 5y stability + persistent ROIC (excess returns) |
| **Growth** | 0.135 | Revenue / FCF / EPS CAGR + YoY growth persistence |
| **Value** | 0.22 | FCF yield, P/E vs own 5y median, upside to analyst target, PEG |
| **Momentum** | 0.08 | Price vs 200DMA, 6m relative strength vs SPY, residual momentum |
| **Insider** | 0.135 | Net Form-4 flow (scaled by market cap) + insider sentiment |
| **Risk** | 0.0 | Realized volatility + max drawdown, both inverted (safer scores higher) — **displayed, not weighted** |

Notes that matter:

- **`roic` is a `moat` leg only** — it is not part of quality.
- **`eps_revision` is a momentum leg with no data source.** No source supplies an EPS
  estimate trend, so the leg is always empty and its weight goes to the other legs.
- **PEG scores only with a positive PEG and a positive P/E.** Shrinking earnings make PEG ≤ 0,
  which the inverted band would clamp to 100; a loss-maker with shrinking losses makes a
  positive PEG out of two negatives. Either way the leg abstains.
- **Value survives FMP gating.** FCF yield and P/E-vs-history are recoverable from free EDGAR
  + Yahoo data; only analyst-target upside and PEG genuinely require FMP.
- **Risk is displayed, not weighted.** The sub-score is still computed and shown (report, CSV,
  `/deep`), but `weights.risk` is `0.0`: its standalone cross-sectional rank IC was negative on
  both committed universes at every horizon
  ([`audits/2026-09-25`](audits/2026-09-25-risk-tilt-disable.md)). It stays sector-neutral,
  never masked, and excluded from `confidence`.

## The composite

**Value and momentum are weighted independently** (default value 0.22 / momentum 0.08 —
value pulls ~3× momentum), so undervaluation drives the ranking rather than price trend.

`ScoreCard.opportunity = max(momentum, value)` is retained for **display only** and does not
feed the composite.

When a sub-score has no inputs it is **excluded and its weight redistributed** — never
silently zeroed, which would penalize a name for a data gap.

> The default weights are a defensible prior, **not a fitted result**. See
> [`ASSESSMENT_GAPS.md`](ASSESSMENT_GAPS.md) §2.1 and §2.9.

## Gates vs flags

**Gates are hard rules.** A gated name sets `passed = false`. It still appears in the ranked
table, shown in red with the gate named in the Flags column, but `--research` skips it
(`/deep` still researches a gated name you ask for by name). The brief's screening call also treats a gate as a ceiling (see `RESEARCH.md` → The screening call).

| Gate | Trips when |
|---|---|
| `below_min_mktcap` | Market cap under `gates.min_market_cap` (default **$300M**) |
| `negative_fcf` | Negative free cash flow — **stage-aware**, excused when revenue CAGR *and* persistence both clear their bar |
| `over_leveraged` | Net-debt/EBITDA above 4.0 when EBITDA is usable, else an artifact-guarded D/E fallback above 5.0 (`gates.leverage`) |
| `heavy_insider_selling` | Insider sentiment below `gates.min_insider_sentiment` (default −0.60) |

The `over_leveraged` D/E fallback abstains on equity distortion and only trips plausible
leverage when interest coverage is also weak — this deliberately spares thin-equity buyback
compounders. Both leverage and FCF gates are config-gated (`gates.leverage` / `gates.fcf`,
both ON) and pinned by `tests/test_gate_backcompat.py`.

**Flags are advisory.** They annotate a name and **never** touch `passed`, `composite` or
`scored`.

| Flag | Meaning |
|---|---|
| `crowded_short` | Elevated short interest (keyless FINRA source) |
| `value_trap` | Cheap **and** weak on quality/growth; optional Piotroski-style refinement |
| `cash_burn` | Any negative FCF, fired regardless of whether the gate was excused |
| `dilution` | Persistent net share issuance |
| `insider_cluster_buy` | Multiple insiders buying together. Needs `insider.conviction` |
| `planned_sale` | Sales under a pre-arranged 10b5-1 plan |
| `risk_off_regime` | Leveraged name during a FRED-detected risk-off regime. Needs `FRED_API_KEY` |
| `social_hype` / `news_spike` | WSB / Finnhub mention-volume spikes |
| `filing_text_change` | Large YoY 10-K/10-Q rewrite (Lazy-Prices signal). **Disabled:** nothing produces its input |
| `activist_13d`, `passive_13g`, `recent_8k`, `planned_insider_sale_144` | A fresh filing of that type on the company exists |
| `late_filing`, `shelf_offering`, `sec_comment_letter` | A fresh NT 10-K/10-Q, S-3/424B5 shelf, or SEC staff comment letter exists |
| `restatement_8k`, `auditor_change`, `listing_deficiency` | A fresh 8-K carrying item 4.02, 4.01 or 3.01 |

Gate and flag names are declared in `scoring.py` (`KNOWN_GATES` / `KNOWN_FLAGS`). CI
AST-scans the emitters and fails if a name is emitted without being declared **and**
documented in `bot/glossary.py` — so every flag has an `/explain` entry.

## Sector-aware abstention

For businesses whose metrics don't apply — banks and brokers, insurers, REITs — the
structurally-undefined legs (gross margin, FCF yield, ROIC, leverage) **abstain** instead of
being averaged into a misleading number, and the false-positive `over_leveraged` /
`negative_fcf` gates are suppressed.

Detection is SIC-based: `EdgarSource` → `m.sic` → `sectors.py:resolve_bucket`, over
config-ordered ranges. An **unknown** sector is a bit-identical no-op — scored exactly as
before.

Each card carries:

| Field | Meaning |
|---|---|
| `sic_bucket` | The resolved sector bucket |
| `confidence` | Data completeness over *applicable* components |
| `scored` | False when too little valid signal survives |
| `abstentions` | Which legs were masked and why |

**`passed` = `not gates and scored`** — an unscored name can neither pass nor rank, and
rankings demote not-scored names. All four fields appear in `--json`; `scored` and
`sic_bucket` are also CSV columns.

v1 *masks* inapplicable legs. Sector-specific *recalibration* of the surviving ones is future
work, so treat cross-sector composites as directional. Configure via `config.yaml: sectors`
and `validity`.

### The composite floor

`validity.min_composite_components` (**ON**, default `1`) is a bucket-independent floor on
`scored`: a composite must rest on at least one real sub-score, and `risk` does not count
toward it. Without it, a name in the `unknown` sector bucket with every sub-score empty
could still post a composite.

It is a **count, not a weight threshold**: a name scored on momentum alone has very low
confidence but is legitimately scored, so no weight floor separates the two cases. No-op
when the key is absent. Pinned by `tests/test_scoring_composite_floor.py`.

## Optional legs

Config blocks, **OFF unless noted** — byte-identical output when absent.

| Leg | Config key | Status | Note |
|---|---|---|---|
| Residual (de-betaed) momentum | `momentum.residual` | **ON** | The only optional leg that passed the cross-sectional test |
| Share-count-aware quality + true diluted-EPS growth | `quality.dilution` | OFF | [`ASSESSMENT_GAPS.md`](ASSESSMENT_GAPS.md) §2.5 |
| Asset growth (inverted, Cooper-Gulen-Schill) | `quality.earnings_quality.asset_growth` | OFF | No cross-sectional edge measured |
| Accruals (inverted, Sloan) | `quality.earnings_quality.accruals` | OFF | Killed on evidence — [`audits/2026-07-12`](audits/2026-07-12-accruals-leg-disable.md) |
| Shareholder yield | `value.shareholder_yield` | OFF | [`PREDICTIVE_SIGNALS_RESEARCH.md`](PREDICTIVE_SIGNALS_RESEARCH.md) §5 |
| Insider conviction (cluster/role/10b5-1) | `insider.conviction` | OFF | One-directional — can only raise `insider` |
| SUE / earnings-surprise drift | `momentum.sue` | OFF | Needs a paid Finnhub tier for full accuracy |

## Reading the output

The composite ranks **business quality + value**, with momentum as a lighter tilt. It is
**portfolio-blind**: a name can top the screen on merit and still be a poor *addition* if it
doubles an exposure you already hold. Use the ranking to surface candidates; use your own
allocation judgment to decide what actually goes in.

The bot's `/portfolio` closes part of that gap — it re-screens what you own and reports
exposure and sector concentration alongside the same scores. See
[`TELEGRAM.md`](TELEGRAM.md).

A `coverage` diagnostic (`coverage.py`) annotates every card with per-source fetch status and
null fields, so a low sub-score can always be distinguished from a missing one.

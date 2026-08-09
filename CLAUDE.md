# CLAUDE.md

Guidance for Claude Code working in this repo. See `README.md` (overview) and `HARNESS.md`
(data layer) for user-facing docs. Detail that used to live inline here has moved to
`docs/` — every section below names its doc; read that doc before touching the feature.

## What this is

A quantitative stock pre-screen: pull fundamentals, score quality / moat / growth /
opportunity (momentum **or** value) / insider / risk, rank a shortlist for a human deep
dive. Config-driven via `config.yaml` (thresholds, weights, gates).

## Design premise — read before adding a signal

This is a **triage funnel for a human deep-dive, NOT a return-predicting alpha model.**

- We validate on ~80–238 free-tier, survivorship-biased, currently-listed names, not
  CRSP/Compustat. At that scale most factor legs are statistically indistinguishable from
  noise — a single-universe `t≈2` is usually noise (buyback, leverage tilt, and accruals all
  failed to replicate). **Stop adding scoring legs hoping one crosses `t=2`.** New scoring
  legs are gated hard on reproducible cross-universe rank IC, not the default move.
- The real edge lives in **event-driven discovery** (13D/13F/insider/buyback/8-K
  originators), not the composite. The highest-leverage work is improving what *feeds* the
  funnel and letting the selection ledger earn signals their weight over time.
- **Measure-first, kill-on-evidence, commit the evidence.** Every enabled signal that moves
  live scores needs a reproducible verdict under the tracked `docs/audits/` tree — **not**
  the gitignored `docs/superpowers/specs/` (two enablement artifacts already evaporated
  from there). Disabling a leg that can't earn its slot is a win, not a regression.
- **A committed guard outranks your reading of the numbers.** When a pre-registered floor,
  a test, or a documented rule disagrees with a story built from the data, the guard wins
  until you can state precisely why it's wrong (2026-07-26 postmortem: four conclusions were
  retracted because a floor everyone assumed was wrong turned out to be correct).

## One fetching layer: the data harness

The async `httpx` **harness** (`shortlist.data.*`) is the sole production data layer:
`Source`s in `data/sources.py` (`yahoo`, `fmp`, `finnhub`, `edgar`, `finra`, `wsb`, `mock`),
merged by `data/models.py:merge_snapshots` into an audited `TickerSnapshot`, adapted by
`bridge.py:snapshot_to_metrics` into the `StockMetrics` that `scoring.py` consumes. Two
CLIs front it: `shortlist` (rank a shortlist) and `shortlist-harness` (raw snapshots). The
keyless `yahoo` OHLCV source — we compute price/momentum/risk ourselves — **leads the price
merge** (`harness_sources: [yahoo, fmp, finnhub, edgar, finra, wsb]`). **`--provider`
overrides `harness_sources`**, so omit it on the default path or yahoo/finra get dropped.

The legacy synchronous screener was retired. Surviving in `providers/`: shared leaves
`_form4.py` (Form 4 aggregation) and `_edgar_facts.py` (10-K/balance-sheet extraction) —
used by both `EdgarSource` and the XBRL backtest, **edit extraction there, not twice**; the
`Provider` base + `MockProvider` (offline `StockMetrics` factory for scoring tests); and
the `quiver`/`fred` stubs (superseded, see `docs/DATA_SOURCES.md` §C2).

## Stacks built on the harness

Three stacks **orchestrate** the harness + scorer — discovery, validation, history, no new
scoring logic of their own:

- **Backtest** (`shortlist.backtest.*`, `shortlist-backtest`) validates the scorer against
  forward returns (rank IC + quantile spreads). `--source xbrl` validates the fundamental
  axes keylessly from SEC companyfacts and can fit weights walk-forward (`--fit`, proposes
  only, never writes `config.yaml`). See `HARNESS.md` → "Backtesting", `docs/ASSESSMENT_GAPS.md`.
- **Accumulation** (`shortlist.data.accumulate`, `shortlist-accumulate`) is idempotent
  point-in-time daily capture into `store.py` so guarded backtest paths can activate.
  Scheduling ships OFF. See `HARNESS.md` → "Feeding the snapshot path".
- **Scout** (`shortlist.scout.*`, `shortlist-scout`) autonomously discovers tickers from
  free signal feeds, deep-screens via `screen.run_harness`, runs the Claude research layer
  on leaders, ships a daily Telegram report. Design: `docs/AUTONOMOUS_SCOUT.md`. Delivery +
  bot hardening: `docs/NOTIFICATIONS.md`. Position monitor (`/add`, `/thesis`, `/hold`,
  `/remove`): `docs/POSITION_MONITOR.md`. Run **one** bot instance — two concurrent
  `getUpdates` pollers 409. The daily push is **ON** (lean digest mode, no Claude
  auto-research by default — `scout.daily_push.research: false`).

## Deploying to the VPS (the ONE command)

The live scout/bot run from **`/opt/shortlist`**, an rsync'd copy — editing this repo
changes nothing in production until deployed. From a checkout of the merged branch:

```bash
sudo bash deploy/install_opt_shortlist.sh     # rsync + uv sync + units + daemon-reload + bot restart
```

Idempotent, handles everything. Then optionally: `sudo systemctl start shortlist-scout.service`.

**GOTCHA — running the installer FROM `/opt/shortlist` is a silent no-op deploy.** `SRC` is
derived from the script's own path, so `cd /opt/shortlist && sudo bash deploy/install_opt_shortlist.sh`
sets `SRC == DEST` and rsyncs onto itself — it still reports success. Either
`cd /opt/shortlist && sudo git pull && sudo bash deploy/install_opt_shortlist.sh`, or run the
installer from a **separate** up-to-date checkout. **Always verify** —
`git -C /opt/shortlist log --oneline -1` plus a grep for a symbol you just added.

**GOTCHA — never deploy 22:30–22:35 UTC**, the scout's run window. It's `Type=oneshot` with
lazy imports, so a `git pull` mid-run leaves a torn import state (modules loaded before the
pull are old code, after are new — a real mismatch can surface as a bogus "data gap").
Check `systemctl is-active shortlist-scout.service` before pulling.

**GOTCHA — the installer generates its unit files inline; it does NOT read
`deploy/*.service`** (except `shortlist-bot.service`, which is static and real). A
`[Service]` setting added to one route must be added to both or it silently never applies
in production.

## Dev workflow (uv)

```bash
uv sync                      # core + dev deps; uv.lock pins everything
uv sync --extra edgar        # add the SEC EDGAR insider source
uv run pytest                # data-harness + scoring + provider tests
uv run pytest tests/test_scoring.py::test_norm_endpoints_midpoint_and_clamp  # single test
uv run shortlist --demo     # offline, no keys
```

`pip install -e .` still works as a fallback.

## Screen data flow

`screen.run_harness()`: `collector.collect()` → one `TickerSnapshot`/ticker → `bridge.
snapshot_to_metrics()` → flat `StockMetrics` (unavailable fields stay `None`) →
`scoring.score()` → `ScoreCard` (seven 0–100 sub-scores + composite + gates). A `coverage`
diagnostic (`coverage.py`) annotates each card with per-source fetch status and null fields.

**Value and momentum are weighted independently** (default value 0.22 / momentum 0.08).
`ScoreCard.opportunity = max(momentum, value)` is display-only, not in the composite.
Composite is a weighted blend (quality 0.18 / moat 0.18 / growth 0.135 / value 0.22 /
momentum 0.08 / insider 0.135 / risk 0.10). **Gates** are hard filters (negative FCF,
sub-threshold market cap, over-leverage, heavy insider selling); **flags** (below) are
advisory and never touch `passed`/`composite`/`scored`.

When a sub-score has no inputs it's excluded and its weight redistributed — never silently
zeroed. Tune thresholds/weights/gates in `config.yaml`, no code changes needed.

**Sector-aware abstention**: SIC-based (`EdgarSource` → `m.sic` → `sectors.py:resolve_bucket`,
config-ordered ranges). `unknown` is a bit-identical no-op. v1 masks equity-centric legs
(gross margin, ROIC, FCF-yield, leverage) for financials/insurers/REITs, since they're
structurally undefined there. `ScoreCard` carries `sic_bucket`/`confidence`/`scored`/
`abstentions`; **`passed` = `not gates and scored`** — an unscored name can't pass or rank.
Tune `sectors` + `validity`.

**Gates in detail**: `over_leveraged` trips on net-debt/EBITDA when EBITDA is usable, else an
artifact-guarded D/E fallback (abstains on equity distortion, trips plausible leverage only
with weak interest coverage — spares thin-equity buyback compounders). `negative_fcf` is
stage-aware (excused when revenue CAGR + persistence both clear their bar); a soft
`cash_burn` flag fires on any negative FCF regardless. Both are config-gated
(`gates.leverage`/`gates.fcf`, ON) and pinned by `tests/test_gate_backcompat.py`.

**Flags** (advisory, `ScoreCard.flags`): `crowded_short`, `value_trap` (cheap + weak
quality/growth, optional Piotroski refinement), `dilution` (persistent net share issuance),
`insider_cluster_buy`/`planned_sale`, `risk_off_regime` (leveraged or cyclical during a
FRED risk-off regime), `social_hype`/`news_spike` (WSB / Finnhub mention volume), presence
flags for fresh 8-K/13D/13G/144 filings, `filing_text_change` (big YoY 10-K/10-Q rewrite,
Lazy-Prices signal — `research/textsim.py`, stdlib bag-of-words cosine).

**Optional scoring legs** (config blocks, OFF unless noted — byte-identical when absent):

| Leg | Config key | Status | Note |
|---|---|---|---|
| Share-count-aware quality + true diluted-EPS growth | `quality.dilution` | OFF | `docs/ASSESSMENT_GAPS.md` §2.5 |
| Asset growth (inverted, Cooper-Gulen-Schill) | `quality.earnings_quality.asset_growth` | OFF | no XS edge measured |
| Accruals (inverted, Sloan) | `quality.earnings_quality.accruals` | OFF | killed on evidence, `docs/audits/2026-07-12-accruals-leg-disable.md` |
| Shareholder yield (straight leg) | `value.shareholder_yield` | OFF | `docs/PREDICTIVE_SIGNALS_RESEARCH.md` §5 |
| Insider conviction (cluster/role/10b5-1) | `insider.conviction` | OFF | one-directional, can only raise `insider` |
| SUE / earnings-surprise drift | `momentum.sue` | OFF | needs paid Finnhub tier for full accuracy; `docs/PREDICTIVE_SIGNALS_RESEARCH.md` §1 |
| Residual (de-betaed) momentum | `momentum.residual` | **ON** | only new leg with significant XS rank-IC (t=2.6); §2 of the same doc |

## Secrets

Keys load from the environment or root `.env` (gitignored; see `.env.example`,
`env.py:load_env()`). Run from inside the repo so `.env` is found. **Any error string that
may embed a request URL MUST pass through `env.py:redact_secrets()`** before printing/storing.

## FMP gotchas

- **`/stable/` API only** — `/v3`/`/v4` were retired 2025-08-31. Every endpoint takes `?symbol=`.
- Field moves: PE/PEG in `ratios-ttm`; ROE/ROIC in `key-metrics-ttm`; recommendations from
  `grades-consensus`.
- Insider trading is paid (402 on free) — skipped quietly, EDGAR is the free source.
- Free plan **gates many symbols per-symbol** (402 "Special Endpoint" — GEV, AXON, MELI…),
  not a quota problem. `fcf_yield` and `pe_vs_history` still recover from free sources;
  PEG/`upside_to_target` go `null`. Paid Starter (~$14–20/mo) lifts gating.

## Harness merge gotchas

- **`insider`** has a bespoke merger (`data/models.py:_merge_insider`) — coupled facts
  (`net_value_6m`/`buy_count`/`sell_count`) come wholesale from one source so they stay
  coherent; don't move it into the flat field-by-field merge set.
- **`statements`** is also bespoke (`_merge_statements`): the highest-priority source with
  data wins the object, and its `fiscal_years` becomes a join key — backfill from lower
  sources is re-indexed **by fiscal year, never by list position** (FMP typically carries 5
  years, EDGAR ~3; a positional backfill pairs mismatched years silently). Full reasoning +
  known limitations: `docs/STATEMENTS_MERGE.md`.
- **EDGAR statements** (`providers/_edgar_facts.py`): `diluted_shares`/`diluted_eps` row
  selection matches the raw `concept` column first (not `standard_concept`, which drifts
  across edgartools releases), value-aware so a sparse concept row can't shadow a working
  label row. 9 issuers with no share-count concept at all recover via a `companyconcept`
  API fallback. `diluted_shares` is **not always absolute** (MCD reports in millions).
  Details + verified facts: `docs/audits/2026-07-31-edgar-concept-match.md`,
  `docs/audits/2026-08-02-edgar-companyconcept-fallback.md`.
- **`EdgarSource`** wraps sync `edgartools` in `asyncio.to_thread`, rate-limited by a
  shared semaphore (`_EDGAR_MAX_CONCURRENCY`, default 3). `set_identity` is process-global,
  set once in `__init__`.

## Short interest (harness)

`FinraSource` (keyless) pulls `ConsolidatedShortInterest` (not the OTC-only, frozen
`EquityShortInterest`). Symbol field is `symbolCode`; `settlementDate` is a partition key
(discover the latest cycle via `/partitions/`, can't sort it in the data query);
`record-max-limit` is 5000, paginate. One bulk fetch/run, cached by settlement date. Row
shape + helpers are single-sourced in `data/finra.py` so the harness source and the scout
fetcher agree on one cache contract.

## The shared sec.gov throttle

`scout/sec_throttle.py` owns a process-wide ~6 req/s budget for the EDGAR-index-based scout
originators (Form 4, 13D/13D-A, 13F, buyback, DERA). **Never give a signal its own
throttle** — that broke the funnel outright on 2026-08-04 (`docs/audits/2026-08-05-discovery-funnel-audit.md`).
Full rules (concurrency, retry/fallback behavior, two rejected volume "optimisations"):
`docs/EDGAR_ORIGINATORS.md`.

## Scout discovery originators

All are keyless, VPS-safe (no Yahoo WAF dependency unless noted), reuse the scorer, and
ship as either a **defensible** established-positive prior (ships enabled) or a
**contested** prior (ships disabled at low weight, attention not direction, judged by the
selection ledger). Implementation-level verified facts + landmines for the EDGAR-index ones
live in `docs/EDGAR_ORIGINATORS.md`; design docs are in `docs/superpowers/specs/`
(**gitignored — do not rely on them surviving a fresh clone**).

| Signal | Module | Status | What / why |
|---|---|---|---|
| FINRA short-interest jump | `FinraShortInterestSignal` | OFF, weight 0.5 | contested (negative base rate in lit.); emits once/settlement cycle |
| Activist 13D (initial) | `EdgarActivist13DSignal` | ON, weight 1.5 | defensible — re-rating catalyst; scout's highest-tier originator |
| 13D/A stake increase | `EdgarStakeIncreaseSignal` | OFF, weight 0.5 | pending its own backfill verdict |
| 8-K item match (1.01∧3.03) | `EdgarEightKSignal` | OFF, weight 0.5 | contested; negative-item veto (separate, ON) drops candidates hit by items {1.03,2.04,2.05,2.06,3.01,4.02,5.01} |
| Buyback authorization | `EdgarBuybackSignal` | OFF | killed on evidence, `docs/audits/2026-07-11-buyback-backfill-kill.md` |
| 13F marquee-fund cloning | `EdgarThirteenFSignal` (`edgar:13f_new_position`) | ON, weight 1.0 | defensible (Martin-Puthenpurackal 2008); info up to 45d stale |
| 13F material add (shares ≥ +50%) | same signal object (`edgar:13f_material_add`) | ON, weight 0.75 | second diff over the same filing pair, **zero extra SEC requests**; detected on **share count**, not value; `docs/audits/2026-08-09-13f-material-adds-design.md` |
| Opportunistic Form 4 (CMP classifier) | `EdgarForm4Signal` | ON, weight 1.0 | drift capture, not an information edge; `docs/FORM4_INSIDER.md` (weight there reads stale 1.5 — 1.0 is current) |
| WSB rank-novelty | `WsbHypeSignal` (novelty submode) | ON | emits only on a ticker that isn't a board regular; `docs/audits/2026-08-07-wsb-novelty-rule.md` |
| Yahoo predefined screeners | `YahooScreenerSignal` | ON | WAF-fragile — see below |
| Investability floor (market cap + $ADV) | `funnel.apply_investable_floor` | ON | is the *security* reachable, not the business; `docs/audits/2026-08-07-investability-floor.md` |
| Quality floor (no revenue / neg equity+earnings+OCF) | `funnel.apply_quality_floor` | OFF | slot-hygiene, not a ranker; `docs/audits/2026-08-05-quality-floor-evidence.md` |
| Per-originator slot cap | `budget.select(..., caps)` | opt-in per signal | `scout.signals.<name>.max_slots`; only engages under contention |

Non-scored context lines (research layer only, never a gate/flag): gov contracts
(`data/govcontract_match.py`, USAspending `spending_by_transaction`), federal lobbying
(Senate LDA API), news flow (`Finnhub company-news`), earnings execution/surprise history.

**Yahoo screener WAF gotcha**: `query1.finance.yahoo.com/.../screener/predefined/saved`
rejects bot-shaped requests with an HTML 429 from the edge WAF (not throttling) — a full
browser header set (`_YAHOO_HEADERS`) returns 200 JSON. Never retry-spam an HTML 429 (bail
after one request); `daily.py` persists a rest-of-day cooldown in `ScoutState`. Only a JSON
429 with `Retry-After` is retried, once, capped.

**13F emits TWO kinds from ONE signal object** — `edgar:13f_new_position` and
`edgar:13f_material_add`. Two seams make that work, and a third kind must touch **both** or it
silently misbehaves: `daily.py:_scan_discovery` reads an optional `cfg_key_for(emission)` hook
so the kinds carry different **weights**, while `max_slots` is resolved from the signal's own
key and governs the family (a cap read per-emission *vanishes* on an adds-only night);
`budget.signal_family` collapses both strings to `edgar:13f` for caps **and** confluence, since
two funds agreeing is not two originators agreeing. Adds are detected on **share count**
(`sshPrnamt`) because `value` is quarter-end market value — a price rise alone would read as
conviction.

## Signal-validation evaluator

`scout/validate.py` (KILL/HOLD/INSUFFICIENT verdict) + `scout/preregister.py`
(anti-p-hacking gate). Three load-bearing contracts — full detail + evidence in
`docs/EVALUATOR_CORRECTNESS.md`: `load_prereg` parses the **committed** `git show
HEAD:<path>`, never the working tree (an uncommitted threshold edit must not read as
pre-registered); bootstraps **resample issuers**, not events, and relabel per issuer-copy;
and the evaluator **abstains** (CI = `None`) rather than substituting a different bootstrap
model when the issuer-level one can't compute. Two previously-published 13D/13D-A spread
claims were retracted 2026-08-03 after a bug fix — see
`docs/audits/2026-08-03-evaluator-rederivation.md` before citing either number.

## Scale, caching, and data conventions

Free tiers fit a watchlist, not a full universe: ~13 FMP calls/ticker, 250/day free ≈ 19
tickers/day (scout caps deep-screening at 10/day, `shortlist-accumulate` at 15/day). FMP
429s degrade honestly (`Retry-After`-aware retry, partial-success coverage). Daily S&P 500
needs FMP Starter or the cache.

**Caching** (`cache.py`): SQLite HTTP cache wraps `FMPSource`/`FinnhubSource`, on by default
(`.cache/http.sqlite`), `--no-cache`/`--refresh-cache` to bypass. Never cache soft failures
(200-OK-with-empty-body gating responses); bump the `v1:` key prefix whenever a `_get`
output shape changes. Yahoo/FINRA keep their own disk caches; EDGAR is intentionally
uncached (free, uncapped).

**Data scale**: margins/returns are fractions (FMP `/stable/` already fractional; Finnhub
percentages need `÷100`). `market_cap` is absolute USD from FMP; Finnhub reports millions in
the issuer's **native currency** and is used **only when `currency == "USD"`** — a foreign
issuer misread here silently inflates market cap and can mask the `below_min_mktcap` gate
(TSM was once recorded as a $60.2T market cap this way).

## Extension scaffolds

`providers/extensions.py` has `QuiverProvider`/`FredProvider` stubs. FRED has shipped for
real as `data/macro.py` (risk-off regime overlay). Quiver is largely superseded (gov
contracts/lobbying/WSB shipped keyless); its one net-new feed, congressional trades, is a
contested prior deferred per `docs/PREDICTIVE_SIGNALS_RESEARCH.md`. To wire anything here,
reimplement as an async `Source` in `data/sources.py` — the harness Source registry, not
`providers/__init__.py:build_providers`, is what `--provider`/`harness_sources` resolves
against.

## Skills

- **`/run`** — end-to-end screener skill (gather tickers → `uv run shortlist --json` →
  interpret scores/gates/coverage). `.claude/skills/run/SKILL.md`.

## Qualitative research layer (`shortlist/research/`)

Opt-in `--research N` enriches top-N non-gated names with a Claude-written 10-K brief via
the **`claude` CLI in headless mode** (`research/claude_cli.py`), not the API SDK — no key,
uses CLI auth. Keep the lockdown flags: `--tools "" --strict-mcp-config --max-turns 1`,
prompt on stdin, neutral cwd, **no `--bare`** (forces `ANTHROPIC_API_KEY`). Lazy-imported so
the core screener works without `claude`/edgartools. Briefs cached by filing accession,
facts quote-verified against the filing, interpretive prose labeled. Output under
`research/` (gitignored).

The brief bundles the latest 10-K, the latest 10-Q's MD&A, and a YoY Item-1A risk-factor
diff (`riskdiff.py`). It also carries several **prompt-only context lines** (never the
quote-verification haystack, so a computed value can't pass as a filing fact): a reverse-DCF
"price-implied FCF growth" reframing (`research/reverse_dcf.py`), recent SEC filings, recent
insider Form-4 trades, DEF 14A pay/governance fields (`research/proxy.py`). Ends with a
**screening call** (buy/hold/avoid + conviction) bounded by three deterministic guards in
`assess.py:apply_guards` — a gate clamp, a conviction cap on low confidence, and a
HIGH-conviction corroboration requirement. Framed as **screening triage, not investment
advice** everywhere it surfaces.

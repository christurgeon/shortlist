# Autonomous scout — signal-driven candidate discovery

**Audience:** whoever builds the autonomous discovery loop that turns this tool from a
"give it tickers" screener into a "tell me what to look at today" system.
**Companion docs:** [`DATA_SOURCES.md`](DATA_SOURCES.md) (the free feeds the signals draw
on), [`ASSESSMENT_GAPS.md`](ASSESSMENT_GAPS.md) (the scorer the candidates flow into),
and the repo `CLAUDE.md` (the harness architecture and house rules this extends).

> **Status:** **Shipped** (`shortlist-scout` CLI, 2026-06). This is the design record approved
> 2026-06-01; the implementation is complete. Report delivery: PNG dashboard glance
> (sendPhoto) + styled HTML deep-dive (sendDocument), with a chunked plain-text fallback when
> Telegram is unconfigured or failing; artifacts saved under `scout/<date>/`. See
> `docs/NOTIFICATIONS.md` for delivery semantics.
>
> **Update (2026-06-07):** the **autonomous daily push is now feature-flagged OFF by default**
> (`config.yaml: scout.daily_push.enabled: false`). On the production VPS, Yahoo's edge WAF
> IP-blocks the screener endpoint (repo `CLAUDE.md` → "Yahoo WAF gotcha"), so signal-driven
> discovery breadth is unreliable there. The primary driver is now an **interactive Telegram
> bot** (`shortlist-bot`, `scout/bot.py`) that lets the operator drive screening on demand
> (`/screen`, `/deep`) over the same scorer + report pipeline this doc designs — discovery
> becomes human-in-the-loop. The autonomous loop below is intact and flippable back on. Inbound
> design: `docs/superpowers/specs/2026-06-06-scout-telegram-bot-design.md`; operator notes:
> README → "Interactive bot" and `deploy/README.md`; delivery counterpart: `docs/NOTIFICATIONS.md` §7.

---

## 1. The goal and the honest framing

Today the harness (`shortlist-harness`) **requires** a ticker list —
discovery is a human job. The goal is to close that loop: **autonomously surface candidate
tickers each day, screen and rank them, and deliver a report of the top names** — with the
expensive Claude 10-K research run on the leaders so the report arrives decision-ready.

The four decisions that shape this design (all settled):

| Decision | Choice | Consequence |
|---|---|---|
| **Discovery** | **Signal-driven** (no fixed universe) | Candidates come from what names *just did something*, not a list to walk. |
| **Budget** | **Strictly free forever** | No paid FMP tier assumed. Daily throughput is capped (~10 deep screens/day). |
| **Delivery** | **VPS systemd timer → Telegram** | Runs on `oracle-prod` like the oracle bot; report lands in Telegram. |
| **Autonomy** | **Fully autonomous, incl. research** | The loop also runs the Claude 10-K briefs on the top names — hands-off. |

### The tension, stated plainly
Signal-driven discovery on strictly-free feeds is the leanest and most ambitious of the
options, and it has one structural caveat and one structural risk:

- **Caveat — discovery is bounded by free feeds.** The richest discovery signals
  (congressional trades, options flow, premium screeners, real-time analyst-revision feeds)
  are paid. So "discovery" here means *"the free signals we can actually see."* That set is
  real and useful — but the report **must say which signal sources ran and which didn't**,
  the same coverage-honesty rule the screener already applies to data gaps. A signal source
  that errors or is rate-limited **shrinks the funnel visibly**, never silently.

- **Risk — signal-driven discovery chases momentum.** "Top gainers" screens structurally
  surface what already ran. The counterweight is **already built**: the scorer's `value`
  axis (`max(momentum, value)` — a name can qualify on cheapness *instead* of momentum) and
  the FCF / leverage / insider-selling gates. So the division of labor is deliberate:
  **discovery casts a wide, noisy net; the existing scorer is the skeptic.** The scout adds
  no new judgment — it feeds the judgment that already exists.

This is the right direction *because* of that division of labor, not in spite of it. The
scout is plumbing; the scoring model stays the single source of truth on quality.

---

## 2. Architecture — a thin layer that orchestrates, not replaces

The scout is a **thin layer** that *orchestrates* the existing harness (`shortlist.data.*`).
It pulls candidates, hands each to the **existing** harness screen + scorer, runs the **existing**
research layer on the leaders, and ships a report. It introduces no new scoring and no new
fundamentals fetching — only **discovery** and **delivery**.

```
   free signal feeds                 EXISTING harness (unchanged)           delivery
 ┌───────────────────┐   Candidate  ┌──────────────────────────┐  ScoreCard ┌──────────┐
 │ YahooScreener (D) │─┐  objects   │ harness collector +      │   + brief  │ Telegram │
 │ EdgarForm4    (D) │ │   ┌──────┐ │ scoring (bridge ->       │  ┌───────┐ │  message │
 │ FinnhubNews   (C) │ ├──▶│funnel│▶│  scoring.score)          │─▶│report │▶│  + JSON  │
 │ Wikipedia     (C) │ │   │+budget│ │ research (Claude CLI)    │  └───────┘ │ artifact │
 │ (Quiver — stub D) │─┘   └──────┘ │ (D)=discovery (C)=conflu. │            └──────────┘
 └───────────────────┘             └──────────────────────────┘
```

### New package: `shortlist/scout/`

Mirrors the existing registry pattern (a `_REGISTRY` of named sources, like
`providers/__init__.py` and `data/sources.py`).

| Module | Responsibility | Depends on |
|---|---|---|
| `signals.py` | Registry of free `SignalSource`s. Each `.scan(session)` emits `Emission(ticker, signal, strength, evidence)` for names that tripped its rule for the given market session. `MockSignal` powers offline `--demo`. | `httpx`, `env.py`, `calendar`, `edgar_index` |
| `edgar_index.py` | **New ingestion path** (not in `_form4.py`): fetch the SEC Form 4 *daily index* for a session, fetch+parse each filing, classify P/S transactions, map CIK→ticker, group by issuer → same-day cluster buys. Has its **own** concurrency budget and a per-day fetch cap. Reuses only the P/S classification logic factored out of `providers/_form4.py`. | `edgartools`, SEC index |
| `models.py` | `Emission`, `Candidate` (one ticker + the set of signals that flagged it + composite `interest` + evidence), `ScoutReport`, `RunManifest`. | stdlib `dataclasses` |
| `funnel.py` | Aggregate emissions → `Candidate` per ticker. Prefilter: cooldown (skip names screened within N days), liquidity / market-cap floor (one cheap quote), dedup, drop names on a held/ignore list. | `models`, `state` |
| `budget.py` | The honest cap (§4.1). Selects the top-`X` candidates by `interest` that fit today's deep-screen ceiling. Logs what it dropped. | `config.yaml` |
| `state.py` | **New** idempotent scout ledger (not `data/store.py`, which is a per-ticker snapshot writer): a screened-ticker→session ledger for the cooldown, a per-session `run_completed` marker for safe timer retries, and the held/ignore list. Single-writer (one-shot timer) but documents read-modify-write semantics. | stdlib `json` |
| `calendar.py` | US trading-calendar gate (§3 step 0): resolve "today" to the last completed market session; skip weekends/holidays so the scout never re-emits Friday's gainers all weekend. | `pandas-market-calendars` or a static holiday table |
| `report/` | Renderer-agnostic view-model → section registry → HTML/text renderers + Pillow PNG "glance". `build_report([ScoreCard], RunManifest, assessments) → ReportArtifacts(html, png, text)`. Adding a section = one `Section` class + one `SECTIONS` entry. Pillow is lazy-imported **only** in `report/png.py`. | `models` |
| `notify.py` | `TelegramNotifier` + `deliver(artifacts, manifest, notifier)` — sends chart (sendPhoto), HTML doc (sendDocument), chunked text fallback. `Notifier` protocol keeps `daily.py` transport-agnostic. | `env.py` |
| `daily.py` | The loop orchestrator (§3). CLI entry `shortlist-scout`. | all of the above + existing stacks |

### The `SignalSource` interface

```python
@dataclass
class Emission:
    ticker: str
    signal: str            # e.g. "yahoo:day_gainers", "edgar:form4_cluster_buy"
    strength: float        # 0..1, source-normalized; how strongly this rule fired
    evidence: str          # human-readable, for the report ("+8.4% on 3x volume")

class SignalSource(Protocol):
    name: str
    is_discovery: bool      # True = can originate unknown tickers; False = confluence-only (§4)
    def scan(self, session: date) -> list[Emission]: ...   # hits for that session; [] on error
    def available(self) -> tuple[bool, str]: ...  # (ran?, why-not) for coverage honesty
```

Same shape as the harness `Source`: a name, a registry entry, graceful degradation, and an
audit of whether it ran. Errors route through `env.py:redact_secrets()` before logging. The
`session` argument is the resolved last-completed market session (§3 step 0), not wall-clock
"today" — so signals are deterministic and reproducible.

---

## 3. The daily loop (`daily.py`)

0. **Resolve session + idempotency guard.** `calendar.py:last_session()` resolves "today" to
   the last *completed* US market session — on a weekend/holiday it anchors to the prior
   trading day (the scout does **not** abort on a non-trading day; it screens off that prior
   session, so "today's gainers" always refers to a real session). If `state.py` already has a
   `run_completed` marker for the resolved session, **exit cleanly** (a retried/duplicate timer
   fire is a no-op, not a second report). Delivery semantics: a configured-but-failed Telegram
   send still persists + writes the artifact and marks the run complete (so it is *not*
   retried — the report is recoverable from the journal and `scout/<session>.txt`), but exits
   non-zero so the `OnFailure` alert fires. An *unconfigured* Telegram (no token/chat) is the
   expected stdout-only mode and exits 0.
1. **Scan.** Each enabled `SignalSource.scan(session)` runs (free APIs only), emitting
   candidates for names that tripped its rule for that session. A source that
   errors/rate-limits returns `[]` and reports `available() == (False, reason)`.
2. **Funnel.** Aggregate emissions → one `Candidate` per ticker, carrying the *set* of
   signals that flagged it. A name flagged by a **discovery** source *and* confirmed by a
   confluence booster outranks one flagged by a single noisy gainer screen — confluence is
   the core ranking idea (§4.1).
3. **Prefilter.** Drop names screened within the cooldown window, below the market-cap /
   liquidity floor, on the held/ignore list, or duplicates.
4. **Budget.** Select the top-`X` candidates by `interest` that fit today's deep-screen
   ceiling (§4.1). `log()` the count dropped for budget so truncation is never silent.
5. **Deep-screen.** Hand each selected ticker to the **existing harness** path
   (`data.collector.collect` → `bridge.snapshot_to_metrics` → `scoring.score`) → `ScoreCard`.
   No new *scoring* code. As of the harness coverage-parity work on `main`,
   `screen.run_harness` now **also** attaches the per-ticker `coverage` diagnostic (via
   `data.coverage_adapt.snapshot_to_coverage_inputs` → `build_coverage`), so each card carries
   `coverage.note` (e.g. "FMP gated this symbol"). The scout surfaces that note per name in the
   report (step 8) — data-layer coverage honesty on top of the signal-layer coverage (§7).
6. **Rank + gate.** Order by composite; surface gates (FCF / leverage / insider) inline.
7. **Auto-research.** Run the **existing** Claude-CLI research layer on the top-`N` non-gated
   names (`N` ≪ `X`; hard-capped; kill-switch; auth-probed — see §5).
8. **Report.** `report/` assembles ranked shortlist + briefs + the signal-coverage line into a
   PNG dashboard "glance" (sendPhoto) and a styled HTML deep-dive (sendDocument), with a
   chunked plain-text fallback when Telegram is unconfigured or failing. `notify.py:deliver()`
   handles the multi-message delivery; artifacts (dashboard.png, report.html, report.txt,
   manifest.json) are written under `scout/<date>/` (signal availability, funnel counts, budget
   drops, research outcome) for trend debugging regardless of Telegram outcome.
9. **Persist.** Record screened tickers + session in the `state.py` ledger (for the cooldown)
   and set the `run_completed` marker, so a retry is a no-op and tomorrow's run skips today's
   names.

`--demo` runs the whole loop offline with `MockSignal` + the existing mock provider and
prints the report to stdout instead of Telegram — the same offline-first ergonomics the
screener already has.

---

## 4. The free signal set

All keyless or already-keyed; all documented (with validated pulls) in `DATA_SOURCES.md`.
The scout **consumes** these as discovery triggers; several are also wishlisted there as
*scoring* inputs — the scout reuses the same pullers where they overlap.

**Two roles, and they are not interchangeable.** Only a source that can *name a ticker we
didn't already know* is a true **discovery** source. A source that needs a symbol as input
(Finnhub `company-news`) or a curated map (Wikipedia) can only *confirm* an
already-discovered name — it's a **confluence booster**, not an originator. The funnel treats
them differently: discovery sources populate the candidate set; boosters only raise the
`interest` of names already in it (which also bounds their call volume to the funnel size).

| Source | Role | Fires on | Access | Notes |
|---|---|---|---|---|
| **`YahooScreenerSignal`** | **discovery** | Yahoo *predefined* screeners: `day_gainers`, `day_losers`, `most_actives`, `undervalued_growth_stocks`, `aggressive_small_caps`, etc. | keyless `query1.finance.yahoo.com/v1/finance/screener/predefined/saved` | The workhorse. **Unofficial, unauthenticated endpoint** — *verified to return populated `quotes` only with a browser `User-Agent` (`Mozilla/5.0`); it `429`s without one.* Distinct host/path from the existing `YahooSource` (`/v8/finance/chart`), so it needs its own UA + day-cache. Treat as best-effort: if it silently `429`s, the discovery funnel collapses to EDGAR alone — so a smoke test asserts `quotes` is non-empty, and the signal-coverage line surfaces the outage. |
| **`EdgarForm4Signal`** | **discovery** | Same-session **insider cluster buys** from the SEC Form 4 **daily index**. | free SEC EDGAR (`set_identity` required) | The **highest-signal free source** — but a **new ingestion path** (`edgar_index.py`), *not* a reuse of `_form4.py`, which is strictly per-ticker (`Company(ticker).get_filings()`). The daily index is ~1,700 Form 4 rows/day giving only CIK+accession; surfacing cluster buys means fetching+parsing each filing, classifying P/S, and mapping CIK→ticker. That runs under EDGAR's ~10 req/s fair-access limit with a **per-day fetch cap** and its **own** concurrency budget (the per-ticker `_EDGAR_MAX_CONCURRENCY` semaphore does not bound this). MVP may cap the daily fetch count and note the truncation in coverage. Reuses only the P/S classification logic factored out of `_form4.py`. |
| **`EdgarActivist13DSignal`** | **discovery** | Fresh **initial SCHEDULE 13D** activist stakes from the SEC daily index (an investor crossed 5% with intent to influence — a leading re-rating catalyst). | free SEC EDGAR (`set_identity` required), keyless | **SHIPPED 2026-06-29.** A second ingestion path in `edgar_index.py`. **VPS-safe** (no Yahoo WAF), so it backfills the dead-Yahoo discovery gap. The **subject company** (not the filer/activist) is the target ticker, resolved CIK→ticker via `company_tickers.json` with **common-stock preference** (`scout/cik_tickers.py`); the noisy firehose (SPAC shells, foreign holdcos, affiliate/sponsor filings) is filtered by `scout/quality.py` (drop SPACs/affiliates, boost curated marquee activists), with the scorer + market-cap gate as the downstream skeptic. Initial 13D only (`/A` amendment-spam excluded). Discovery-only (no scored leg) — the **selection ledger** measures it. See repo `CLAUDE.md` → "Activist 13D discovery + selection ledger". |
| **`FinraShortInterestSignal`** | **discovery** | Tickers whose **short interest jumped** vs the prior FINRA settlement cycle, filtered to a **moderate (non-extreme) crowding band**. | keyless FINRA `ConsolidatedShortInterest` (shares the harness `FinraSource` disk cache) | **SHIPPED 2026-06-29, default OFF.** The discovery analogue of the per-ticker `crowded_short` flag, and **VPS-safe** (no Yahoo WAF). A **CONTESTED prior**, NOT a defensible one — heavy/rising short interest has a *negative* base rate for a long book (Asquith-Pathak-Ritter 2005; Cohen-Diether-Malloy 2007 — *the jump is the negative signal*; Hong et al 2016 — DTC is a *stronger* negative predictor). So it supplies **attention, not direction** (the scorer/gates judge the sign; the ledger measures it), ships **disabled at weight 0.5**, and uses a **middle band** (a jump off a non-extreme base, the falling-knife tail excluded) rather than the floors the advisory flag uses. Emits **once per new settlement cycle** (the data updates ~bi-monthly; a per-cycle `ScoutState` gate stops daily re-emission). `scout/short_interest.py` (pure aggregator + sync fetcher) + `scout.short_interest` config. See repo `CLAUDE.md` → "Short-interest discovery (scout)". |
| **`EdgarThirteenFSignal`** | **discovery** | **New positions** in a curated set of marquee funds' latest **13F-HR** (a CUSIP present now, absent in the prior quarter's filing) that clear a within-book weight floor. | keyless SEC (submissions JSON + information-table XML + FTD-file CUSIP map) | **SHIPPED 2026-07-09, default ON at weight 1.0.** Marquee-fund cloning — a **DEFENSIBLE, established-positive prior** (Martin-Puthenpurackal 2008; Cohen-Polk-Silli 2010 "best ideas"), so it ships enabled like the 13D originator, but *below* the 13D/Form-4 tier because the info is up to **45 days stale** (the clone return is measured from the FILING date, the disclosure lag priced into the literature). **VPS-safe** (pure SEC, no Yahoo WAF). Per fund: latest 13F-HR (amendments excluded) diffed against the prior; new positions resolved CUSIP→ticker via SEC fails-to-deliver files (`scout/cusip_map.py`, most-recent-settlement wins) with an exact-normalized-issuer-name fallback, abstaining on a miss. `max_filings_per_day` caps processing (13F is quarterly-bursty); unprocessed filings **carry over** (never dropped). Emissions carry `cik=None` (the CUSIP resolver yields a ticker but no CIK — a stated measurement limit). Backfill deferred (a PiT CUSIP→symbology replay is future work). `scout/thirteenf.py` + `scout.thirteenf` config. See repo `CLAUDE.md` → "13F marquee-fund cloning (scout)". |
| **`EdgarBuybackSignal`** | **discovery** | 8-Ks whose full text announces a **new/expanded share-or-stock repurchase authorization** (verb-anchored EFTS phrase query). | keyless SEC EDGAR full-text search (EFTS; shared `data/efts.py`, own `.cache/efts_buyback` namespace) | **SHIPPED 2026-07-09, default OFF.** **VPS-safe** (SEC-hosted, no Yahoo WAF). A **DEFENSIBLE academic prior** (Ikenberry-Lakonishok-Vermaelen 1995; Peyer-Vermaelen 2009 — positive post-announcement drift), but shipped **disabled at weight 0.5** on the 8-K MEASURE-FIRST precedent: the sign in *this* funnel's universe/horizon is what the pre-registered backfill cohort (`preregister/edgar_buyback_auth.yaml`, K=3m) earns or kills — and it **KILLED** it (2026-07-11: FF3 alpha −0.84%/mo, 90% CI entirely negative; `docs/audits/2026-07-11-buyback-backfill-kill.md`), so it stays OFF on evidence. The **filer IS the subject** (CIK→ticker via `cik_tickers`, no `display_names` fallback); SPAC/SIC-6770 drops; walk-back `session-2..session` per phrase with a capped accession-seen set in `ScoutState`. Phrase precision live-probed **~97% (29/30)**. `daily_cap`/`deny_list` are **live-only** knobs the cohort never applies. `scout/buyback.py` (pure aggregator) + `scout.buyback` config. See repo `CLAUDE.md` → "Buyback-authorization discovery (scout)". |
| **`FinnhubNewsSignal`** | confluence | News-volume spikes / sentiment on **already-discovered** names (`company-news` — *requires a symbol*). | free Finnhub key (already configured) | Free-tier endpoints (validated in `DATA_SOURCES.md` B2). Cannot originate a candidate; only boosts ones Yahoo/EDGAR found. |
| **`WikipediaAttentionSignal`** | confluence | Daily **pageview spikes** for a curated ticker→article map. | keyless Wikimedia REST (descriptive User-Agent) | Attention proxy (`DATA_SOURCES.md` A4). Needs a known ticker→article map ⇒ confirmation only, low weight. |
| **`QuiverSignal`** *(stub)* | discovery | Congressional trades / gov-contract awards. | needs a key | Registered, unwired stub (like `providers/extensions.py`) — activates if a free key appears. Honors "strictly free": **off by default**. |

So the MVP has **two** real discovery originators (Yahoo screeners, EDGAR Form 4) and two
confluence boosters. Adding a signal is a one-file change: implement `SignalSource`, mark its
role, register it, give it a `config.yaml` weight. GDELT news tone (`DATA_SOURCES.md` A5,
*discovery*) and FINRA short-interest jumps (C1, *discovery*) are the natural next two and
would widen the originator base.

### 4.1 Throughput, the budget, and the FMP question

The funnel is free; the **deep screen is what costs**. Today the harness deep-screen chain
includes `FMPSource` (~13 FMP calls/ticker), and FMP's free **250/day** ⇒ ≈ **19 screens/day**
— that single source, not the free signal feeds, is what forces a low `daily_x` (ships 10). This is in
tension with "strictly free + scalable": we've built a free discovery firehose bolted to an
FMP-limited funnel.

The design resolves it in two stages, stated honestly rather than hand-waved:

- **MVP — keep FMP, accept the ~10/day cap.** FMP still carries the `value` axis (PE-vs-history,
  FCF yield, analyst-target upside) and several quality/moat fundamentals, and 10 well-chosen
  names/day is a reasonable human reading load. The cap is real and printed (§7).
- **Scale path — drop FMP from the *scout's* deep-screen chain.** Run the scout on
  `harness_sources: [yahoo, finnhub, edgar]` so throughput is bound by **Finnhub's 60/min**
  (comfortable headroom), not FMP's daily quota. The cost is the `value` axis collapsing to
  `momentum` for scout-discovered names (the scorer's weight-redistribution already handles a
  null sub-score honestly) until **EDGAR XBRL fundamentals** (`DATA_SOURCES.md` A1) backfill
  the gap keylessly. This is the durable answer to "strictly free forever" and is wired as a
  config switch (`scout.deep_screen_sources`), defaulting to the MVP chain.

`interest` = Σ over the signals that flagged a candidate of `strength × weight`. **Caveat:**
each source normalizes `strength` to 0..1 independently, so the sum is only an **ordinal,
within-a-session** ranking — not comparable across days, and not a calibrated probability.
Real signal→forward-return weighting is deferred to the `ASSESSMENT_GAPS.md` §2.1 backtest
harness; until then the weights are a defensible prior (like the scoring weights), and
per-candidate `interest` is capped so one source can't dominate purely by firing many rules.

### Config (in `config.yaml`, new `scout:` block)

```yaml
scout:
  daily_x: 10                  # max deep screens/day (FMP-bound; see §4.1)
  research_top_n: 3            # Claude briefs run on the top-N non-gated names (absolute cap)
  research_phase_budget_s: 2000 # wall-clock ceiling for the whole research phase (§5)
  cooldown_days: 7             # don't re-screen a name within this window
  # market-cap floor is enforced by the existing scoring gate (gates.min_market_cap),
  # not a scout-level prefilter — market cap isn't known until the deep screen runs.
  deep_screen_sources: [yahoo, fmp, finnhub, edgar, finra, wsb]  # drop fmp to scale (§4.1)
  edgar_index_daily_cap: 400   # max Form 4 docs fetched/session (cap shown in coverage)
  wikipedia_ticker_map: {}     # curated ticker -> article map; empty = booster no-ops honestly
  signals:                     # per-source enable + interest weight
    yahoo_screener:  {enabled: true,  weight: 1.0}
    edgar_form4:     {enabled: true,  weight: 1.5}   # highest-signal free source
    finnhub_news:    {enabled: true,  weight: 0.5}
    wikipedia:       {enabled: true,  weight: 0.5}
    wsb_hype:        {enabled: true,  weight: 0.5}   # WsbHypeSignal — ApeWisdom WSB mention velocity (keyless)
    quiver:          {enabled: false, weight: 1.0}   # strictly-free: off until keyed
```

A disabled signal (e.g. `quiver` above) still surfaces in the report's coverage line as
`✗ (disabled)` — coverage honesty (§7) covers the *configured* signal set, not just the
ones that ran.

The research kill-switch is **not** a config-only flag (a redeploy is too slow to stop a bad
run): it reads `SCOUT_NO_RESEARCH=1` from the environment / a `scout/STOP_RESEARCH` sentinel
file at the top of step 7, so it can be flipped on the running box without a deploy.

---

## 5. Fully-autonomous research — the guardrails

"Fully autonomous incl. research" means step 7 runs **unattended on the VPS**. The existing
research layer already uses the **`claude` CLI in headless mode** (no API key; uses the
user's CLI auth) with a strict lockdown (`--tools "" --strict-mcp-config --max-turns 1`).
Running it unattended adds these requirements:

1. **CLI availability gate + per-name graceful skip (shipped behavior).** Before step 7 the
   loop gates on `research.is_available()` (the `claude` binary on PATH + `edgartools`
   importable); if unavailable it **degrades to screen-only and says so in the report**. An
   *expired* OAuth token isn't caught up front — instead each per-name brief fails
   independently and is recorded as `skipped="…"`, which surfaces in the report rather than
   crashing the run. This is robust (no mid-run death, reasons visible) but weaker than a true
   up-front auth probe; a cheap `--max-turns 1` no-op probe before the batch is a tracked
   enhancement (§9), deferred to avoid an extra `claude` invocation every run.
2. **A hard cap, not a percentage.** `research_top_n` (default 3) bounds spend per day
   regardless of how many candidates surface. Absolute, not "top X%."
3. **A wall-clock budget for the whole phase, not just per-call.** Each brief already has a
   600s call timeout (`config.yaml: research.timeout_s`), but `N` hung calls still serialize.
   `research_phase_budget_s` (config-defined 2000; code fallback 600) caps the entire research
   phase; once exceeded, remaining briefs are skipped and noted
   in the report — a single hang can't stall the daily run.
4. **A runtime kill-switch.** `SCOUT_NO_RESEARCH=1` / a `scout/STOP_RESEARCH` sentinel skips
   step 7 entirely, flippable on the running box without a redeploy. The failure-alert timer
   (§6) trips on a non-zero exit; the `RunManifest` (§3 step 8) records graceful degradations
   that exit 0, so a quietly-thin run is still debuggable.

Briefs remain cached by filing accession (existing behavior) — a name re-surfacing soon
won't re-burn tokens on the same filing.

---

## 6. Deployment on `oracle-prod`

Model the scout exactly on the existing oracle daily-report pattern (see the VPS `CLAUDE.md`):

| Unit | Mirrors | Purpose |
|---|---|---|
| `deploy/shortlist-scout.service` | `oracle-daily-report.service` | One-shot: run `shortlist-scout`, exit. Runs as a service user, cwd = repo (so `.env` is found per the secrets house rule). |
| `deploy/shortlist-scout.timer` | `oracle-daily-report.timer` | Fires once daily, off-hours (`22:30 UTC`, after US close; `Persistent=true` reruns a missed timer). |

The two unit files ship in `deploy/` (with `deploy/README.md` for install). The failure alert
is **not** a third shipped unit: the `.service` carries a commented `OnFailure=` line the
operator points at their existing `oracle-alert-failure@%n.service` (or any alert unit). Because
a configured-but-failed Telegram send now exits non-zero (§3 step 0), that `OnFailure` hook is
what surfaces a delivery failure. Units are documented, not silently hand-installed; secrets
stay in the repo-root `.env` (gitignored), loaded by `env.py`.

---

## 7. Coverage honesty for discovery

The screener already annotates each `ScoreCard` with a `coverage` block (which provider was
`ok` / `gated_402` / `empty` / `error`). The scout extends the **same principle to the
discovery layer**: the report header carries a **signal-coverage line** —

```
Signals: yahoo_screener ✓ (42 hits) · edgar_form4 ✓ (6) · finnhub_news ✓ (11)
         · wikipedia ✗ (rate-limited) · quiver — (disabled)
Funnel:  53 raw → 38 after dedup → 21 after cooldown/cap floor → 15 screened (3 dropped: budget)
```

So a thin day reads as *"Wikipedia was rate-limited and the gainers screen was quiet,"* not
an unexplained short list. This is the discovery-side analogue of the screener's existing
`Coverage notes`, and it is a hard requirement, not a nicety.

---

## 8. Testing

Follows the existing provider/source test pattern (recorded fixtures, no live calls in CI):

- **Each `SignalSource`** parses a recorded fixture into the expected `Emission`s, and
  degrades to `[]` + `available() == False` on an error/`429` fixture.
- **`edgar_index`**: parses a recorded daily-index + sample Form 4 docs → cluster buys;
  respects the per-day fetch cap; CIK→ticker mapping and P/S classification unit-tested.
- **`calendar`**: weekend/holiday → resolves to the prior session; idempotency guard makes a
  same-session re-run a no-op (`run_completed` honored).
- **`funnel`**: dedup, discovery-vs-booster aggregation (a booster alone can't create a
  candidate), cooldown skip, market-cap/held-list filter.
- **`budget`**: selects the right top-`X`, reports the dropped count, never exceeds the cap.
- **`report`**: snapshot of the rendered message incl. the signal-coverage line; `RunManifest`
  serializes the funnel counts + signal availability + research outcome.
- **A live, network-gated smoke test** (skipped in CI) asserts the Yahoo screener endpoint
  still returns non-empty `quotes` with the browser UA — early warning if Yahoo breaks it.
- **`MockSignal`** drives an end-to-end `--demo` test through the existing mock provider, so
  the whole loop is exercised offline with zero keys.

House rules carried through (from `CLAUDE.md`): every error string that may carry a request
URL routes through `env.py:redact_secrets()`; a missing/erroring signal **lowers and
annotates** coverage rather than silently shrinking the funnel; insider extraction edits go
in `providers/_form4.py` only.

---

## 9. Scope boundaries (YAGNI)

**In scope:** the `scout/` package (`signals`, `edgar_index`, `models`, `funnel`, `budget`,
`state`, `calendar`, `report`, `notify`, `daily`), two live discovery signals (Yahoo
screeners, EDGAR Form 4 daily index) + two confluence boosters (Finnhub news, Wikipedia) +
one stub (Quiver), the `config.yaml` `scout:` block, the idempotent state ledger, the
trading-calendar gate, the `RunManifest` artifact, Telegram delivery, the systemd units,
`--demo`, and tests.

**Explicitly out of scope** (tracked, not built now):
- No paid tiers, no full-universe walk — those contradict "strictly free." The honest ~10/day
  cap stays until the scale-path switch (§4.1) is taken.
- No new *scoring* — growth/value/momentum/insider stay in `scoring.py`. The scout only
  *feeds* the scorer.
- ~~Porting the screener's per-ticker `coverage` block onto the harness path~~ — **done on
  `main`** (harness coverage parity); the scout now surfaces `coverage.note` per name (§3 step 5).
- **EDGAR XBRL keyless fundamentals** (`DATA_SOURCES.md` A1) — partially landed on `main` (the
  EDGAR-financials value-axis work recovers `fcf_yield`/`pe_vs_history` when FMP gates a symbol);
  full keyless fundamentals remain the prerequisite for dropping FMP from the deep-screen chain
  and truly unbinding throughput (§4.1).
- **Up-front Claude auth-validity probe** (a `--max-turns 1` no-op before the research batch)
  to catch an expired OAuth token early — deferred in favor of the shipped per-name graceful
  skip (§5), to avoid an extra `claude` call every run.
- No backtest of *signal→forward-return* quality yet. The score backtest harness now exists on
  `main` (`shortlist-backtest`, `ASSESSMENT_GAPS.md` §2.1), but it validates the *scoring*
  weights, not the scout's *signal* weights — those still ship as a defensible prior.
- GDELT, FINRA short-interest, and Quiver activation are post-MVP signal additions, each a
  one-file change against the `SignalSource` interface.

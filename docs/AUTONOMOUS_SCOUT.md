# Autonomous scout — signal-driven candidate discovery

**Audience:** whoever builds the autonomous discovery loop that turns this tool from a
"give it tickers" screener into a "tell me what to look at today" system.
**Companion docs:** [`DATA_SOURCES.md`](DATA_SOURCES.md) (the free feeds the signals draw
on), [`ASSESSMENT_GAPS.md`](ASSESSMENT_GAPS.md) (the scorer the candidates flow into),
and the repo `CLAUDE.md` (the two-stack architecture and house rules this extends).

> **Status:** design / not yet built. This is the design record approved 2026-06-01.

---

## 1. The goal and the honest framing

Today both stacks (`shortlist` screener, `shortlist-harness`) **require** a ticker list —
discovery is a human job. The goal is to close that loop: **autonomously surface candidate
tickers each day, screen and rank them, and deliver a report of the top names** — with the
expensive Claude 10-K research run on the leaders so the report arrives decision-ready.

The four decisions that shape this design (all settled):

| Decision | Choice | Consequence |
|---|---|---|
| **Discovery** | **Signal-driven** (no fixed universe) | Candidates come from what names *just did something*, not a list to walk. |
| **Budget** | **Strictly free forever** | No paid FMP tier assumed. Daily throughput is capped (~15 deep screens/day). |
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

## 2. Architecture — a third stack that orchestrates, not replaces

The repo already has **two parallel stacks** (screener `shortlist.*`, harness
`shortlist.data.*`). The scout is a **third, thin layer** that *orchestrates* them. It pulls
candidates, hands each to the **existing** harness screen + scorer, runs the **existing**
research layer on the leaders, and ships a report. It introduces no new scoring and no new
fundamentals fetching — only **discovery** and **delivery**.

```
   free signal feeds                 EXISTING stacks (unchanged)            delivery
 ┌───────────────────┐   Candidate  ┌──────────────────────────┐  ScoreCard ┌──────────┐
 │ YahooScreener     │─┐  objects   │ harness collector +      │   + brief  │ Telegram │
 │ EdgarForm4        │ │   ┌──────┐ │ scoring + coverage       │  ┌───────┐ │  message │
 │ FinnhubNews       │ ├──▶│funnel│▶│ (data.collector,         │─▶│report │▶│  + JSON  │
 │ WikipediaAttention│ │   │+budget│ │  scoring, coverage)      │  └───────┘ │ artifact │
 │ (Quiver — stub)   │─┘   └──────┘ │ research (Claude CLI)     │            └──────────┘
 └───────────────────┘             └──────────────────────────┘
```

### New package: `shortlist/scout/`

Mirrors the existing registry pattern (a `_REGISTRY` of named sources, like
`providers/__init__.py` and `data/sources.py`).

| Module | Responsibility | Depends on |
|---|---|---|
| `signals.py` | Registry of free `SignalSource`s. Each `.scan()` emits `Emission(ticker, signal, strength, evidence)` for names that tripped its rule today. `MockSignal` powers offline `--demo`. | `httpx`, `env.py`, `providers/_form4.py` |
| `models.py` | `Emission`, `Candidate` (one ticker + the set of signals that flagged it + composite `interest` + evidence), `ScoutReport`. | stdlib `dataclasses` |
| `funnel.py` | Aggregate emissions → `Candidate` per ticker. Prefilter: cooldown (skip names screened within N days), liquidity / market-cap floor (one cheap quote), dedup, drop names on a held/ignore list. | `models`, `store` |
| `budget.py` | The honest cap. Knows free ceilings (~13 FMP calls/ticker ⇒ ~15 deep screens/day; Finnhub 60/min) and selects the top-`X` candidates by `interest` that fit today's budget. Logs what it dropped. | `config.yaml` |
| `report.py` | Render `ScoutReport` → a Telegram-friendly message **and** a JSON/Markdown artifact under `scout/` (gitignored). Includes the signal-coverage line. | `models` |
| `notify.py` | Deliver the message to Telegram (reuses the existing bot token/config). Thin; swappable. | `env.py` |
| `daily.py` | The loop orchestrator (§3). CLI entry `shortlist-scout`. | all of the above + existing stacks |

State (cooldown / "already screened" / held list) reuses the harness `data/store.py`
conventions — a small JSON/SQLite file under a gitignored state dir, not a new store engine.

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
    def scan(self) -> list[Emission]: ...   # today's hits; [] on a clean/erroring run
    def available(self) -> tuple[bool, str]: ...  # (ran?, why-not) for coverage honesty
```

Same shape as `Provider`/`Source`: a name, a registry entry, graceful degradation, and an
audit of whether it ran. Errors route through `env.py:redact_secrets()` before logging.

---

## 3. The daily loop (`daily.py`)

1. **Scan.** Each enabled `SignalSource.scan()` runs (free APIs only), emitting candidates
   for names that tripped its rule today. A source that errors/rate-limits returns `[]` and
   reports `available() == (False, reason)`.
2. **Funnel.** Aggregate emissions → one `Candidate` per ticker, carrying the *set* of
   signals that flagged it. A name flagged by **three** signals outranks one flagged by a
   single noisy gainer screen — confluence is the core ranking idea.
3. **Prefilter.** Drop names screened within the cooldown window, below the market-cap /
   liquidity floor, on the held/ignore list, or duplicates.
4. **Budget.** Select the top-`X` candidates by `interest` that fit today's free-tier
   ceiling. `log()` the count dropped for budget so truncation is never silent.
5. **Deep-screen.** Hand each selected ticker to the **existing harness** (`data.collector`
   → `scoring.score` → `coverage`) → `ScoreCard`. No new scoring code.
6. **Rank + gate.** Order by composite; surface gates (FCF / leverage / insider) inline.
7. **Auto-research.** Run the **existing** Claude-CLI research layer on the top-`N` non-gated
   names (`N` ≪ `X`; hard-capped; kill-switch — see §5).
8. **Report.** `report.py` assembles ranked shortlist + briefs + the signal-coverage line →
   `notify.py` to Telegram + a dated artifact under `scout/`.
9. **Persist.** Record screened tickers + date for the cooldown, so tomorrow's signal-driven
   run doesn't re-screen the same names.

`--demo` runs the whole loop offline with `MockSignal` + the existing mock provider and
prints the report to stdout instead of Telegram — the same offline-first ergonomics the
screener already has.

---

## 4. The free signal set

All keyless or already-keyed; all documented (with validated pulls) in `DATA_SOURCES.md`.
The scout **consumes** these as discovery triggers; several are also wishlisted there as
*scoring* inputs — the scout reuses the same pullers where they overlap.

| Source | Signal it fires on | Access | Notes |
|---|---|---|---|
| **`YahooScreenerSignal`** | Yahoo *predefined* screeners: `day_gainers`, `day_losers`, `most_actives`, `undervalued_growth_stocks`, `aggressive_small_caps`, etc. | keyless `query1.finance.yahoo.com/v1/finance/screener/predefined/saved` | The workhorse. Unofficial endpoint → polite backoff, day-cache, graceful degrade (same posture as the existing `YahooSource`). |
| **`EdgarForm4Signal`** | Same-day **insider cluster buys** from the EDGAR Form 4 daily index. | free SEC EDGAR (`set_identity` required) | The **highest-signal free source** — open-market insider buying is a researched alpha signal. Reuses `providers/_form4.py` aggregation; respects the shared EDGAR concurrency semaphore. |
| **`FinnhubNewsSignal`** | News-volume spikes / sentiment on already-covered names (`company-news`, `news`). | free Finnhub key (already configured) | Free-tier endpoints (validated in `DATA_SOURCES.md` B2). Confirmation, not a driver. |
| **`WikipediaAttentionSignal`** | Daily **pageview spikes** for a curated ticker→article map. | keyless Wikimedia REST (descriptive User-Agent) | Attention proxy (`DATA_SOURCES.md` A4). Fuzzy name→ticker mapping ⇒ curated map, low weight. |
| **`QuiverSignal`** *(stub)* | Congressional trades / gov-contract awards. | needs a key | Left as a registered, unwired stub (like `providers/extensions.py`) — activates if a free key appears. Honors "strictly free": **off by default**. |

Adding a sixth signal is a one-file change: implement `SignalSource`, register it, give it a
`config.yaml` weight. GDELT news tone (`DATA_SOURCES.md` A5) and FINRA short-interest jumps
(C1) are the natural next two.

### Config (in `config.yaml`, new `scout:` block)

```yaml
scout:
  daily_x: 15                  # max deep screens/day (free-tier bound; honest cap)
  research_top_n: 3            # Claude briefs run on the top-N non-gated names
  cooldown_days: 7             # don't re-screen a name within this window
  min_market_cap: 2.0e+9       # reuse the screener's floor for the prefilter
  signals:                     # per-source enable + interest weight
    yahoo_screener:  {enabled: true,  weight: 1.0}
    edgar_form4:     {enabled: true,  weight: 1.5}   # highest-signal free source
    finnhub_news:    {enabled: true,  weight: 0.5}
    wikipedia:       {enabled: true,  weight: 0.5}
    quiver:          {enabled: false, weight: 1.0}   # strictly-free: off until keyed
  research_kill_switch: false  # set true to skip the Claude step on a bad day
```

`interest` for a candidate = Σ over the signals that flagged it of `strength × weight`.
Confluence (multiple signals) and source quality (the weight) both lift a name.

---

## 5. Fully-autonomous research — the guardrails

"Fully autonomous incl. research" means step 7 runs **unattended on the VPS**. The existing
research layer already uses the **`claude` CLI in headless mode** (no API key; uses the
user's CLI auth) with a strict lockdown (`--tools "" --strict-mcp-config --max-turns 1`).
Running it unattended adds three requirements:

1. **CLI auth must be present on `oracle-prod`.** The headless `claude` call needs the user's
   CLI logged in on the VPS. The loop checks for it up front and, if absent, **degrades to
   screen-only and says so in the report** rather than failing the run.
2. **A hard cap, not a percentage.** `research_top_n` (default 3) bounds token/time spend per
   day regardless of how many candidates surface. The cap is absolute, not "top X%."
3. **A kill-switch.** `research_kill_switch: true` (or a `--no-research` flag) skips step 7
   entirely. The failure-alert timer (below) trips if a run errors, so a runaway day is
   visible and stoppable.

Briefs remain cached by filing accession (existing behavior) — a name re-surfacing soon
won't re-burn tokens on the same filing.

---

## 6. Deployment on `oracle-prod`

Model the scout exactly on the existing oracle daily-report pattern (see the VPS `CLAUDE.md`):

| Unit | Mirrors | Purpose |
|---|---|---|
| `shortlist-scout.service` | `oracle-daily-report.service` | One-shot: run `shortlist-scout`, exit. Runs as a service user, cwd = repo (so `.env` is found per the secrets house rule). |
| `shortlist-scout.timer` | `oracle-daily-report.timer` | Fires once daily, off-hours (after US close, before the next open). |
| `shortlist-alert-failure@…` | `oracle-alert-failure@…` | Telegram alert if the run fails — the visible kill-switch trigger. |

Unit files live in the repo (a `deploy/` dir) and are documented, not silently hand-installed
on the box. Secrets stay in the repo-root `.env` (gitignored), loaded by `env.py`.

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
- **`funnel`**: dedup, multi-signal aggregation, cooldown skip, market-cap/held-list filter.
- **`budget`**: selects the right top-`X`, reports the dropped count, never exceeds the cap.
- **`report`**: snapshot of the rendered message incl. the signal-coverage line.
- **`MockSignal`** drives an end-to-end `--demo` test through the existing mock provider, so
  the whole loop is exercised offline with zero keys.

House rules carried through (from `CLAUDE.md`): every error string that may carry a request
URL routes through `env.py:redact_secrets()`; a missing/erroring signal **lowers and
annotates** coverage rather than silently shrinking the funnel; insider extraction edits go
in `providers/_form4.py` only.

---

## 9. Scope boundaries (YAGNI)

**In scope:** the five-module `scout/` package, the four live free signals + one stub, the
`config.yaml` `scout:` block, Telegram delivery, the systemd units, `--demo`, and tests.

**Explicitly out of scope** (tracked, not built now):
- No paid tiers, no full-universe walk — those contradict "strictly free." The honest ~15/day
  cap stays until a budget decision changes.
- No new *scoring* — growth/value/momentum/insider stay in `scoring.py`. The scout only
  *feeds* the scorer.
- No backtest of signal→forward-return quality yet (it belongs with the `ASSESSMENT_GAPS.md`
  §2.1 backtest harness; signal weights ship as a defensible prior, like the scoring weights).
- GDELT, FINRA short-interest, and Quiver activation are post-MVP signal additions, each a
  one-file change against the `SignalSource` interface.
```

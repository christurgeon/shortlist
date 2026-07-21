# Position Monitor — design (v1)

**Status:** spec, not yet implemented. Date: 2026-07-21.

Sell-side counterpart to the discovery funnel. Where Scout answers *"what should I look
at?"*, this answers *"which of the things I already own needs my attention this week?"*

See `AUTONOMOUS_SCOUT.md` (discovery), `NOTIFICATIONS.md` (delivery), and the repo
`CLAUDE.md` (design premise) for the surrounding system.

---

## 1. Premise — why the sell side is worth building

This is the same triage-funnel doctrine as the rest of the repo, pointed at holdings. It
is **not** a return-predicting exit model and must not become one.

The evidence that motivates it is specific. Akepanidtaworn, Di Mascio, Imas & Schmidt
(2021), *"Selling Fast and Buying Slow: Heuristics and Trading Performance of
Institutional Investors"* — ~780 institutional PMs, ~4.4M trades:

- Their **buys beat** a random-buy-from-their-own-portfolio benchmark by **~1.2%/yr**.
- Their **sells lost ~0.8%/yr** versus randomly selling a held name.
- Same managers, same book, same day.

The mechanism the authors identify is **attention asymmetry**, not lack of skill: buys get
a research process, sells get a salience heuristic (names that recently moved a lot in
either direction). The diagnostic detail — sell performance recovered when the trade was
driven by an **earnings announcement** — is what this design is built around: when a
scheduled event forces structured attention, the skill comes back.

So the product thesis is:

> **The sell decision is where a systematic process beats a discretionary one by the
> widest margin — because it is where humans allocate the least attention.** The goal is
> not alpha. It is to stop running on salience.

Two consequences that constrain everything below:

1. **No stance is ever emitted.** The system routes attention and presents deltas. It
   never says sell, and never scores an exit.
2. **Silence is the primary feature.** A monitor that pings weekly gets muted, and a muted
   monitor is worse than none — it manufactures false confidence that something is
   watching. Every design choice below is subordinate to keeping the alert rate low.

## 2. Non-goals — the anti-paranoia contract

The following are **deliberately not alerts**, and this list is normative. They may appear
inside a scheduled re-underwrite as context; none of them may ever interrupt the user:

- composite score drift
- momentum decay, price vs. 200dma, 52-week-high proximity
- valuation richening
- `value_trap`, `crowded_short`, `news_spike`, `social_hype`, `risk_off_regime` flags
- macro regime changes
- anything derived from a continuous score crossing a threshold

Rationale: all are either noise at a 30–90d horizon, already-priced, or not
thesis-relevant. The repo's design premise applies unchanged — a single-universe `t≈2` is
noise, and an alerting surface is *more* sensitive to false positives than a scoring leg,
because the cost is paid in user attention rather than in a backtest number.

Also out of scope for v1: auto-sell, stop-losses as execution, tax-lot optimization,
account-type modeling, position sizing recommendations, opportunity-cost ranking.

## 3. Position store

### 3.1 File and ownership

`positions.json` — gitignored, path from `portfolio.store` in `config.yaml`. Written
atomically (write temp in the same directory, `os.replace`). A new pure leaf
`src/shortlist/positions.py` owns all reads and writes, following the
`portfolio.py` / `_form4.py` shared-leaf pattern: stdlib only, no network, no optional
deps, safe to import on the always-on bot path.

Two processes touch this file: the interactive bot (on command) and the daily scout timer
(reads at 22:30 UTC, writes only `last_reunderwrite` / `alerted`). Both always read fresh
before writing; the file is small and the write windows are disjoint in practice.
Last-writer-wins is acceptable and no locking is introduced.

### 3.2 Schema

**Lots are the only stored truth within a record.** Everything else is derived, so there
is no denormalized field that can drift out of sync:

```json
{
  "version": 1,
  "positions": {
    "MSFT": {
      "lots": [
        {"date": "2026-03-14", "shares": 12, "price": 402.15},
        {"date": "2026-06-02", "shares": 5,  "price": 455.00}
      ],
      "thesis": "Azure AI capex converts to operating margin by FY27",
      "entry_card": {"composite": 71.2, "quality": 78, "moat": 74, "growth": 66,
                     "value": 41, "momentum": 58, "insider": 60, "risk": 55,
                     "gates": [], "flags": [], "as_of": "2026-03-14"},
      "last_reunderwrite": "2026-06-20",
      "alerted": {"drawdown_25": "2026-05-02", "gate:over_leveraged": "2026-06-11"}
    }
  },
  "closed": [
    {"ticker": "XYZ", "opened": "2025-11-03", "closed": "2026-07-01",
     "shares": 40, "avg_cost": 22.10, "exit_price": 31.40, "reason": "thesis played out"}
  ]
}
```

Derived, never stored: `shares = Σ lots.shares`, `opened = min(lots.date)`,
`avg_cost = Σ(shares×price) / Σ shares`.

Lots exist for three reasons, each load-bearing: correct weighted-average cost when
averaging in; correct **per-tranche holding duration** (§6); and FIFO reduction on `/trim`
without losing entry history.

`entry_card` is the delta baseline for re-underwrites (§5.2). It is captured because
`/add` screens the name anyway (§4), so it costs nothing extra and removes any dependency
on the accumulation store having been running at entry time.

`alerted` is the fire-once ledger (§5.1), capped at 32 entries per position, oldest
dropped.

### 3.3 Forward compatibility and migration

Unknown top-level keys and unknown per-position keys are preserved on rewrite (the
`ScoutState` convention). A missing `version` is treated as `1`.

**Migration from `portfolio.csv`:** on first read, if `positions.json` is absent and a
`portfolio.csv` exists, import each row as a single lot with `date: null, price: null`,
write the store, and emit a one-time warning naming the imported tickers. A `null` lot
date means return-since-entry and holding-duration **abstain** (never fabricated). The
file is documented as hand-editable so historical dates can be backfilled once.

`portfolio.csv` is read exactly once, at migration. After that `positions.json` is the
sole source of truth — there is never a period with two live sources.

## 4. Telegram commands

Grammar is strictly positional and unambiguous: `ticker`, `shares`, optional `@price`,
remainder is free prose. No conversational state; every command is a single message,
consistent with the existing single-worker handler model.

| Command | Behavior |
|---|---|
| `/add MSFT 12 @402.15 Azure margin thesis` | `@price` optional (defaults to the live screened price), thesis optional. Lot date = today. **Screens the name immediately and replies with the card** — instant feedback, and that card is stored as `entry_card`. An existing ticker **appends a lot** (averaging in); `opened` and `entry_card` are unchanged. |
| `/trim MSFT 5 [reason]` | Partial reduction, FIFO against lots. Preserves entry-date history — the reason `/remove` + `/add` is not an acceptable substitute. Appends a `closed[]` record for the reduced portion. |
| `/remove MSFT [reason]` | Closes the position fully. Alias `/sold`. |
| `/positions` | One line per holding: weight · return vs SPY · days held · holding-duration note · `⚠ no thesis`. |
| `/thesis MSFT <text>` | Set or replace the thesis. |
| `/portfolio` | **Unchanged** dashboard (`_Portfolio` report section), rewired to read the new store. |

**`/remove` is non-destructive** — it moves the position into `closed[]`, retaining
everything. This is why no confirmation prompt is required despite Telegram fat-finger
risk, and it is also how the sell ledger (§7) builds itself at zero marginal cost. The
reply states this explicitly.

**Thesis stays optional.** Demanding it at `/add` would suppress adoption; instead every
re-underwrite and every `/positions` line nags when it is missing. The nag is the forcing
function, not the gate.

Command help text is added to the existing `/help` block in `bot.py`. Per repo
convention, any new user-facing term added here must also land in `scout/glossary.py`
(the AST-scan test binds emitted literals to glossary entries).

## 5. Cadence

The monitor runs inside the **existing** daily scout run (22:30 UTC), after the negative-8-K
veto sweep has already populated its map. It adds no new schedule and no new service.

**FMP quota is the binding constraint, and holdings must not consume it.** The harness
makes ~13 FMP calls/ticker against a 250/day free limit (≈19 tickers/day total), and
discovery already spends up to `scout.daily_x` = 10. Screening 10–15 holdings daily on the
full chain would blow the budget outright and starve discovery.

Therefore the monitor screens holdings on the **free-source chain only**
(`digest_sources(base, include_fmp=False)` — the identical rationing the daily digest
already uses, `scout.daily_push.include_fmp: false`). This costs only `peg` and
`upside_to_target`; all seven axes still score, and critically **every Tier-1 breach source
survives**: the hard gates derive from EDGAR statements and Finnhub-backfilled
`market_cap`, the 8-K breach comes from the already-swept EFTS map, and the drawdown band
comes from keyless Yahoo closes. EDGAR load is bounded by the existing
`_EDGAR_MAX_CONCURRENCY` semaphore, and Finnhub's 60/min is comfortable at this volume.

A `monitor.include_fmp` knob (default `false`) flips this on a paid FMP plan. The
interactive `/portfolio`, `/screen`, and `/deep` commands are unaffected and always keep
the full chain — the rationing applies only to the unattended daily sweep.

It is **failure-isolated** in the manner of `_record_session_picks`: any exception inside
the monitor is caught, noted on the manifest, and never crashes an already-delivered
discovery run.

### 5.1 Tier 1 — thesis breach (rare, event-keyed, fire-once)

Only discrete, dated, unambiguous events qualify. All four sources are already computed:

| Breach | Source | Marginal cost |
|---|---|---|
| Negative 8-K item filed (`1.03, 2.04, 2.05, 2.06, 3.01, 4.02, 5.01`) | the existing `veto_map` from `daily.py:_negative_veto_sweep` | **zero** — already swept daily, keyed by ticker |
| A hard gate newly trips (`negative_fcf`, `over_leveraged`, `heavy_insider_selling`, `below_min_mktcap`) | `card.gates` vs `entry_card.gates` | free (holdings are screened anyway) |
| `dilution` flag onset | `card.flags` vs `entry_card.flags` | free |
| Price crosses −25% or −40% vs entry | entry date + one fresh adjusted series | free |

The 8-K reuse is the design's best leverage: that map already exists to *drop* discovery
candidates pre-screen. For holdings the identical data *alerts* instead — the same signal
applied to the opposite side of the funnel, and it is the one breach source with
independent measured evidence in this repo (`docs/audits/2026-07-08-…`, veto confirmed
directionally at −5.8%/mo).

**Fire-once discipline.** Every breach carries a stable key (`8k:<accession>`,
`gate:<name>`, `flag:dilution`, `drawdown_25`, `drawdown_40`) recorded in `alerted` with
its date. A key that has fired **never fires again**. This single rule is what keeps a
persistently-tripped gate from pinging daily, and it is the difference between a monitor
that stays unmuted and one that does not.

A gate that clears and later re-trips is a genuine new event: clearing removes the key, so
the re-trip fires once more.

**Delivery:** one standalone Telegram message per breach, sent from the daily run. Format:

```
⚠ MSFT — thesis breach
8-K item 4.02 (non-reliance on previously issued financials) filed 2026-07-19
Held 4.2mo · 6.1% of book · +12.4% vs SPY +3.1% since entry
Your thesis: "Azure AI capex converts to operating margin by FY27"
→ Re-underwrite. /deep MSFT
```

Expected rate: **~1–2 per month** across a 10–15 name book.

### 5.2 Tier 2 — post-earnings re-underwrite

**Trigger:** `Events.last_report_filed > last_reunderwrite`. This reuses the bridge's
existing exact-form 10-Q/10-K filed date (already derived as the SUE decay anchor), which
is a ~0–5d proxy for the announcement. **Backstop:** if `last_reunderwrite` is more than
`stale_days` (default 100) old, the name is due regardless — so foreign issuers, recent
spin-offs, and any name with missing earnings data can never go dark.

This is the piece with direct evidence behind it: earnings announcements are the one
context in which the studied PMs' sell decisions were *not* worse than random.

**No artificial scheduler is needed.** Earnings dates are already staggered across the
calendar, so the re-underwrite queue self-distributes. Earnings-season clustering is
handled by a `max_per_day` cap (default 2) with unprocessed names **carrying over to later
sessions rather than being dropped** — the established `13F max_filings_per_day` precedent.

**Contents** (screen + delta only; see below on why no research brief):

- **Your thesis, verbatim.** A falsifier cannot be tested if it is not shown.
- Position facts: weight, return since entry vs SPY, days held, holding-duration note (§6).
- **Delta vs `entry_card`:** composite and per-axis then→now, gates and flags added or
  cleared since entry.
- The current screen card.
- The framing question, which is the actual mechanism of a PM re-underwrite:

  > **"Would you buy this today, at this price, with no position?"**

- A copy-paste `/deep MSFT` line.

**The Claude research brief is deliberately not included.** It ships as the `/deep` line
instead, exactly mirroring the daily digest's existing `/deep` block pattern. Rationale:
it keeps the Claude CLI and FMP quota entirely out of the daily timer's failure surface,
costs one extra tap only when the delta actually looks interesting, and keeps the
re-underwrite message fast and fully deterministic. The long-term framing lives in the
thesis, the delta, and the framing question — not in the brief.

Expected rate: **~1 per week** for a 10–15 name book.

### 5.3 Summary of what the user experiences

- Most days: **nothing**.
- ~1–2×/month: a breach ping.
- ~1×/week: one re-underwrite.
- On demand: `/positions`, `/portfolio`, `/deep`.

## 6. Holding-duration notes (account-type agnostic)

The system **models no account type and asserts no tax consequence.** It reports the
neutral duration fact and lets the user apply their own account's rules:

```
held 11.1mo (12mo on 2026-09-02, in 26 days)
```

Computed **per lot**, since averaging in creates tranches with independent clocks; a
position with lots at 14mo and 3mo reports both rather than a misleading blended figure.
Surfaced in `/positions`, in re-underwrites, and in a breach message when a lot is within
`duration_notice_days` (default 45) of the 12-month mark.

Lots with a `null` date (CSV migration) abstain rather than guess.

This is pure date arithmetic on data already held — no new field, feed, or dependency. The
12-month reference point is a widely-relevant boundary across many account types; the
framing is a **fact**, never a recommendation, which is what keeps it account-agnostic.

## 7. Split-safety and the sell ledger

### 7.1 Split-safety

Stored per-share cost basis **will be wrong after a split**. This repo already carries that
scar: `picks.py:pick_performance` is documented split-safe precisely because an earlier
approach divided a fresh adjusted close by a stored scalar.

The rule, inherited unchanged:

- **The headline return is always computed from the entry _date_**, with both endpoints
  taken from **one fresh adjusted series** (reuse `pick_performance`, or a shared helper
  extracted from it). Never `fresh_price ÷ stored_cost`.
- **Stored cost basis is labeled "as entered" and used for display only** — never for the
  return, never for the drawdown bands.

This removes the entire failure class at zero implementation cost, and requires no split
detection.

### 7.2 Sell ledger

`closed[]` records every exit. In v1 this is **record-only** — nothing reads it.

Its purpose is to make the Akepanidtaworn counterfactual measurable on the user's *own*
trades: did selling this name beat continuing to hold it, benchmark-relative, at fixed
horizons? That measurement is deferred, but the data cannot be reconstructed after the
fact, so recording begins on day one.

This is the repo's "measure-first, let evidence accumulate over calendar time" doctrine
(the selection-ledger precedent) pointed at the user's decisions rather than at a signal.

## 8. Wiring

- **`state.set_held` is fed from positions.** `funnel.py:32` already drops held tickers
  from discovery via `is_held`, but nothing currently populates `set_held`. The daily run
  sets it from the position store, so Scout stops re-surfacing names already owned. Small
  fix, real effect.
- **`daily.py:run`** gains one failure-isolated monitor phase after the veto sweep and
  screening: load positions → screen holdings → compute breaches + due re-underwrites →
  deliver → persist `alerted` / `last_reunderwrite`.
- **`bot.py`** gains the five new handlers; `_do_portfolio` is rewired to the new store.
- **`portfolio.py`** is unchanged except that `load_holdings` is superseded by
  `positions.py` as the input path; `summarize` and the `_Portfolio` report section are
  untouched.

## 9. Config

Extends the existing `portfolio:` block:

```yaml
portfolio:
  store: positions.json    # source of truth (gitignored); atomic writes
  path: portfolio.csv      # legacy CSV — read ONCE at migration, then ignored
  max_holdings: 50         # safety cap; over this, screen the cap + WARN (never silent-drop)
  monitor:
    enabled: true
    include_fmp: false     # holdings sweep rations FMP -> free sources (§5). All 7 axes
                           # still score and every breach source survives; loses only peg +
                           # upside_to_target. Flip true on a paid FMP plan.
    breach:
      negative_8k: true
      gates: true          # newly-tripped hard gates
      dilution: true
      drawdown_bands: [0.25, 0.40]   # fire-once per band, vs entry-date-anchored return
    reunderwrite:
      max_per_day: 2       # overflow CARRIES OVER, never dropped
      stale_days: 100      # forced re-underwrite backstop
    duration_notice_days: 45
```

Removing the `monitor:` block disables the feature entirely with no other behavior change.

## 10. Robustness and failure modes

| Failure | Behavior |
|---|---|
| `positions.json` missing / unreadable / corrupt | Treated as empty, loud warning, never raises. The bot stays up. |
| Monitor phase raises | Caught; manifest note; the discovery run still delivers. |
| Holding screens with no data (delisting, M&A, typo) | Surfaced as an alert (the existing `no_data` predicate), contributes no exposure — a typo cannot hide silently. |
| Veto sweep stale/failed | Already degrades loudly with a stale-state note; breaches inherit that note rather than silently under-alerting. |
| Lot with `null` date | Return and duration abstain; all other features work. |
| Holdings exceed `max_holdings` | Screen the cap, name the dropped tickers explicitly (existing `_do_portfolio` behavior, preserved). |
| Concurrent bot/timer write | Atomic replace; read-fresh-before-write; last-writer-wins accepted and documented. |

## 11. Testing

- **`positions.py` pure-leaf tests:** lot math (add / average-in / FIFO trim / close),
  derived fields, `null`-date abstention, unknown-key preservation, corrupt-file
  tolerance, atomic write, CSV migration (including the once-only guarantee).
- **Command parsing:** the `/add` grammar across all optional-token combinations, malformed
  input, unknown ticker.
- **Fire-once ledger:** a persistent gate fires exactly once across N consecutive sessions;
  clear-then-re-trip fires again; cap eviction.
- **Cadence:** re-underwrite triggers on a new filed date, respects `max_per_day`, carries
  overflow to the next session, and the `stale_days` backstop fires.
- **Anti-paranoia contract:** an explicit test asserting that the §2 non-goal list produces
  **no** breach, so a future change cannot quietly widen the alert surface.
- **Split-safety:** a regression test asserting the return is date-anchored and never
  derived from stored cost basis.
- **FMP rationing:** with `include_fmp: false` the daily holdings sweep resolves to the
  free-source chain, and every Tier-1 breach source still evaluates on a
  no-FMP snapshot (the quota regression guard).
- **Disabled-block invariance:** absent `monitor:` → no monitor behavior, discovery run
  byte-identical.

## 12. Deferred to v2

Named explicitly so they do not creep into v1:

- **Sell-ledger measurement** — the counterfactual read of `closed[]` (data collection
  starts in v1).
- **Opportunity-cost ranking** — "your weakest holding vs. this week's best candidate."
  Advice-shaped; needs care.
- **Correlation / factor exposure** beyond the existing sector concentration.
- **Multi-account books** and any account-type-specific logic.
- **Activist-exit detection** (13D/A stake *decrease*) as a breach source.
- **Position sizing** guidance of any kind.

## 13. Framing

Every surface carries the repo-wide tag: **screening triage, not investment advice.** The
monitor emits no stance, no target, and no exit recommendation — it routes attention and
presents deltas against a thesis the user wrote themselves. The framing question is the
product; the answer is always the user's.

---
name: prospect
description: >
  Use when hunting for fresh stock ideas to deep-dive — surfacing undervalued or
  breakout-poised US-listed candidates (typically a weekly run), or when the user
  types /prospect or asks for new tickers to feed into /deep. For discovery and
  idea-generation, NOT for scoring an already-known ticker list (that is /run).
---

# prospect — weekly opportunity hunt

## Overview

A **web-only** discovery routine that hunts US-listed stocks that are either
**undervalued** or **poised for a breakout**, vets them against the same gate
logic the `shortlist` scorer uses, and hands back a tight **5–8 name ranked
brief plus a copy-paste `/deep` block** for the Telegram bot. It is the "what
should I even look at this week" brain; the downstream `/deep` run is the real
diligence.

**Core principle:** cast a wide, multi-lens net, then be a skeptic. Discovery is
noisy — your job is to hand over only names worth a rate-limited `/deep` run.

## When to use

- Weekly (or ad-hoc) idea generation: "find me stocks," "weekly stock hunt,"
  "fresh tickers to deep-dive," "undervalued / breakout candidates," `/prospect`.
- **NOT** for scoring tickers you already have — that's `/run` / the `shortlist`
  screener. This skill produces the *candidates*; `/run` and `/deep` judge them.

## What it needs

Only `WebSearch` + `WebFetch`. No repo checkout, no API keys, no `uv`, no VPS
load. It is designed to be copied into a Claude Code remote routine and scheduled
weekly — the scan runs in the cloud, and the only thing that ever touches your VPS
is the handful of `/deep` commands you choose to paste into Telegram afterward.

## The funnel

Run these phases in order. If your runtime can dispatch parallel sub-agents, fan
the Phase-1 lenses out (one agent per family) and merge their hits; otherwise run
them sequentially with batched searches.

**Phase 0 — Calibrate.** Confirm today's date. Do one or two searches to read the
market regime (major-index trend, rate backdrop, risk-on vs risk-off, hot vs cold
sectors). Pick **one rotating weekly theme** from what you find (a sector, a
secular trend, or a fresh dislocation) so the hunt never goes stale. Read the most
recent prior brief if one exists (see Phase 5) so you don't re-surface the same
names.

**Phase 1 — Cast the net.** Search across **both** families. The value lenses are
**mandatory** — they are the counterweight that keeps this from becoming pure
momentum-chasing.

| Family | Lenses to search |
|---|---|
| **Value** (undervalued) | 52-wk lows in *profitable* names / overreactions · cheap vs own history or peers (low EV/EBIT, high FCF yield) · insider cluster buying (Form 4) · special situations (spinoffs, restructurings, sum-of-parts) · net-cash / asset-rich small caps near book |
| **Breakout** (catalyst) | bases breaking out on rising volume · post-earnings drift (big beat + raised guidance) · catalyst-driven (new product, contract win, regulatory/FDA) · short-squeeze + *improving* fundamentals · analyst upgrade / estimate-revision clusters |
| **+ rotating theme** | the single theme chosen in Phase 0 |

For each hit, capture: ticker, the lens it came from, a one-line "why it
surfaced," the catalyst, and a source URL.

**Phase 2 — Verify & dedupe.** Confirm each ticker is real and **US-listed
(NYSE/Nasdaq), roughly ≥ $300M market cap, and not a thin foreign (20-F) ADR**
(those come back empty in `/deep`). Drop everything else. Dedupe. **Star
confluence names** — those that appear in *both* a value and a breakout lens.
Cheap *and* waking up is the highest-conviction setup there is.

**Phase 3 — Skeptic filter (mirror the `shortlist` gates).** Drop or down-rank
names the screener would gate, so you don't burn a `/deep` run on them:

| Disqualifier | Why |
|---|---|
| Burning FCF with no growth excuse | `negative_fcf` gate |
| Over-leveraged without interest coverage | `over_leveraged` gate |
| Heavy insider selling | `heavy_insider_selling` gate |
| Pure WSB / social hype, no fundamental thesis | momentum mirage |
| Recent reverse split, dilution spiral, going-concern language | data trap / value trap |

Each survivor must have **≥ 2 independent corroborating points** and a stated
**key risk** (the bear case — the thing that kills the thesis).

**Phase 4 — Rank & deliver.** Keep the top **5–8** by conviction = thesis
strength × catalyst proximity × data-coverage confidence, plus a confluence bonus.
Bucket into Undervalued vs Breakout. Emit the brief in the Output Contract below.

**Phase 5 — Freshness (best-effort).** Save the brief to
`prospect/<YYYY-MM-DD>.md`. On the next run, read the latest `prospect/*.md` and
skip names already surfaced recently unless their thesis materially changed.
Degrade silently if the filesystem doesn't persist between runs.

## Output Contract

Produce EXACTLY this shape. The `/deep` block and the per-name conviction tags are
the whole point of the handoff — never omit them.

```
# Prospect — week of <date>
**Regime:** <one line: index trend · rates · risk-on/off>
**This week's theme:** <the rotating theme>
**Lenses run:** <which families/lenses ran>  ·  **Gaps:** <any lens or source that
failed — name it; a shrunken funnel is fine, a faked one is not>

## Undervalued
### <TICKER> — <Company name> · conviction <H/M/L> <★ if confluence>
- **Thesis:** <1–2 lines — why it is cheap>
- **Catalyst:** <what re-rates it, with timing if known>
- **Key risk:** <the bear case / what would kill it>
- **Source:** <url>

## Breakout
### <TICKER> — <Company name> · conviction <H/M/L> <★ if confluence>
- **Thesis:** <1–2 lines — why it is breaking out>
- **Catalyst:** <...>
- **Key risk:** <...>
- **Source:** <url>

## Hand off to /deep
/deep <T1>, <T2>, <T3>
/deep <T4>, <T5>, <T6>
(conviction-ordered, ≤ 3 tickers per command — the bot caps /deep at 3 names;
run any single name on its own with `/deep TICKER`)

*Screening triage, not investment advice — /deep is the real diligence.*
```

## Guardrails

- **Never fabricate** tickers, prices, or numbers. Verify every symbol exists and
  is US-listed. Cite a source per name.
- **Honesty over completeness:** if a lens or a source fails, list it under
  **Gaps** — never invent names to fill a bucket.
- **Value must contribute:** if every survivor is a breakout/momentum name, you're
  chasing. Go back and work the value lenses until at least the Undervalued bucket
  has real names.
- **Stay in-universe:** US-listed, ≈ ≥ $300M, no thin foreign issuers.
- **Tight beats broad:** 5–8 high-conviction names, not a watchlist dump.

## Common mistakes

- **Punting a bucket** to "go run your own screener" instead of naming concrete
  tickers — the deliverable IS named, verified candidates.
- **Mislabeling momentum as value** — a name that already rebounded 35% is not
  "undervalued." Distinguish *still cheap* from *already ran* and down-weight the
  latter.
- **Dropping the `/deep` block or conviction tags** — that handoff is the entire
  reason this skill exists.
- **Wasting the `/deep` rate budget** (FMP free ≈ 19 tickers/day) on gate-trippers
  you could have filtered out in Phase 3.

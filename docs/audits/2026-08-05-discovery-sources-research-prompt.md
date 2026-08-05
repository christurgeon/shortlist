# Research prompt — new discovery sources for the shortlist scout

Companion to `2026-08-05-discovery-funnel-audit.md`. **Copy everything below the line into a
fresh Claude conversation on the web** (not Claude Code — the goal is breadth of ideas, and a
model without repo context will range wider). Bring the answer back here for feasibility
probing; nothing it proposes should be built without the measure-first treatment.

---

I run an automated daily stock-*discovery* pipeline and I need help finding new data sources.
I want breadth and genuinely non-obvious ideas — not another listicle of stock-market APIs.

## What the system does

It is a **triage funnel that feeds a human deep-dive**, explicitly *not* a return-predicting
alpha model. Each night it:

1. **Originates** candidate tickers from event feeds (something *happened* to this company),
2. screens them on fundamentals (quality / moat / growth / value / insider / risk),
3. ranks the survivors and pushes a short digest to me.

The thesis is that whatever retail-accessible edge still exists lives in **event-driven
origination** — noticing a company early because of a disclosure — not in a better scoring
model. Origination is what I want to improve.

## What I already use (please don't re-suggest these)

**Working:** SEC EDGAR Schedule 13D activist stakes; SEC Form 4 insider open-market buys
(classified routine-vs-opportunistic per Cohen-Malloy-Pomorski 2012); SEC 13F-HR new
positions from ~7 marquee funds; SEC EDGAR full-text search (EFTS) for 8-K item
combinations and buyback-authorization phrases; FINRA consolidated short interest;
USAspending federal contracts; Senate LDA lobbying disclosures; FRED macro; Finnhub company
news; ApeWisdom/WSB mentions.

**Retired on evidence** (please don't propose reviving without a genuinely new angle):
Reddit/WSB hype as an *originator* — it surfaced mega-cap chatter with no edge; unconditional
8-K discovery and buyback authorizations — both killed by pre-registered backtests;
congressional trading — no credible post-STOCK-Act aggregate alpha.

**Broken in my environment:** the Yahoo Finance *screener* endpoint is permanently WAF-blocked
from my datacenter IP.

## Hard constraints — a source is useless to me if it fails any of these

1. **Free and keyless, or a free tier that survives daily unattended polling.** I am already
   at the ceiling of my paid-ish quotas (one provider gives 250 calls/day total).
2. **Reachable from a datacenter IP** (Hetzner, US). Many consumer finance sites block these
   outright. Cloudflare/Akamai-protected endpoints usually fail.
3. **No browser, no JavaScript execution, no headless Chrome.** The box has 1.9 GB RAM. Plain
   HTTP + JSON/XML/CSV parsing only. HTML scraping is a last resort and fragile.
4. **Pollable on a daily schedule, unattended,** and stable enough not to need babysitting.
5. **US-listed equities.**
6. **Politely rate-limitable.** I am already straining my SEC request budget (~10 req/s
   shared), so a source needing hundreds of requests per run is a hard sell.

## Strong preferences (rank your suggestions by these)

- **Market-cap band.** My current originators badly over-select nano-caps (median ~$50M) and,
  before I killed the social signal, mega-caps. The gap — and where a small book can plausibly
  be early — is **$0.3B–$10B**. A source that naturally lands there is worth far more to me
  than one that emits more rows.
- **Historically replayable.** Before I enable anything I pre-register a hypothesis and
  backtest it point-in-time over 2022–2025. A feed with **no accessible history** can only
  ever be measured forward from today, which costs me a year. Say explicitly whether each
  source has a queryable archive.
- **No look-ahead / survivorship traps.** Flag any source that only lists *currently* listed
  companies, or whose historical view is silently restated.
- **An event with a date**, not a running aggregate — I need to know exactly when the market
  could first have known.

## What I want from you

1. **15–25 candidate sources**, ranked by expected usefulness given the constraints above.
   Prioritise things I would *not* find by searching "free stock API." Regulatory filings,
   exchange operational notices, government datasets, standards/registry bodies,
   international regulators with US-listed coverage, industry-specific disclosures, court and
   patent records, procurement systems — that kind of thing.
2. For each: **what event it captures, why that event might precede a re-rating, the concrete
   access method** (endpoint/URL shape, format, auth), **whether history is queryable**, and
   **which of my hard constraints it is most likely to violate.**
3. **Be specific about access.** "Company X has an API" is not useful; the URL shape, the
   response format, and the rate limit are.
4. **Within SEC EDGAR specifically** — I already parse it heavily, so I likely have the
   plumbing — which *other* form types carry a documented or plausible predictive event?
   Consider at least: SC 13G, Form 3, Form 144, NT 10-K/NT 10-Q, Form 25/25-NSE, S-1 and
   424B*, SC TO-T/SC 14D9, DEF 14A merger votes, 8-K items I may be ignoring, S-4, 15-12B.
   Tell me which have real published evidence behind them versus which are folklore.
5. **One standing, non-event screen.** All my originators require a filing, so a quiet filing
   day produces an empty report. I need one always-on source that ranks a broad universe on
   fundamentals or price structure, keylessly, from a datacenter IP. What are my real options?
6. **Tell me what to skip.** Which popular suggestions in this space are actually dead ends —
   deprecated, WAF-blocked, effectively paid, or unusable without a browser?

## Tone

Be skeptical and concrete. If a source's predictive claim is weak, say so — I would rather
have five sources I can actually reach and measure than twenty aspirational ones. Where you
know of academic evidence for a signal, cite it; where you are speculating, label it clearly.

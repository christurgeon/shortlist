# Position Monitor — design (v1)

**Status:** spec, not yet implemented. Date: 2026-07-21.

Tells you when something material and verifiable is **filed against a name you own**. It is
a filings watch, not a selling system.

See `AUTONOMOUS_SCOUT.md` (discovery), `NOTIFICATIONS.md` (delivery), and the repo
`CLAUDE.md` (design premise).

---

## 1. Premise — read this before adding anything

**This feature is taken on judgment. It cannot be validated on one user's book, and it is
the first thing in this repo for which that is true.** That is stated up front because an
earlier draft of this spec opened with a citation instead, and the citation did not survive
review.

### What the evidence actually supports

**One trigger, measured on this repo's own universe.** The full negative-8-K item set
{1.03, 2.04, 2.05, 2.06, 3.01, 4.02, 5.01} was backfilled and evaluated as
`edgar:8k_negative` (`TODO.md:468`):

> **INSUFFICIENT on both cohorts** (raw fraction 0.625; scored 0.883 — 1.7pp under the
> 0.90 floor, "the honest refusal"), recorded as a **labeled non-verdict observation**:
> alpha is decisively negative on both — raw −5.5%/mo CI [−6.7, −4.5]; scored −5.8%/mo
> CI [−6.7, −4.8]. The pre-registered "KILL-shaped result CONFIRMS the veto" reading is
> met **directionally**, and the ON-default veto stands with strong but formally
> unverdicted support.

This is FF3-adjusted, on-universe, at K=3m — better evidence than any outside citation.
**Three caveats that must not be dropped:** (a) INSUFFICIENT means it failed its own
robustness floor, so this is suggestive, not established; (b) the measured cohort is
micro/small-cap-skewed per `docs/audits/2026-07-08-eightk-composition-audit.md`, so the
effect on a quality-screened large/mid book is likely **materially smaller**; (c) the
non-measurable tail was no-price-series micro/OTC junk whose exclusion, if anything,
**flatters** the estimate.

The published event-study literature agrees on sign and is weaker on size: Item 4.02
non-reliance shows CAR ≈ −0.9% at 1 day and −1.5% at 20 days over 2007–2023, decayed
roughly 5× from 1990s samples.

**But that cohort validated the set for the wrong side of the funnel, and v1 uses a
subset (§5.1).** The measurement was a *veto over a micro-cap-skewed discovery cohort* —
names one might buy. Held large/mid names are a different job, and item-by-item the set
does not transfer cleanly: 5.01 (change of control) fires most often on an *acquisition*,
frequently a premium buyout — a favorable outcome for a holder, not a negative; 2.05/2.06
(restructuring/impairment) are routine on large caps, backward-looking, and usually already
priced; 3.01 (delisting) is a micro-cap phenomenon that essentially never fires on a
quality-screened book. So v1 alerts on only the three clean, unambiguous negatives —
{1.03, 2.04, 4.02} — and the "same signal, opposite side of the funnel" reuse is
deliberately partial. This is the single most important quality decision in the design: a
silent monitor is trusted or discarded on its *first* alert, so the first one must be sharp.

### What the evidence does not support

An earlier draft justified this feature with Akepanidtaworn, Di Mascio, Imas & Schmidt
(*Journal of Finance* 78(6), 2023) — institutional PMs' sells underperform a random-sell
counterfactual by ~0.8%/yr. **That justification was withdrawn.** The paper's own interview
evidence gives the mechanism: *"Selling is simply a cash-raising exercise for the next
buying idea."* Sells underperform because they are a **funding operation** subordinate to a
buy, executed under a cash and mandate constraint. A 12-name book with no client flows, no
month-end, and no tracking-error budget does not have that constraint, so the causal driver
is absent. The finding is also unreplicated proprietary data, and a co-author is CEO of the
firm that sells trade-analytics on that premise.

**The user population is biased the other way.** Odean (1998): retail investors are 1.5–2×
more likely to sell a winner than a loser, and the winners they sold beat the losers they
kept by **3.4pp over the following year**. Barber & Odean (2000): high-turnover households
underperform by **2–6.5pp/yr**. The retail sell-side error is selling too readily, not
failing to sell.

**Therefore the burden of proof runs against alerting, not for it.** A system whose output
is a stream of reasons to consider selling pushes on the side of the ledger where this user
population is already over-active and already wrong.

### What follows

1. **No stance, ever.** Output routes to a primary source. It never says sell, never scores
   an exit, never renders a verdict.
2. **No price-derived trigger, ever.** See §2.
3. **Alert rate is the primary design variable**, and it is bounded structurally rather than
   by a tuned threshold (§5.2).
4. **Measured from day one.** Every alert is firehose-logged with a pre-registered expected
   sign, at the same bar `edgar:8k` and `edgar_buyback` were held to — **both of which were
   killed on evidence.** A sell-side signal does not get a lower bar than a buy-side one.

### The honest limitation

The product claim — that routing attention improves decisions — needs many exits to test. A
12-name book produces perhaps 5–10 exits/year, so the decision ledger (§3.3) is
statistically dead for years. **This feature cannot be killed on evidence the way a scoring
leg can.** That is a real departure from repo doctrine, taken deliberately and labeled
here rather than papered over. The mitigation is to keep the surface small enough that
being wrong is cheap.

## 2. Non-goals — normative

The following **must never produce an alert.** This list is enforced by
`KNOWN_BREACH_KINDS` + an AST scan (§9), not by convention.

**No price-derived _alert_, ever** — no drawdown-from-entry, no trailing stop, no
52-week low, no relative strength, no moving-average cross may ever *push* a message or
interrupt you. Two independent reasons:

- Price-from-entry is a **purchase-price anchor** — investor-specific, carrying zero
  information about the business, and the canonical disposition-effect reference point
  (Shefrin & Statman 1985; Odean 1998). Two people holding the same stock would get
  different alerts.
- It is **the villain of the paper the earlier draft cited.** Akepanidtaworn et al. find
  managers sold extreme-past-return assets — up *or* down — at ~50% higher rates, and name
  that as *the* value-destroying heuristic. The earlier draft cited the paper and then
  implemented its villain as a Tier-1 interrupt.

A drawdown alert also fires hardest in market-wide selloffs, when many positions cross the
band in the same week. The information content of ten simultaneous alerts is "the market
went down," which the user already knows — and it would arrive at the moment judgment is
most impaired. `CLAUDE.md` already warns that drawdown "peaks at bottoms and can be
anti-predictive at turning points."

**The door is left open for pulled price _context_.** The ban is on price *pushing* — an
alert or interrupt. It does **not** forbid a future pull-only surface (a v2 `/review NVDA`)
from showing a price move as passive context the user chose to look at, which carries none
of the salience-manufacturing harm. The distinction is push vs. pull, and it is the whole
principle: the system never uses price to *decide to contact you*. v1 builds no price
surface at all; this paragraph exists only so the doctrine doesn't foreclose one.

**No continuous-threshold crossing.** No composite drift, no per-axis decay, no valuation
change, and **no hard-gate transitions in v1** (see §10 for why they were cut and what they
would need). No `value_trap`, `crowded_short`, `news_spike`, `social_hype`,
`risk_off_regime`, `dilution`.

**No tax or holding-period nudges.** An earlier draft surfaced "crosses 12mo in 26 days."
Framed as account-agnostic, it is a US-tax-specific constant, and placing it next to an
alert nudges toward tax-motivated timing — advice-shaped, and contrary to §11.

**Out of scope entirely:** auto-sell, stop-losses, position sizing, opportunity-cost
ranking, tax-lot optimization, account-type modeling.

## 3. Position store

### 3.1 Ownership — one writer per store

The bot and the daily timer are separate processes. Rather than lock a shared file,
**ownership is split so each store has exactly one writer:**

| Store | Owner | Contents |
|---|---|---|
| `positions.json` | **bot** (interactive commands only) | tickers, shares, thesis, `entry_card`, decisions |
| `ScoutState` | **daily run** (already its exclusive owner) | `position_alerts_seen` (dedup ledger) + `position_last_prompted` (§5.4) |

This matters. Atomic `os.replace` prevents a torn file but **not a lost update**: the daily
run would read at 22:30, spend tens of seconds screening, then write back — silently
erasing any `/add` that landed in that window, or having its own dedup ledger erased by
one. The latter would re-arm every alert on the book, breaking the one invariant that keeps
this feature unmuted. Split ownership removes the failure mode instead of documenting it.
(Verified: `bot.py` currently has zero `ScoutState` references — the daily run's exclusivity
is real.)

### 3.2 Schema

```json
{
  "version": 1,
  "positions": {
    "NVDA": {
      "added": "2026-07-21",
      "shares": 12,
      "thesis": "Datacenter capex cycle has another two years",
      "entry_card": {"composite": 71.2, "quality": 78, "…": "…",
                     "gates": [], "flags": [], "abstentions": [],
                     "sources": ["yahoo", "finnhub", "edgar"],
                     "as_of": "2026-07-21"}
    }
  }
}
```

`shares` and `thesis` are **optional** (`null` allowed). `/add NVDA` alone is valid.

**No lots, no FIFO, no cost basis, no CSV migration.** Each was cut for a specific reason
recorded in §10.

**`entry_card.sources` is load-bearing.** An earlier draft captured `entry_card` on the full
FMP chain while the monitor ran on the rationed free chain. Those produce different `value`
and therefore different `composite` — so the first delta rendered for every position would
have been fabricated, with nothing having changed in the world. Two guards: `/add` screens
on **the same chain the monitor uses** (§4), and `sources` is stored so any future delta
renderer can refuse a cross-chain comparison. `abstentions` is stored for the same reason —
a v2 gate-diff must distinguish "gate cleared" from "input was `None`" (§10).

### 3.3 Decision ledger — and why `/remove` is non-destructive

`/hold` and `/remove` append one line to `decisions.jsonl` (gitignored, append-only):

```json
{"ts": "2026-07-21", "ticker": "NVDA", "action": "hold", "note": "impairment is the old
 datacenter fleet, not demand", "trigger": "8k:0001045810-26-000123"}
```

Append-only JSONL, no structure to maintain, no reader in v1. It exists because the data
**cannot be reconstructed later** and because it records the outcome that matters most —
*"I looked and decided to hold"* — which an exits-only ledger would miss entirely. It also
doubles as the engagement signal (§5.4).

**`/remove` must not lose the thesis.** It is a Telegram command with no undo and no
confirmation prompt, so a fat-finger deletes a position — and the thesis you wrote months
ago is irreplaceable. Therefore `/remove` embeds the **entire** position record (thesis,
`entry_card`, `added` date, shares) into its `decisions.jsonl` line before removing it from
`positions.json`:

```json
{"ts": "2026-07-22", "ticker": "NVDA", "action": "remove", "note": "thesis broke",
 "position": {"added": "2026-03-14", "shares": 12, "thesis": "…", "entry_card": {…}}}
```

The removal is then non-destructive — recoverable by hand from the ledger — which is why no
confirmation prompt is needed. (No confirmation is a deliberate choice: a yes/no round-trip
on every exit is its own friction, and the ledger makes it unnecessary.)

## 4. Commands

| Command | Behavior |
|---|---|
| `/add NVDA` | Adds the position. Shares and thesis optional: `/add NVDA 12`, `/add NVDA 12 datacenter capex cycle`. **Bulk form:** `/add NVDA, MSFT, LMT` (comma-parsed, reusing `/screen`'s tokenizer) adds several bare tickers in one message — first-run setup is one paste off a broker app, not twelve messages. On an existing ticker, **fills in or updates** shares/thesis without disturbing `added` or `entry_card`. Screens and replies with the card (bulk form replies with a count + the current holdings). |
| `/thesis NVDA <why you own it>` | Sets or replaces the thesis on an existing holding. The lazy path for the friction-minimized `/add`. |
| `/hold NVDA [note]` | Records that you looked at an alert and chose to hold. Appends to `decisions.jsonl`. |
| `/remove NVDA [reason]` | Closes the position **non-destructively** (§3.3). Alias `/sold`. |
| `/portfolio` | The **single** holdings view — the existing screened dashboard (exposure, sector concentration, per-name scorecards), rewired from `portfolio.csv` to this store. |

There is **no `/positions`.** An earlier draft had a bare ticker+shares list alongside
`/portfolio`; two "show my holdings" commands is redundant, and the bare list is caught in a
bind — with no returns it is a dead view you would ignore in favor of your broker, and with
returns it becomes the purchase-price disposition anchor §2 bans. `/portfolio` is the one
viewer; `/add` confirms the holding count on success.

`/add` **screens on the free chain** (`digest_sources(include_fmp=False)`) — the same chain
the monitor uses — so `entry_card` is comparable by construction. The reply notes that
`/screen NVDA` gives the full-chain view including `peg` and `upside_to_target`.

Positions without `shares` are monitored for filings but **excluded from exposure and
sector math**, and named explicitly in the `/portfolio` output. This reuses `portfolio.py`'s
existing `unpriced` / `no_data_tickers` convention — never silently drop a holding.

**Thesis is optional but nudged, never required.** Requiring it at `/add` reintroduces the
friction that left `portfolio.csv` unused; omitting it entirely produces alerts with no
anchor (§5.3). So the nudge is asymmetric: `/add` accepts a bare ticker, and every
`/portfolio` line and the `/add` reply carry `⚠ no thesis — /thesis NVDA <why>` until one is
set. The thesis is captured lazily, when you have a reason to write it, not as an entry tax.

New user-facing terms require `scout/glossary.py` entries (the AST-scan test enforces it).

## 5. The trigger

### 5.1 One trigger, three items

A **clean-negative 8-K filed against a held ticker** — items **{1.03 bankruptcy, 2.04 debt
acceleration, 4.02 non-reliance/restatement}**.

This is a **subset** of the seven-item set the veto sweep matches, chosen per §1: these three
are unambiguously bad for an equity holder, rare, and high-signal. The other four the sweep
sees are filtered out for the held-book job — 5.01 (change of control) is frequently a
premium buyout and would fire ⚠ on *good* news; 2.05/2.06 are routine, already-priced
large-cap noise; 3.01 (delisting) does not fire on a quality book. The monitor matches
`meta.items` against the v1 subset and ignores the rest. A `positions_monitor.items` config
key holds the subset so it can be widened later on engagement evidence, not guesswork.

It is the only *trigger family* because it is the only candidate that is simultaneously
**discrete, dated, verifiable, free, and measured in-repo** (§1). `daily.py:_negative_veto_sweep`
already sweeps EFTS market-wide every day and returns a ticker-keyed map of
`{last_date, items, adsh}`. The monitor reads that map. **Marginal cost: zero fetches.**

The same data already *drops* discovery candidates pre-screen; here it *surfaces* a held
name. Same signal, opposite side of the funnel — but a **narrower** item set, because the
sign of an item can differ by side (a pending control change is a reason not to *enter*, and
often a reason to be pleased you *held*).

**Dedup:** `8k:<accession>` recorded in `ScoutState.position_alerts_seen` via the existing
`_append_capped` helper. A given filing surfaces exactly once, ever.

**Known limitation (stated, not fixed):** the veto map holds **one record per ticker,
newest-wins**, pruned at 30 days (`state.update_eightk_negative`). Two negative 8-Ks for one
holding inside a short window can overwrite before the monitor observes the first, so it
would be missed. Low frequency; accepted for v1 rather than claiming completeness this
design does not have.

### 5.2 Delivery — structurally rate-capped

**The alert is a section in the existing daily digest, not a standalone message.** The scout
push already arrives daily; a held-name filing becomes a section at the top of it, gated by
`applies()` exactly like `_Portfolio`.

This bounds the alert rate at **one message per day by construction**, regardless of what
fires or how wrong the rate estimate is. A structural cap is more robust than a
`max_alerts_per_year` config that has to be tuned and enforced by a test.

It also matches urgency to the trigger set: nothing in {1.03, 2.04, 4.02} is time-critical
for a medium-to-long-term holder deciding whether a thesis broke — same-day versus
next-digest is immaterial.

**A once-daily heartbeat rides the same section.** A silent monitor is indistinguishable
from a broken one, so the digest carries a one-line footer even when nothing fired —
`Monitoring N holdings · last filing check <date>`. It confirms the sweep is alive without
being an interrupt, and it is nearly free since the digest already renders.

**Promotion path:** the item set is deliberately narrow to start (§5.1). If engagement data
(§5.4) shows you act on these, *widen* — add 2.05/2.06, or promote 4.02/1.03 to a standalone
message — on evidence. Starting quiet and widening is recoverable; starting loud and getting
muted is not.

### 5.3 Message

With a thesis on the name — the thesis is the anchor that makes the alert a *decision*
rather than free-floating anxiety:

```
NVDA — 8-K item 4.02 filed 2026-07-19
Non-reliance on previously issued financial statements
https://www.sec.gov/Archives/edgar/data/…
Your thesis: "Datacenter capex cycle has another two years"
→ /hold NVDA <note>   ·   /deep NVDA   ·   /remove NVDA <reason>
```

Without one — the friction-minimized `/add NVDA` path — the alert **leads with the missing
anchor** instead of showing an empty quote, turning the gap into a one-tap prompt at the
moment it matters most:

```
NVDA — 8-K item 4.02 filed 2026-07-19
Non-reliance on previously issued financial statements
https://www.sec.gov/Archives/edgar/data/…
⚠ No thesis on file — why do you own this? /thesis NVDA <reason>
→ /hold NVDA <note>   ·   /deep NVDA   ·   /remove NVDA <reason>
```

No stance, no score, no recommendation. The alert's job is to route to primary evidence.
Item codes are rendered with their plain-English meaning from `scout/glossary.py`.

### 5.4 Engagement

`last_prompted` (per ticker, in `ScoutState`) is tracked **separately from any decision**.
An earlier draft stamped `last_reunderwrite` on delivery — asserting in the schema that a
review happened when the data only recorded that a message was sent.

If a ticker is prompted 3× with no `/hold`, `/deep`, or `/remove`, the digest notes once
that alerts for it appear unread. This is the disengagement detector and the input to the
promotion decision in §5.2.

## 6. Wiring

- **`state.set_held` is fed from the position store.** It exists (`state.py:270`), is
  called nowhere, and `funnel.py:32` already drops held tickers via `is_held` — so Scout
  will re-surface names you own the moment positions exist, burning FMP deep-screen slots
  from a budget of ~10/day. Latent today (`held` is `[]`), real immediately after. ~10 lines.
- **`daily.py:run`** gains one failure-isolated monitor step after the veto sweep: read
  positions → intersect with `veto_map` → drop seen accessions → emit section → firehose-log
  → persist. Any exception is caught and noted; it must never crash an already-delivered run
  (the `_record_session_picks` precedent).
- **`bot.py`** gains four handlers; `_do_portfolio` swaps its loader.
- **`portfolio.py`** — `summarize()` and the `_Portfolio` section are untouched. Only the
  input path changes.

## 7. Config

```yaml
portfolio:
  store: positions.json      # bot-owned source of truth
  decisions: decisions.jsonl # append-only decision ledger
  max_holdings: 50
  monitor:
    enabled: true            # remove this block -> byte-identical pre-feature behavior
    include_fmp: false       # holdings screen on the free chain (quota; see below)
    items: ["1.03", "2.04", "4.02"]   # v1 clean-negative subset (§5.1); widen on evidence
```

**FMP quota is why `include_fmp` defaults false.** The harness makes ~13 FMP calls/ticker
against a 250/day free limit (≈19 tickers/day), and discovery already spends up to
`scout.daily_x` = 10. Screening 12 holdings on the full chain would starve the funnel.
Verified: `digest_sources(include_fmp=False)` yields `[yahoo, finnhub, edgar]` with yahoo
still leading the price merge. Interactive `/screen` and `/deep` always keep the full chain.

## 8. Failure modes

| Failure | Behavior |
|---|---|
| Store missing / unreadable / corrupt | Empty, loud warning, never raises. Bot stays up. |
| Monitor step raises | Caught; manifest note; digest still delivers. |
| Veto sweep stale or failed | Already degrades loudly with a stale note; the monitor **inherits that note** rather than silently under-alerting. |
| Holding screens with no data | Surfaced via the existing `no_data` predicate; contributes no exposure. |
| Yahoo price series unavailable (documented VPS WAF history) | Exposure/weights abstain; **filing alerts are unaffected** — they need only the ticker. |
| Ticker change (FB→META) | **Known-unhandled.** The old key goes no-data, or worse resolves to a different company now holding the ticker. After 5 consecutive no-data sessions the digest prompts once, then goes quiet. `/remove` + `/add` is the manual fix. |
| Cash acquisition / spinoff | **Known-unhandled**, named in §10. |
| Split | Share count becomes stale, so weights and exposure are wrong until corrected by hand. **Filing alerts are unaffected.** No split detection in v1; `/add NVDA <newshares>` corrects it. |

## 9. Testing

- **Store:** add / update / remove, optional shares and thesis, unknown-key preservation,
  corrupt-file tolerance, atomic write.
- **Dedup:** one accession surfaces exactly once across N consecutive sessions; the capped
  ledger never evicts an unbounded-growth key in a way that re-arms a recent alert.
- **`KNOWN_BREACH_KINDS` + AST scan** — a frozen set plus a scan asserting no alert kind is
  emitted outside it, and that each has a glossary entry. This replaces an earlier draft's
  proposed "test that the non-goals list produces no alert," which was unenforceable: §2 is
  a list of English concepts, and a future contributor adding a trigger would not fail it.
  The repo already solved this exact problem with `KNOWN_GATES` / `KNOWN_FLAGS`.
- **Chain consistency:** `entry_card.sources` matches the monitor's chain; a cross-chain
  delta is refused.
- **Quota:** the holdings screen resolves to the free chain when `include_fmp: false`.
- **Isolation:** a raising monitor step still delivers the digest.
- **Disabled-block invariance:** absent `monitor:` → discovery run byte-identical.

## 10. Deferred, with reasons

Named so they do not creep back in.

| Cut | Reason |
|---|---|
| **Drawdown bands** | §2. Deleted outright, **not shipped disabled** — leaving the key is an invitation to enable the one feature most likely to prompt selling a bottom. |
| **Hard-gate transitions** | Three of four are continuous-threshold crossings (`heavy_insider_selling` reads monthly-refreshed Finnhub MSPR), violating §2. And gate-diffing cannot distinguish "cleared" from "input was `None`" — every gate short-circuits on `None`, so one EDGAR timeout would clear a key and the next night's success would fire a false alert. Returns only when **abstention-aware** (using `ScoreCard.abstentions`) and **hysteretic** (re-fire only after N consecutive absent sessions and ≥90 days). |
| **8-K items 2.05 / 2.06 / 3.01 / 5.01** | Matched by the sweep but **not alerted** in v1 (§5.1). 5.01 is often a favorable buyout; 2.05/2.06 are already-priced large-cap noise; 3.01 doesn't fire on a quality book. Widen the `items` config on engagement evidence. |
| **`/positions` command** | Redundant with `/portfolio`; the bare list is either a dead view (no returns) or a disposition anchor (with returns). `/portfolio` is the single holdings view. |
| **Post-earnings re-underwrite** | Deferred pending engagement data. Likely v2 shape is **pull-only `/review <ticker>`** — thesis + delta-vs-entry + current card on demand, no push, no queue, no cadence machinery. |
| **`dilution`** | Annual data, continuous threshold, slow quality drift — not a dated event. |
| **Lots / FIFO / `/trim`** | Highest-complexity, lowest-frequency code in the design. Also produced an undefined return for multi-lot positions (no single entry date). |
| **Cost basis** | Returns are date-anchored (`pick_performance` is split-safe precisely because it never divides a fresh adjusted close by a stored scalar). Stored cost would be display-only and wrong after a split. |
| **CSV migration** | One user, one file. Hand-write or use `/add`. |
| **Holding-duration notices** | §2. |
| **Corporate actions** | Ticker changes, cash M&A, spinoffs — genuinely unhandled (§8), not merely deferred. |
| **Decision-ledger measurement** | Recording starts v1; reading is years away (§1). |

## 11. Framing

Every surface carries the repo-wide tag: **screening triage, not investment advice.** This
feature emits no stance, no target, and no exit recommendation. It tells you a document was
filed against something you own, shows you the thesis you wrote, and links you to the
primary source. The decision is yours and the system records only what you decided.

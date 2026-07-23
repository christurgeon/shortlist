# Position Monitor — design (v1)

**Status:** implemented and shipped (PR #146, 2026-07-22) — deployed to `/opt/shortlist`.
Spec dated 2026-07-21; this document remains the design authority. v2 items in §10 are
still deferred.

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
| `positions.json` | **bot** (interactive commands only) | tickers, shares, thesis, `entry_card` |
| `ScoutState` | **daily run** (already its exclusive owner) | `position_alerts_seen` — the dedup ledger, nothing else |

This matters. Atomic `os.replace` prevents a torn file but **not a lost update**: if both
processes wrote the same file, the daily run would read at 22:30, spend tens of seconds
screening, then write back — silently erasing any `/add` that landed in that window, or
having its own dedup ledger erased by one. The latter would re-arm every alert on the book,
breaking the one invariant that keeps this feature unmuted. Split ownership removes the
failure mode instead of documenting it.

**The daily run READS `positions.json` (bot-written) but never writes it** — the monitor
step and the `set_held` wiring both only read it, and write their state into `ScoutState`.
That read is safe against a concurrent bot write with no lock: atomic `os.replace` swaps the
directory entry to a new inode, and a POSIX reader either opens the old inode (and reads it
whole) or the new one (whole) — **never a half-written file**. The monitor sees a
possibly-one-cycle-stale but always internally-consistent snapshot; a `/add` mid-run is
simply picked up next digest. (Verified: `bot.py` has zero `ScoutState` references and the
daily run is `positions.json`'s sole reader-not-writer — the exclusivity is real. The one
rule an implementer must honor: **the daily monitor never writes `positions.json`.** If a
future "mark alerted" needs per-position state, it goes in `ScoutState`, not the store.)

### 3.2 Schema

```json
{
  "version": 1,
  "positions": {
    "NVDA": {
      "added": "2026-07-21",
      "shares": 12,
      "thesis": "Datacenter capex cycle has another two years",
      "entry_card": {"composite": 71.2,
                     "sources": ["yahoo", "finnhub", "edgar"],
                     "as_of": "2026-07-21"}
    }
  }
}
```

`shares` and `thesis` are **optional** (`null` allowed). `/add NVDA` alone is valid.

**No lots, no FIFO, no cost basis, no CSV migration.** Each was cut for a specific reason
recorded in §10.

**`entry_card` is a minimal seam, not a rendered field.** Nothing in v1 displays it — the
delta view is deferred (§10). It stores only `composite`, `sources`, and `as_of`, because
those cannot be reconstructed point-in-time later (the same "record now, read later" logic
as the decision ledger), and it is near-free since `/add` already runs the screen.
Deliberately **not** stored: `gates`/`flags`/`abstentions`, which existed only to feed a v2
gate-diff that §10 defers twice over — capturing them now is machinery for a feature two
steps away. `sources` **is** kept and load-bearing: `/add` screens on the same free chain
the monitor uses (§4), and `sources` lets any future delta renderer refuse a cross-chain
comparison (an earlier draft captured the entry card on the full FMP chain and would have
rendered a fabricated day-one delta against the free-chain monitor).

### 3.3 Decision ledger — and why `/remove` is non-destructive

`/hold` and `/remove` append one line to `decisions.jsonl` (gitignored, append-only):

```json
{"ts": "2026-07-21", "ticker": "NVDA", "action": "hold", "note": "impairment is the old
 datacenter fleet, not demand", "trigger": "8k:0001045810-26-000123"}
```

Append-only JSONL, no structure to maintain, no reader in v1. It exists because the data
**cannot be reconstructed later** and because it records the outcome that matters most —
*"I looked and decided to hold"* — which an exits-only ledger would miss entirely.

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
| `/add TICKER [shares]` | `/add NVDA` or `/add NVDA 12`. Shares is an **optional numeric token** (accepts a fraction, e.g. `12.5`). **Bulk form:** `/add NVDA, MSFT, LMT` — a comma anywhere means bulk, bare tickers only. On an existing ticker, fills/updates shares without disturbing `added` or `entry_card`. Screens the name and replies with the card; bulk replies with a count. |
| `/thesis TICKER <why you own it>` | Sets/replaces the thesis. **The only command that takes free-text prose** (see grammar below). |
| `/hold TICKER [note]` | Records that you looked at an alert and chose to hold. Appends to `decisions.jsonl`. |
| `/remove TICKER [reason]` | Closes the position **non-destructively** (§3.3). Alias `/sold`. |
| `/portfolio` | The **single** holdings view — the existing screened dashboard (exposure, sector concentration, per-name scorecards), rewired from `portfolio.csv` to this store. |

**Grammar — kept unambiguous by keeping prose out of `/add`.** An earlier draft let `/add`
carry an inline thesis (`/add NVDA 12 datacenter capex cycle`). That is unparseable: `/add
NVDA 2 years of runway` cannot tell shares from thesis, and a comma inside a thesis collides
with the bulk form. The fix costs nothing in capability and removes every ambiguity —
**`/add` never takes prose; thesis is always `/thesis`.** Concretely:

- `/add` accepts a ticker and an optional *numeric* second token. A non-numeric second token
  is rejected with usage text (it is almost certainly a thesis typed in the wrong command).
- **Uppercase the ticker token only** — do not reuse `_tickers()` (`bot.py:38`), which
  upper-cases the whole line; `/thesis` prose must keep its case. `BRK.B` and lowercase
  input (`/add nvda`) both work because only the validated ticker token is upper-cased.
- `/thesis`, `/hold`, `/remove` on a **not-yet-tracked** ticker reply `not tracked — /add
  TICKER first` — never auto-create, never silent error.

There is **no `/positions`** (§10): `/portfolio` is the one viewer.

`/add` **screens on the free chain** — it must resolve `digest_sources(base,
include_fmp=False)` explicitly, **not** inherit the bot's default `self.sources` (which
includes `fmp`, `bot.py:141`) — so `entry_card.sources` matches the monitor's chain by
construction. The reply notes `/screen NVDA` gives the full-chain view (`peg`,
`upside_to_target`).

Positions without `shares` are monitored for filings but **excluded from exposure and
sector math**, and named explicitly in the `/portfolio` output (the existing
`unpriced` / `no_data_tickers` convention — never silently drop a holding).

**Thesis is optional but nudged, never required.** Requiring it at `/add` reintroduces the
friction that left `portfolio.csv` unused; omitting it produces anchorless alerts (§5.3). So
the nudge is asymmetric: `/add` accepts a bare ticker, and every `/portfolio` line, the
`/add` reply, and the thesis-less alert carry `⚠ no thesis — /thesis NVDA <why>` until one
is set. Captured lazily, not as an entry tax.

**First-run experience (must not be skipped in implementation).** The on-ramp is as
important as the alert for a CRUD-first feature:

- **`_HELP` (`bot.py:84`) gains a line per new command** — plain verbs: `/add NVDA 12 —
  track a holding`, `/thesis NVDA <why> — why you own it`, `/remove NVDA — stop tracking`,
  and note `/portfolio` now shows what you `/add`.
- **The empty-state `/portfolio` reply is rewritten.** Today it says *"create portfolio.csv
  …"* (`bot.py:290`) — **wrong** after the rewire. New copy: *"No holdings yet. Add one with
  `/add NVDA` (shares optional), or paste several: `/add NVDA, MSFT, LMT`."* This turns the
  most-likely first interaction from a dead-end into the on-ramp.
- **The `/add` success reply teaches the next step** — confirms the holding count and points
  at `/portfolio`.

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
`_append_capped` helper (copy `add_eightk_accessions`, `state.py:130`). A given filing
surfaces exactly once, ever. **Cap sizing matters:** `_append_capped` evicts oldest past
cap, and the veto map is 30-day-pruned — so an accession can only re-arm if it is evicted
*while still in the map*. Size the cap well above the 30-day held-book inflow (the far-denser
8-K originator uses 500; 500 is generous here) and an eviction can never re-fire a live
alert. Document the window in the setter docstring, like `add_eightk_neg_logged`.

**Known limitation (stated, not fixed):** the veto map holds **one record per ticker,
newest-wins**, pruned at 30 days (`state.update_eightk_negative`). Two negative 8-Ks for one
holding inside a short window can overwrite before the monitor observes the first, so it
would be missed. Low frequency; accepted for v1 rather than claiming completeness this
design does not have.

### 5.2 Delivery — structurally rate-capped

**The alert is a section in the existing daily digest, not a standalone message.** The scout
push already arrives daily; a held-name filing becomes a **new** section near the top of it
(§6 — it is *not* the `_Portfolio` section, which the daily `build_report` never renders).
Its `applies()` keys on **payload-presence, not alert-presence**: it returns True whenever
the monitor is enabled and positions exist, so the heartbeat (below) renders even on a quiet
day. Byte-identical when the payload is absent, exactly like `_ValidationScoreboard`.

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

**Promotion path (manual judgment, not machinery).** The item set is deliberately narrow to
start (§5.1). If you find over time that you *act* on these alerts, widen the `items` config
— add 2.05/2.06, or promote 4.02/1.03 to a standalone push. For one user reading their own
digest, that judgment needs no instrumentation: an earlier draft built a `last_prompted`
"you've ignored this 3× " disengagement detector, which is patronizing for a solo user and
redundant with the firehose (every alert is already logged with a pre-registered expected
sign, §1). **Cut** — the firehose is the measurement seam; the promotion call is yours.
Starting quiet and widening is recoverable; starting loud and getting muted is not.

### 5.3 Message

**Plain meaning leads; the item code is secondary provenance.** "Non-reliance on previously
issued financial statements" does not read as *bad* to a human on a phone — so the first line
says what happened in plain words, with the SEC item code trailing for the link/glossary. The
three v1 glosses (also in `scout/glossary.py`, enforced by the §9 AST scan):

- **4.02** → *"its past financial statements can no longer be relied on — a restatement is coming"*
- **1.03** → *"filed for bankruptcy"*
- **2.04** → *"a lender is calling debt due early (default/acceleration)"*

With a thesis on the name — the anchor that makes the alert a *decision* rather than
free-floating anxiety:

```
NVDA — its past financials can no longer be relied on; a restatement is coming.
8-K item 4.02, filed 2026-07-19
https://www.sec.gov/Archives/edgar/data/…
Your thesis: "Datacenter capex cycle has another two years"
→ /hold NVDA <your note>   ·   /deep NVDA   ·   /remove NVDA <your reason>
```

Without one — the friction-minimized `/add NVDA` path — the alert **leads with the missing
anchor** instead of an empty quote, turning the gap into a one-tap prompt when it matters most:

```
NVDA — its past financials can no longer be relied on; a restatement is coming.
8-K item 4.02, filed 2026-07-19
https://www.sec.gov/Archives/edgar/data/…
⚠ No thesis on file — why do you own this? /thesis NVDA <reason>
→ /hold NVDA <your note>   ·   /deep NVDA   ·   /remove NVDA <your reason>
```

No stance, no score, no recommendation — the alert routes to primary evidence and stops.
(The `<your note>` placeholders read as literal to a first-time user; the first alert and
`_HELP` both note "type your reason after the command".)

## 6. Wiring

- **`state.set_held` is fed from the position store.** It exists (`state.py:270`), is
  called nowhere, and `funnel.py:32` already drops held tickers via `is_held` — so Scout
  will re-surface names you own the moment positions exist, burning FMP deep-screen slots
  from a budget of ~10/day. Latent today (`held` is `[]`), real immediately after. ~10 lines.

- **`daily.py:run` — the monitor is THREE insertions, not one step.** `veto_map` is a live
  local from `_negative_veto_sweep` (`daily.py:505`) through the end of `run()`, but "emit a
  section" is not something `run()` does inline — a section renders inside `build_report`.
  So:
  1. **Compute the payload** just before the `build_report` call (`daily.py:581`): intersect
     `positions.json` with `veto_map`, filter to the `items` subset, drop
     `position_alerts_seen`. Also assemble the heartbeat (`count`, `last filing check`).
  2. **Thread it through `build_report`** as a new `positions_monitor=` kwarg → `build_view_model`
     → a new `ReportVM` field → a new `Section` (§below). The daily `build_report` passes no
     `portfolio=`, so the `_Portfolio` section does not render on the digest — the monitor
     needs its **own** section.
  3. **Firehose-log + persist `position_alerts_seen`** *after* `deliver()`, beside
     `_record_session_picks` (`daily.py:607`) — the "never crash an already-delivered run"
     precedent. The whole monitor is failure-isolated: any exception is caught + noted.

- **New digest section** (`scout/report/`): copy `_ValidationScoreboard` (`sections.py:644`)
  — a display-only, byte-identical-when-absent section. Touches: `viewmodel.py` (`ReportVM`
  field + `build_view_model` kwarg), `report/__init__.py` (`build_report` kwarg forward),
  `sections.py` (one `Section` class + one `SECTIONS` entry, placed right after
  `_MacroHeader` for "top"). `applies()` returns True on payload-presence (heartbeat).

- **`bot.py`** gains the `/add`, `/thesis`, `/hold`, `/remove` handlers, updates `_HELP`, and
  swaps `_do_portfolio`'s loader (`bot.py:288`) from `load_holdings(csv)` to the new store.

- **`portfolio.py` — `summarize()` is NOT untouched; it crashes on `shares=None`.** Its guard
  is on price, not shares (`value = h.shares * price if price else None`, `portfolio.py:126`),
  and `Holding.shares` is typed `float` (`portfolio.py:21`). The CSV loader never produced a
  null share count, so the new optional-shares store is the first producer — and `None *
  price` raises `TypeError`, crashing the whole `/portfolio` render on the first shares-less
  `/add`. **Fix:** widen `Holding.shares` to `Optional[float]` and guard `value = h.shares *
  price if (price and h.shares is not None) else None`. Then a shares-less holding flows to
  `value=None → weight=None`, excluded from exposure and listed in `unpriced` — exactly the
  §4 behavior. The `_Portfolio` **section** is genuinely untouched (it never reads
  `pos.shares`).

## 7. Config

```yaml
portfolio:
  store: positions.json      # bot-owned source of truth
  decisions: decisions.jsonl # append-only decision ledger
  max_holdings: 50
  monitor:
    enabled: true            # remove this block -> byte-identical pre-feature behavior
    items: ["1.03", "2.04", "4.02"]   # v1 clean-negative subset (§5.1); widen on evidence
    # NOTE: no `include_fmp` key here. The holdings screen is hardcoded to the free chain
    # (see below) — it is v1 fixed behavior, not a config knob.
```

**FMP quota is why the holdings screen defaults to the free chain.** The harness makes ~13
FMP calls/ticker against a 250/day free limit (≈19 tickers/day), and discovery already
spends up to `scout.daily_x` = 10. Screening 12 holdings on the full chain would starve the
funnel. `bot.py:_free_sources` calls `digest_sources(base, include_fmp=False)` **directly**
— `include_fmp` is a hardcoded `False` argument at the call site, not a `config.yaml` key
(v1: hardcoded, not a config key). Verified: `digest_sources(include_fmp=False)` yields
`[yahoo, finnhub, edgar]` with yahoo still leading the price merge. Interactive `/screen`
and `/deep` always keep the full chain.

## 8. Failure modes

| Failure | Behavior |
|---|---|
| Store missing / unreadable / corrupt | Empty, loud warning, never raises. Bot stays up. |
| Position added without shares | `summarize()` guards `shares=None` (§6) → no exposure, listed in `unpriced`. Must be tested — it is the first-ever null-shares path. |
| Monitor step raises | Caught; manifest note; digest still delivers. |
| Veto sweep stale or failed | Already degrades loudly with a stale note; the monitor **inherits that note** rather than silently under-alerting. |
| Holding screens with no data | Surfaced via the existing `no_data` predicate; contributes no exposure. |
| Yahoo price series unavailable (documented VPS WAF history) | Exposure/weights abstain; **filing alerts are unaffected** — they need only the ticker. |
| Ticker change (FB→META) | **Known-unhandled.** The old key goes no-data, or worse resolves to a different company now holding the ticker. After 5 consecutive no-data sessions the digest prompts once, then goes quiet. `/remove` + `/add` is the manual fix. |
| Cash acquisition / spinoff | **Known-unhandled**, named in §10. |
| Split | Share count becomes stale, so weights and exposure are wrong until corrected by hand. **Filing alerts are unaffected.** No split detection in v1; `/add NVDA <newshares>` corrects it. |

## 9. Testing

Each has a close in-repo template to copy — this is pattern-matching, not invention:

- **Store:** add / update / remove, optional shares and thesis, unknown-key preservation,
  corrupt-file tolerance, atomic write. (Back-compat idiom: `test_state.py:35`.)
- **`shares=None` render:** a holding with null shares flows through `summarize` → `/portfolio`
  render without a `TypeError` and is listed in `unpriced` (the §6 crash-fix regression guard).
- **Dedup:** one accession surfaces exactly once across N consecutive sessions; the capped
  ledger never re-arms a recent alert on eviction. Template:
  `test_scout_daily_veto.py:152` (fire-once across runs) + `:79` (cap round-trip).
- **`KNOWN_BREACH_KINDS` + AST scan** — a frozen set plus a scan asserting no alert kind is
  emitted outside it, each with a glossary entry. Enforceable where an earlier draft's "test
  that the §2 non-goals produce no alert" was not (§2 is English concepts). Template:
  `test_scoring_names.py` (whole file — AST walk + vacuity floor) + the glossary-binding
  `tests/scout/test_glossary.py:71`.
- **Chain consistency:** `entry_card.sources` matches the monitor's chain.
- **Quota:** the holdings screen resolves to the free chain (`include_fmp=False` is
  hardcoded in v1, not a config key — see §7).
- **Section isolation + disabled-block invariance:** all *other* digest sections are
  byte-identical whether the monitor payload is present or absent, and absent `monitor:` →
  discovery run byte-identical. Template: `test_scout_report_sections.py:82` (section
  present-vs-absent) + `test_scout_daily_veto.py:297` (run-level disabled byte-identical).

## 10. Deferred, with reasons

Named so they do not creep back in.

| Cut | Reason |
|---|---|
| **Drawdown bands** | §2. Deleted outright, **not shipped disabled** — leaving the key is an invitation to enable the one feature most likely to prompt selling a bottom. |
| **Hard-gate transitions** | Three of four are continuous-threshold crossings (`heavy_insider_selling` reads monthly-refreshed Finnhub MSPR), violating §2. And gate-diffing cannot distinguish "cleared" from "input was `None`" — every gate short-circuits on `None`, so one EDGAR timeout would clear a key and the next night's success would fire a false alert. Returns only when **abstention-aware** (using `ScoreCard.abstentions`) and **hysteretic** (re-fire only after N consecutive absent sessions and ≥90 days). |
| **8-K items 2.05 / 2.06 / 3.01 / 5.01** | Matched by the sweep but **not alerted** in v1 (§5.1). 5.01 is often a favorable buyout; 2.05/2.06 are already-priced large-cap noise; 3.01 doesn't fire on a quality book. Widen the `items` config on engagement evidence. |
| **`/positions` command** | Redundant with `/portfolio`; the bare list is either a dead view (no returns) or a disposition anchor (with returns). `/portfolio` is the single holdings view. |
| **Engagement detector** (`last_prompted` + "ignored 3×") | Patronizing for a solo user who reads their own digest, and redundant with the firehose (the measurement seam). ScoutState carries only `position_alerts_seen`. |
| **`entry_card` gates/flags/abstentions** | Captured only to feed a v2 gate-diff that is itself deferred (this table). v1 stores the minimal `composite`/`sources`/`as_of` seam. |
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

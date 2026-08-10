# TODO — open follow-ups

**A working set of open work, not a session journal** — when an entry's work ships, delete it
rather than marking it done. The durable record is `CLAUDE.md` (behaviour + landmines),
`docs/audits/` (evidence) and git history; a resolved entry left here is pure cost. This file
reached 2,133 lines by 2026-08-08 because nobody owned removing anything.

See `docs/PREDICTIVE_SIGNALS_RESEARCH.md` for signal designs and `docs/ASSESSMENT_GAPS.md` for
the scoring roadmap.

> **HOW TO PRIORITISE ANYTHING BELOW (restated by the user, 2026-08-07).** The bar is
> **"surface interesting stocks the user evaluates, and passes to `/deep` when they want a
> closer look."** *It is fine for a signal to have no measurable edge.* This is `CLAUDE.md`'s
> design premise, and it means the price-feed-blocked items (the `form4`/`13f` backfill
> cohorts, the regime-break audit, attributing the buyback verdict) are **NOT** the top of
> this list — they answer an alpha question the project is not asking. A prior session drifted
> into treating "no repo-measured edge" as a defect; it is the expected condition.
>
> Work that serves the actual bar — *does a surfaced name earn the user's attention* — ranks
> above work that measures forward returns. By the same token, guards about **wasting the
> user's attention** (the quality floor, the investability floor, the negative-8-K veto) need
> no alpha evidence to justify themselves; they need only to be right about what they claim.

---

# 1. Watch items — deployed, not yet observed

## `shortlist-accumulate` has NO failure alerting (2026-08-10)

Failure alerting shipped in `b7fbe77` and covers the **scout only in practice**. The
`OnFailure=` line is in the accumulate unit's heredoc and pinned by a test, but the installer
only regenerates `shortlist-accumulate.service` under `SHORTLIST_ACCUMULATE=1` — so the unit on
the box still dates from 2026-07-08 and has no `OnFailure=`. That timer is **active** (21:30
UTC) and currently fails silently.

```
sudo SHORTLIST_ACCUMULATE=1 bash deploy/install_opt_shortlist.sh
systemctl cat shortlist-accumulate.service | grep OnFailure   # the check that settles it
```

Also unverified end-to-end for either unit: nothing has actually *failed* yet, so the
`OnFailure` → template → script → Telegram chain is untested in situ. Force it with a transient
unit carrying the same `OnFailure=`, or wait for a real failure and see whether the alert lands.

**Status:** scout covered and deployed; accumulate is the open gap.

## Retry-vs-alert on a failed Telegram delivery (2026-08-10)

`state.mark_run_completed()` runs *before* the `return 2` on a delivery failure
(`daily.py:912`), so a failed delivery cannot be retried that day — a re-run no-ops as "already
completed". Looks like a deliberate trade (the unit comment cites avoiding a re-hit of the Yahoo
WAF endpoint), but the comment's "marked complete only after delivery" wording implies the
opposite, so one of the two is wrong. Now that a failed delivery pages, decide whether the alert
is sufficient or a bounded retry is wanted.

**Status:** deferred decision, nothing changed.

## `daily_x` 15 — first live run (2026-08-07)

`config.yaml:526` allocates **15** deep-screen slots/night (was 10); deployed 2026-08-08
(`/opt/shortlist` at `6a09dff`). Cleared against **runtime**, not request count: peak wall
clock is 227s (2026-08-05, 10 names) against `TimeoutStartSec=1800`. Per-provider cost was
checked — ticker count doesn't set the request *rate* (`collector.collect` runs 8 in parallel,
`EdgarSource` holds its own `_EDGAR_MAX_CONCURRENCY = 3`), FMP is 0 calls on the digest chain,
FINRA/WSB are one cached bulk fetch each.

Two things to check on the first real 22:30 run:
- **Coverage, not just completion.** Finnhub ships with **no retry** (`_max_retries = 0`,
  deliberate — "60/min is comfortable"). At 8 concurrent tickers × 8 calls ~64 can be in
  flight, and 15 tickers sustains that one batch longer. A 429 there does **not** error — it
  quietly thins a section. Read the coverage summary.
- Duration + any EDGAR 429s (`journalctl -u shortlist-scout.service`).

**Not yet stressed.** An off-schedule run 2026-08-10 (13:53 UTC, sandboxed state) completed in
~3 min with 591 SEC requests and no throttle errors, but the funnel only produced **9 candidates
→ 4 screened**, so the 15 slots were never contended and `dropped_for_budget` was 0. Note the
mid-day caveat for any repeat: EDGAR's daily index is not yet populated pre-market, so both
Form 4 and 13D logged `2026-08-10 index empty, used 2026-08-07` — a mid-day run silently screens
against the *previous* session's filings. Only a 22:30 run exercises this for real.

The deep-screen SEC draw is still **uncounted** (harness `EdgarSource` is outside
`sec_throttle`), so `daily_x` is not sizeable from `sec_requests`; if a real number is ever
needed it needs its own instrumentation.

## `wsb:novel` — first live run + the prereg clock (2026-08-08)

Deployed and verified inert-but-alive (`ran=True, 14 prior boards, 0 emissions` — the rule
fires on ~half of days). Not yet seen against a real 22:30 run: confirm `wsb:novel` names reach
the digest and the signal reports `ran=True` rather than degrading the run.

**Do not read the run gap as a fault.** The last real session is **2026-08-07** (Friday). The
Aug 8 and Aug 9 timers both fired and correctly no-opped — `journalctl -u shortlist-scout`
reads `run for 2026-08-07 already completed; nothing to do`, because the session resolves to
the last *trading* day and the weekend has none. So `daily_x: 15` and `wsb:novel` both get
their first real exercise on **Mon 2026-08-10 22:30 UTC**, and the 08-07 manifest still shows
`wsb_hype: disabled` (it predates the novelty deploy). Checked 2026-08-09.

**Composition is now answered** (2026-08-10, off-schedule 13:53 UTC run against a sandboxed
state, so it did not consume the real session): `wsb_hype` reported `ran=True`, `1 novel of 100
tracked (14 prior boards)`, and that name — `wsb:novel | ACHR`, strength 0.30 — cleared the
funnel and **reached the digest at rank 4**. So the rule fires, does not degrade the run, and
its output survives prefilter, the investability floor and slot contention. Nothing about the
end-to-end path is unobserved any more.

What remains is **only the prereg clock**: forward returns from 2026-08-08, K=3m, KILL a real
expected outcome. Do not re-verify plumbing here. (Caveat if anyone repeats the exercise: a
mid-day run reads a *stale* EDGAR index — see below — so the candidate mix differs from 22:30.)

**Forward returns are NOT measured, only composition.** `scout/preregister/wsb_novelty.yaml`
is committed with a live-forward window from 2026-08-08, K=3m. **A KILL is a real expected
outcome** — the rule surfaces some retail-lottery names (`LCID HTZ SOUN DJT`) whose profile
Bali's MAX-effect literature ties to *under*performance. No backfill is possible; ApeWisdom
publishes no history. Shipping it ENABLED is a deliberate exception to the contested-prior
precedent, recorded as the owner's call.

## `edgar:13f_material_add` — first live burst (shipped 2026-08-09)

ENABLED at weight 0.75, `ratio: 1.50`, `top_n: 5`, and **uncapped** (a `max_slots: 4` was
written then removed unshipped — see below). **Never emitted through the live scout** — the
detector itself HAS been
run against real filings offline (all 7 funds' Q1-2026 vs Q4-2025 pairs: 154/154 `sshPrnamt`
parsed, 6 adds, 0 abstentions, no split artifacts — design doc §6a). What is unobserved is the
live path: emission → funnel → cap → digest. **The deploy-by-2026-08-12 deadline is met** —
`/opt/shortlist` is at `0f47068` (deployed 2026-08-09 23:04 UTC), so no fund loses its Q2 adds.
Re-confirmed 2026-08-10: all 7 funds' latest 13F-HR is Q1 (period 2026-03-31) and already in
`thirteenf_seen`, the manifest reads `0 new 13F positions from 0 filings (7 funds), 0 material
add(s)`, and a forced-empty-seen probe against live SEC reproduced 8 new positions + 2 adds
with `cfg_key_for` routing the two kinds to their separate weights. So the code is verified
live; only the burst is pending. Q2 is due **2026-08-14** and these funds file at the deadline
(Q1 → filed May 14/15). Aug 15/16 are a weekend, so with `max_filings_per_day: 3` the 7-fund
burst absorbs across Fri 14 (3), Mon 17 (3), Tue 18 (1).

Read from the first post-burst manifest:
- the `N material add(s)` count, and that the `N new 13F positions` headline still counts
  **only** new positions (it was `len(out)` pre-ship, which would have absorbed adds);
- `N overlapping positions with unusable share counts` — every position held in **both** books
  whose `sshPrnamt` was missing or non-positive, tallied *before* the ratio test. A 3-digit
  value on a large filer is not itself a fault; it is an `sshPrnamt` coverage diagnostic, **not**
  "adds we abstained on";
- whether any add is a **stock split** false positive (shares ≈2.0× with flat book weight).
  Documented and deliberately unmitigated — count it before deciding it needs a guard.

Expect a new `INSUFFICIENT` row for `edgar:13f_material_add` in `shortlist-scout validate` and
the digest verdicts section: it has no prereg, for the same PiT CUSIP→symbology reason
`edgar:13f_new_position` has none. Expected, not a regression.

**Burst sizing, from the real Q1 filings + config order** (`max_filings_per_day: 3`): Fri Aug 14
→ Berkshire/Pershing/Baupost ≈10 emissions; **Mon Aug 17 → ValueAct/Third Point/Appaloosa ≈18
emissions, the one night contention is actually real**; Tue Aug 18 → TCI ≈1.

**Why it ships uncapped** (measured, do not re-litigate without new numbers): interest is
`strength × weight`, and the live firehose puts 13F at the **bottom** of the funnel —
`edgar:13f_new_position` median interest **0.33** (n=23) against `edgar:activist_13d` **1.05**
(n=58, weight 1.5) and `edgar:form4_cluster_buy` **1.00** (n=24). The high-tier originators
therefore cannot be crowded out by 13F, so a cap protects nothing the weights don't already
protect; the only thing it would arbitrate is a near-tie with `edgar:form4_insider_buy` (0.34).
Against that, capping would make the 13F ledger's pre/post cohorts non-comparable. **If a
burst does drown the daily originators, `dropped_for_budget` in the manifest says so and it is
a one-line config change** before the November burst. The `budget.signal_family` machinery
stays regardless — it protects the confluence invariant, which is correct independent of caps.

Note the sharpest-ranking case, which is **pre-existing** behaviour: fund A opening + fund B
adding on one ticker sums interest to as much as `1.00 + 0.75 = 1.75`, topping `activist_13d`
(`INTEREST_CAP` is 10.0, so nothing clips it). Defensible — two marquee funds independently
increasing exposure is real information, and two funds both *opening* already sums to 2.0
today. The family collapse means it counts as one originator rather than confluence.

## Weekend finality-vs-cursor edge (8-K veto)

The EFTS day-cache freezes a day as FINAL by *calendar* fetch-age while the sweep cursor lags
by *session* days. If EFTS indexing lags in business days over a weekend, a late-indexed Friday
filing could be permanently missed. Look at real weekend data before trusting the lookback edge.

---

# 2. Funnel & discovery

## The digest never names the originator that surfaced a candidate (2026-08-10)

Hit for real by the user, who read the 2026-08-10 report and could not tell whether any name
came from WSB. One did — `wsb:novel | ACHR`, strength 0.30, which cleared the whole funnel and
landed at rank 4. The attribution exists only in `ScoutState.firehose` and the manifest, never
in the delivered report.

The digest line carries **flags** (advisory, computed during scoring), not originators:

```
4. ACHR  12.9  negative_fcf  recent_8k, passive_13g, planned_insider_sale_144, dilution, cash_burn
```

`activist_13d` is the sole accidental exception — it is both an originator name and a flag name,
so it shows up on a 13D-sourced line and makes that originator look like the only one that ever
contributes. Actively misleading, not merely absent.

Attribution for that run, for whoever picks this up: `edgar:activist_13d` 5 (ACR COCP HEPA VRME
YYAI) + `edgar:form4_insider_buy` 3 (ABTC BRVE ONMD) + `wsb:novel` 1 (ACHR) = the raw 9; the
investability floor took ONMD ACR COCP VRME YYAI, leaving BRVE ABTC HEPA ACHR.

Cheap fix (one tag per name in `build_report`), but it changes every digest line, so confirm the
wanted format first — and note a name can have several originators, which is exactly the
confluence case worth surfacing.

**Status:** gap confirmed against a real delivered report, nothing changed.

## `--demo` writes `mock:demo` rows into the PRODUCTION firehose (2026-08-10)

`shortlist-scout --demo` skips `mark_run_completed` but still calls `_log_firehose`, and it uses
the configured `state_path` — so the offline smoke test injects three synthetic rows (GEV,
GOOGL, LMT) into the live selection ledger. Present on **19 sessions** in
`/opt/shortlist/state/scout_state.json` from 2026-07-01 onward, including 2026-08-10.

The source is the installer itself: step 4/7 runs `--demo` as a deploy smoke test, so every
deploy adds a session's worth. Not a one-off operator mistake — it is wired in.

Probably inert (every consumer selects by signal name and nothing real is called `mock:demo`, so
the evaluator and the per-signal ledgers should never see them), but that is an assumption worth
checking before trusting any all-signals firehose count. Either have `--demo` refuse to write
firehose rows at all, or have the installer's smoke test point at a throwaway `state_path`.
Decide whether existing rows get purged or left as harmless archaeology.

**Status:** verified in prod state, nothing changed. Check the "inert" claim before purging.

## A single-axis composite can top the digest at 100.0 (2026-08-10)

Observed in a real run (session 2026-08-10, 4 names): **BRVE ranked #1 at composite 100.0** with
every axis abstained except one — `Qual· Moat· Grow· Value· Mom· Insdr· Risk100`, market cap
`$0M`, annotated `(thin)`, and `⊘ edgar, finra, wsb: supplied no usable data`.

Not a gate bug — `below_min_mktcap` tripped, so `passed` is false and it cannot rank into a
recommendation. It is a **presentation** defect: weight redistribution ("when a sub-score has no
inputs its weight is redistributed") reduces to `composite == risk` when risk is the *only*
scored axis, and 100.0 then heads the digest. The user's attention lands on the noisiest name in
the report, which is exactly what the funnel guards exist to prevent.

The seam is the `validity` block, not the gates: a floor on **how many axes must be scored**
before a composite is emitted at all (or before a card is allowed to sort above scored names).
`ScoreCard` already carries `confidence`/`abstentions`, so the input exists. Check first whether
`scored` is even true for a one-axis card — if it is, that is the narrower question to settle.
Measure how often this happens across the ledger before choosing a threshold; a single observed
instance does not size the rule.

**Status:** observed once in a live run, nothing changed. Needs a ledger-wide frequency count
before any `validity` threshold is chosen.

## A null `market_cap` bypasses BOTH the gate and the investability floor (2026-08-07)

`market_cap = None` is the input both guards need to reject a name, and both abstain on it:
- **The gate** — `scoring.py:627` needs `m.market_cap is not None`, so null ⇒ recorded
  `gated: false`.
- **The floor** — `investable.assess` abstains on a missing cap by design, *and* a symbol
  absent from the ~7,100-name Nasdaq screener gets no `Liquidity` row at all, which
  `funnel._apply_floor` treats as abstain ⇒ keep.

Mechanical, not a one-off: of 199 ledger picks, **13 have a null cap and every one is
`gated: false`** (`SOXL GLD VOO USO` ETFs, `FTECX VFLEX BBASX` mutual funds, plus `TM`, `YARW`,
`CSBA`); 18 of 199 (9%) are absent from the listed universe entirely. Note the interaction with
`50be4ed` (Finnhub non-USD abstention) — a correct fix that traded an *inflated* cap for a
*null* one, closing the wrong-number half of the TSM bug and leaving the silently-passes half.

**Sized 2026-08-08 against `.cache/nasdaq_universe/2026-08-07.json`** — the obvious fix is the
least valuable one:

| | count |
|---|---|
| picks with a null `market_cap` | 13 |
| of those, backfillable from the listed universe | **2** (TM, YARW) |
| absent from the universe — a backfill does nothing | 11 |
| would newly trip the $300M gate | **1** (YARW, $116M) |

So backfilling the cap changes the gate outcome for 1 pick in 199. The value is in the
absent-from-universe cases, and they are **one rule, not two** — every ETP *and* CSBA sits in
that bucket. Of the 18 absent, 7 were already gated and 4 were killed by the `X`-suffix rule,
leaving roughly **2 genuinely open (~1%)**: `GLD` (arrived via 13F) and `CSBA`.

**Deny lists are the wrong instrument.** There are five, one per signal, so junk must be
enumerated once *per originator* — exactly how GLD entered via 13F while denied for WSB. They
are reactive, and `buyback`'s must stay empty (a live-only knob whose backfill cohort ran
undenied). `_FIFTH_LETTER_SUFFIXES` can't help either: `GLD/VOO/USO/SOXL` are 3–4 letters.
*(Factual footnote: `USO` is the one already-recurred ETP not in the 14-entry
`scout.wsb_hype.deny_list`, hit twice via `wsb:hype`. **Do not fix it by enumerating `USO`** —
that is the whack-a-mole this paragraph rejects; it falls out of the universe rule for free.)*

The right seam is `funnel.apply_investable_floor` — it runs once per candidate regardless of
originator, and absence from the Nasdaq **stock** screener is precisely "not a listed common
stock". **This inverts a documented rule and needs the user's call, not a quiet flip**:
`investable.py`'s docstring reasons that such a symbol "is not a listed common stock we can
size", which is sound for declining to *size* it — but the funnel converts "can't size" into
"keep". Counter-risk: the same abstention deliberately protects foreign issuers, so measure the
drop set first, and guard it so one bad fetch from that undocumented API cannot delete real
picks.

**Status:** defect verified in code + ledger, nothing changed. The user was shown all of this
on 2026-08-08 and **declined to act for now** — reasonable, since supply, not filtering, is the
binding constraint. Do not re-raise as high-leverage without new evidence.

## Supply is the binding constraint — do not tighten in response to anything above

Raw candidates over the last 10 sessions: 21, 12, 16, 3, 6, 3, **0**, 13, 8, 7 (median ~7.5
against 15 slots); `dropped_for_budget: 0` on **8 of 10**; 8 of 12 signals disabled. On
2026-08-07 the investability floor dropped exactly two names, at $12M and $9M — correct drops
that were *not* what shortened the list. A market-cap pre-filter was measured before being
built and would have left **13 of 25 sessions with zero candidates**; it deletes the only names
there are. **Do not resolve thin supply by filtering the funnel harder** — that path is
measured and closed. New originators are judged on landing names in the **$0.3–10B band**, not
on row count.

## Enable `scout.quality_floor`? (still OFF)

**Its stated gate is now met**: `RunManifest.sec_requests` has three sessions, and `edgar_form4`
is 97.8–98.0% of the SEC draw in all three (930/950, 741/756, 569/582) — the 2026-08-04 cascade
is confirmed, not inferred.

What remains is the evidence quality, not the gate: the **5.2% slot-waste figure is
same-ledger** — the GIPR/COE false-positive guards were found on the very 135 picks the number
is computed from, with no held-out set, and SEC `frames` is **LIVE-ONLY** so it scores today's
fundamentals against historical picks. Consider shipping a firehose drop-log (the
`edgar:8k_negative` pattern) so the floor is killable on evidence once live.
Evidence: `docs/audits/2026-08-05-quality-floor-evidence.md`.

## `YahooScreenerSignal` — retire or replace (100% failure rate on this box)

Every originator is event-triggered; the only standing screen is WAF-blocked here, so empty
days are structural (no `min_candidates` or fallback universe anywhere in `daily.py`).
`api.nasdaq.com` screener and the Nasdaq Trader halts RSS both returned keyless `200`s from the
VPS.

⚠ **Do not hand-probe the Yahoo screener from the VPS** — doing so during the 2026-08-05 audit
tripped the WAF IP-wide and the `v8/finance/chart` price endpoint 429'd for minutes. That
endpoint feeds the entire scorer.

**Track B0 gates any standing-screen work**: reconcile against
`docs/audits/2026-08-05-standing-screen-data-source.md` §6, which says do **NOT** build a
standing full-universe originator. Plan of record for Tracks B/C:
`docs/audits/2026-08-06-discovery-breadth-plan.md`.

## Deferred originator ideas (recorded, not scheduled)

- **Materiality-scaled government-contract-award originator** (USAspending daily, award ≥ X% of
  TTM revenue) — matcher + source already exist.
- **FINRA short-interest:** a cleaner fund/ETF universe filter than the seed `deny_list` (the
  5th-letter drop misses 4-letter ETFs/CEFs); **from-zero ramps** (brand-new short positions,
  currently dropped by `min_prev_short_shares`) as a separate absolute-share variant.
- **13F:** material-**exits** as a negative-context veto (adds SHIPPED 2026-08-09,
  `docs/audits/2026-08-09-13f-material-adds-design.md`; exits need veto machinery and their own
  evidence, and a wrong veto silently deletes real picks from a supply-starved funnel), and
  trims on the same reasoning; a PiT CUSIP→symbology backfill cohort (live FTD files leak
  post-event symbols, so this is deferred by design).
- **13D:** a stake-**decrease**/exit negative-context signal; reweighting initial-13D strength by
  stake-% (needs ledger data first); extending the curated `scout/quality.py:_MARQUEE` alias map
  as new credible activists appear.

---

# 3. Evaluator & measurement

Ordering note: everything here answers an alpha question, which the prioritisation rule at the
top ranks *below* funnel work. Take these when they are cheap or when they unblock something.

## Open, small

- **Pre-register a `|high_frac − low_frac|` tolerance, then enforce it.** `double_sort` v1
  discloses per-bucket measurable fractions and deliberately does not enforce — inventing a
  threshold post-measurement is the exact sin pre-registration exists to prevent.
- **`random.Random` instead of the hand-rolled LCG** (`validate.py`). Real — glibc's LCG has
  known lattice structure in successive tuples and successive draws are used as indices — but
  cross-cutting to every seeded path and would churn fixtures, so it was kept out of a
  correctness PR.
- **Extract `backfill.py`'s eight-reason classifier into a shared leaf** + per-bucket reason
  counts. Reporting-only; the mechanical guard is already in.
- **Two backfill bugs from the 13D/A run** (recorded in
  `docs/audits/2026-07-19-13d-a-stake-increase-backfill-verdict.md`): a chunk-boundary overshoot
  (4 events dated past `window_end`), and `meta.adsh` **None** on backfill emissions (live
  emissions carry it) — which blocks dedup auditing of the 48 duplicate `(ticker, event_date)`
  keys.
- **Adjudicate the blocks-gate discrepancy.** The parent spec §7 wants **≥8** independent
  blocks; `preregister/edgar_activist_13d.yaml` pins `min_independent_blocks: 2`. Left
  unchanged deliberately — silently tightening an inference parameter after the fact is what
  the tamper guard exists to prevent. Needs a human call on which governs.

## Blocked on a price feed or on disk

- **Fresh-price re-run for `8k-neg` (50% price-covered) and `13d-a` (79%)** — they could not be
  measured from the cached snapshot in the 2026-08-03 re-derivation. Needs a **throttle on
  `fetch_history`** (currently an unthrottled serial loop; ~6k Yahoo requests would risk the IP
  the nightly scout depends on) or an off-hours window that cannot collide with the 22:30 timer.
  **Always check `.cache/famafrench` coverage FIRST** when replaying a cohort — a low-coverage
  cohort produces plausible-looking fractions that are pure artifact.
- **Delisting-imputation sensitivity re-run for `13d-a`.** The verdict was INSUFFICIENT but
  KILL-shaped (raw −1.99%/mo, scored −4.39%/mo); `delisting_by_reason` came back EMPTY so the
  prereg's `delisting_return: -0.55` was never applied and 393 unmeasurable events were
  **dropped, not imputed**. The drops skew toward **acquisitions** (the successful activist
  outcome), so measured alpha is plausibly biased **downward**. The defensible claim is
  **"no evidence to enable,"** NOT "proven value-destructive" — **do not wire a "KILL" config
  comment**. ~~Needs disk freed first~~ — **the disk constraint is GONE** (measured 2026-08-09:
  38 GB total, 22 GB used, **15 GB free** at 61%, against an 8 GB preflight floor). This item is
  now blocked on the price feed alone.
- **The Form 4 backfill leg is not wired.** `preregister/edgar_form4.yaml` is committed (so the
  anti-p-hacking guarantee holds) but no cohort has run. Unlike the other originators' pure
  per-chunk `assemble`, it needs a point-in-time `assemble_factory` — the DERA classification
  index must be built only from quarters **strictly before** each event's quarter, or future
  trading behaviour leaks into the routine/opportunistic label. Needs its own spec (fetch
  cadence, PiT index cost, cache shape). Meanwhile the live signal accrues evidence through the
  picks ledger + firehose: every emission carries its tier, so the **opportunistic-vs-unclassified
  within-cohort spread** is the statistic this data supports (absolute cohort levels are not
  trustworthy).
- **Momentum Stage 0 prize-bound re-run** on the full **80-name** largecap basket
  (`uv run python -m shortlist.backtest.prize_bound`) — the 2026-06-14 marginal PROCEED used a
  28-name subset, and a quota-starved run inflates momentum's effective weight. **Decision
  rule:** compare `mom_12_1`'s full-basket τ to the recorded **0.947** — holds or drops ⇒ the
  prize is real, write the Stage 1 plan; rises toward 1.0 ⇒ **stop**, momentum at 0.08 is a
  near-zero mover. Drop `mom_6m` either way (τ 0.995 vs the incumbent `rel_strength_6m`, fully
  redundant). Cost is ~1,000 FMP calls against the 250/day free cap — a scheduling/quota
  problem, not a host problem (`FMP_API_KEY` is present; Yahoo *is* reachable from oracle-prod).

## Snapshot-replay path: live, with two standing constraints (2026-08-09)

`--source snapshot` is un-gated and smoke-tested; the suppression question that gated it is
answered. What survives is the part that constrains *future* runs:

- **Horizon maturity.** Signals come from the store, forward returns from Yahoo `hists`, so
  the store's span bounds the *observation grid*, not the horizon. Only **h=1** has matured
  windows from the earliest captures; **h=3 needs ~late September 2026**. A 3-month replay
  before then is not a thin result, it is an empty one.
- **The 0% suppression result is large-cap-only.** 1642/1642 stored snapshots emit
  `composite` — but the store is 42 large caps, the population where confidence is highest.
  **Re-measure before trusting composite replay IC if accumulation widens to small/mid**,
  where `validity.min_scored_weight` (0.25) is far likelier to bind.

Reading trap: low breadth is **not** suppression — a 2026-06-22 grid date shows breadth 10
because only 10 tickers had been captured that early (42 by 2026-08-08).

**Status:** path live, no verdict attached. Next in this thread, in order:
1. **Finnhub `roiTTM` → `roic` proxy** (`data/sources/finnhub.py`): Return on *Investment*
   mapped into Return on Invested *Capital*, on the FMP-gated fallback path. The doc half is
   done (inline comment); the numerics half (keep the proxy vs drop to `None`) shifts
   quality/moat and needs a proxy-on-vs-off replay — null the `roic` where
   `provenance['fundamentals'] == ['finnhub']` and compare rank IC. The store already holds
   ~706 Finnhub-provenance `roic` snapshots across 43 days, exactly the population at issue.
   `--source xbrl` **cannot** measure this (it derives ROIC from companyfacts and never
   exercises the fallback).
2. **SUE** is blocked on calendar time only — a prereg-grade verdict (≥8 non-overlapping blocks)
   is a late-2026-into-2027 proposition. Keep accumulating.

**Lazy-Prices (`filing_text_change`) can never validate on this path** — full filing text was
deliberately kept out of the snapshot (`EdgarSource` fetches Form 4 + financials + filing-index
only). Measuring it needs a collector change to compute EDGAR text similarity into the
snapshot. Separate feature, not a waiting game.

## Other measurement gaps

- **Re-measure the `net_debt_to_ebitda` axis** on both committed universes. Every prior IC run —
  including the 2026-07-11 "leverage tilt NOT earned" verdict — scored negative-EBITDA names at
  the **top** of the inverted leverage band (they read as net cash) before the abstention fix.
  The verdict may stand, but it was measured on polluted data. One backtest command per universe.
- **Gate-impact measurement (`negative_fcf` excuse, scope B).** Gates are entirely unmeasured.
  Compare forward returns of *excused* (high-growth) vs *gated* negative-FCF names to test
  whether `revenue_cagr ≥ 0.15 ∧ persistence ≥ 0.70` beats a blanket gate. Needs new machinery
  (the XBRL source would have to evaluate gates, or a parallel cohort path).
  `docs/ASSESSMENT_GAPS.md` §2.7.
- **Selection-ledger forward-return analysis** — excess-over-SPY hit rate at 1/3/6/12m and the
  per-originator cohort split via the `catalyst` field. Calendar-gated, not effort-gated.
- **DEF 14A pay-vs-performance axis.** The **XBRL path is a NO-GO** (live-verified): SEC's XBRL
  APIs serve only `dei`/`us-gaap`, the `ecd` PvP tags are absent from companyfacts, and a
  `companyconcept/.../ecd/...` probe 404s. It can only be built via snapshot-replay once
  accumulation captures `research/proxy.py`'s PvP extraction point-in-time. Phase 2 also holds
  the narrative related-party/CD&A sections (no section splitter, ~350K-char raw text).
- **`dilution`-flag threshold review** instead of a `share_count` scored leg — the payoff is
  tail-concentrated, which suits a flag/screen better than a ranker.

---

# 4. Data layer

## FMP quota is ~2.7× over-subscribed — a config-or-money decision, not a build

Accumulate (42 tickers) + scout ≈ 676 calls/day against a 250/day free limit, which is why
**23 of 24 store dates have ZERO fmp-won statements** and EDGAR supplies 100% of production
statements. Options: drop `--max-tickers` to ~18; remove `fmp` from the accumulate chain (it
contributes nothing today); or paid **Starter** (~$14–20/mo).

The free window is **not calendar-UTC-day aligned** (measured 2026-07-31: still "Limit Reach"
42 min after UTC midnight, because our own 21:30 accumulate + 22:30 scout timers drained it).
Retry probes in the **mid-day UTC window**, never shortly after 22:30.

Three items unblock on this:
- **The live statements-merge before/after was never run.** The plan required a
  `shortlist --json` run on a real FMP-covered ticker showing `share_count_cagr`/`asset_growth`/
  `accruals` populated where the old merge returns null; both runs 429'd. The mechanism is
  covered by unit RED evidence plus a store-based offline re-merge on 17 real FMP-won snapshots
  (join key agrees on real data, including non-calendar fiscal years) — but the FMP-wins branch
  under a **live fetch** is still unexercised. Treat "recovered fields still null on a non-402
  name" as a bug against the fiscal-year join key, not a config problem.
- **The paid-plan flip**: with Starter, set `scout.daily_push.include_fmp: true` (or delete the
  key) → the digest uses the identical full chain as the bot, no code change.
- The momentum prize-bound re-run above.

## Should `fmp` outrank `edgar` for the insider transaction group? (design decision)

`config.yaml`'s `harness_sources` orders `fmp` before `edgar`, and `_merge_insider` takes the
coupled transaction facts **wholesale** from the first source with a present field — yet
`CLAUDE.md` calls EDGAR "the free authoritative source" for insider data. So enabling a paid
FMP Starter tier would silently override EDGAR's insider numbers. A priority/intent question,
deliberately left out of the FMP-insider classification fix; it becomes live the day the quota
decision above goes the paid way.

## EDGAR / statements minors

- **`get_shares_outstanding_diluted()` returns MCD's count in millions**, not absolute shares
  (`[716.4, 721.9, 732.3]`). Nothing depends on it for MCD any more, but any future consumer
  inherits the bug. (`diluted_shares` from the companyconcept fallback *is* absolute, so
  `financial_series` display mixes conventions — scoring is unaffected, `share_count_cagr` being
  scale-invariant.)
- **Widen the diluted-shares go/no-go beyond the store's 42 tickers** — keyless, costs only
  time, and it is the only thing that further reduces residual risk (another code review would
  not).
- **`_usable_years` (`data/models.py:510`) does not reject an all-`None` `fiscal_years` list.**
  Net observable behaviour is identical to rejection, so it is a docstring-vs-contract gap, not
  a wrong output.
- Parked observations: the `pe_ttm` fallback accepts negative EPS (harmless — `pe_vs_history()`
  guards `> 0` and `pe_ttm` isn't in `--json`); `bridge._close_near` has no max-gap bound (a
  short monthly history can pair a fiscal end with a months-away close);
  `Fundamentals.operating_margin`/`current_ratio` and `Statements.total_equity` are extracted
  but consumed nowhere; WSB `upvotes`/`rank_24h_ago` are captured but unused.

---

# 5. Code hygiene (fold in when next touching these files)

- **`scout/signals.py` (1,059 lines)** — candidate package split, the same one-module-per-thing
  pattern as the `data/sources/` split. Lower urgency: the classes are already cohesive.
- **Optional guardrail:** ruff `C901` with a `max-complexity` (or a soft line ceiling) so
  mega-functions can't silently regrow after a split. Its own small change, not bundled with a
  refactor.
- `_TRUE` is duplicated between `scout/dera.py` and `scout/insider.py`; `n_joint` counts tickers
  pre-filter while labelled "filings"; `edgar_index.fetch_daily_records`/`fetch_recent_records`
  are dead code with tests pinning them.
- An `isinstance` assertion-of-convenience in `tests/test_scout_insider_parse.py` exists only to
  satisfy ruff F401 — fold into a real assertion.
- Several run()-level tests (`test_scout_daily_research_gate`, `scout/test_digest_fmp_toggle`,
  `scout/test_fixes`, `scout/test_daily_push_flag`, `scout/test_orchestrator_integration`) read
  the repo-relative `scout/validate-latest.json`; add `monkeypatch.chdir(tmp_path)` when next
  touched.
- `docs/PLAN_EDGAR_DILUTED_SHARES.md`'s historical "Step 2"/"Step 3" code blocks quote the
  original signed-off text (including a false "ultimately ABSTAIN" safety claim). Each is
  followed by its `[R…]` correction so a linear reader is fine, but someone skimming would copy
  stale text — annotate them "superseded".
- Cosmetic nits from the sources-split review: a stray `# --- helpers ---` header was dropped; a
  few docstrings cross-reference symbols that moved to sibling modules.
- **Pin the dev Python via `.python-version`** — a fresh 3.11 venv fails
  `test_block_bootstrap_ci_*` on a floating-point boundary that 3.13 doesn't hit, so a fresh
  clone hits a spurious local failure.

---

# 6. Preserved record — pre-registration anchor (do not delete)

The 2026-07-01 registered spec is gitignored per convention, so the clauses the H2
immature-denominator correction rests on are excerpted **verbatim** here to survive in the
committed repo alone:

> §6.1: "Include an event in the cohort only when ≥ K forward data exists (H2)."
> §12: "fixed-horizon (H2): a 95-day-old event is excluded from the K=12m cohort (not measured early)."
> §6.1 (measurable, enumerated): "Non-measurable = no usable price series at all, or an
> unresolvable/ambiguous delisting." — calendar immaturity is neither.

Related standing adjudication: **on lifting 2025 coverage before `verdict_as_of`, WAIT** — a
targeted coverage push aimed at one vintage's floor is outcome-directed curation, the exact
pattern pre-registration exists to prevent. The vintage matures on its own by 2026-12-31.

---

# 7. Closed with a verdict — do not redo

One line each, so the next session doesn't re-derive them. Evidence is in `docs/audits/` and
the linked docs.

- **Five "obvious" refactors were measured and deliberately rejected** (PR #145): merging
  `assemble_eightk_events`/`assemble_buyback_events` (would silently change which events enter a
  *measured cohort*), splitting `bridge.snapshot_to_metrics` (order-dependent pipeline),
  splitting `extract_financials`/`panel_to_metrics` (transcription risk in numerics),
  genericizing the four `_load` double-checked locks, and extracting the
  `GovContractsSource`/`LobbyingSource` pagination loops. Revisit only with a specific reason
  *and* a measurement plan. `edgartools` `standard_concept` alias lists stay untouched
  (version-sensitive; they have broken accruals before).
- **WSB:** a per-ticker mention-ratio baseline and a market-cap ceiling were both measured and
  killed — do not rebuild either (`docs/audits/2026-08-07-wsb-novelty-rule.md`).
- **A market-cap pre-filter in the funnel** deletes the only names there are (13 of 25 sessions
  → zero candidates). Resolved instead by lowering `gates.min_market_cap` to $300M + the
  investability floor.
- **The 13D entry-price size-band re-run is retired, not parked.** It waited on the
  `calendar_time_portfolio` Jensen fix, which landed in #151, so it is technically runnable —
  but the same audit's later finding (below) makes a band on the *raw* 13D cohort a raw-level
  analysis, and the prioritisation rule ranks it under funnel work. Reinstate only if the
  raw-vs-scored attrition finding is overturned.
- **Cohort levels are structurally unmeasurable on free data** (outcome-correlated attrition;
  22% of events have no price series, monotonic in age). Do **not** build the ABK/value-weighting
  correction; never quote a RAW-cohort alpha; scored-cohort levels *are* usable. No data
  purchase indicated.
- **`edgar_index_daily_cap` stays 2500** (never binds; peak 37%; lowering truncates a
  *structured* prefix, so any future time-budget cutoff must shuffle or relevance-sort first).
  **Do not give a signal its own `SecThrottle`** — a per-signal throttle cannot bound the
  process's request rate, which is exactly how the 2026-08-04 cascade happened. Concurrency buys
  nothing here (~17 ms latency; one serial worker already sustains ~57 req/s).
- **`accruals` stays disabled** — re-measured on both reproducible universes 2026-07-18,
  reproducing the 07-12 table bit-for-bit. The 195-name universe that once earned it is
  permanently unreproducible. Nothing left to measure.
- **The three §2 price-refinement axes are measured and parked** — `pct_to_52w_high` and
  `vol_scaled_momentum` duplicate scored legs; `max_daily_return` is orthogonal but its sign
  flips across universes. **EV/EBIT** is a don't-ship (corr 0.55–0.72 with `fcf_yield`, no
  incremental IC). **`share_count`**, **`asset_growth`**, **`shareholder_yield`** and
  **`piotroski`** all failed the XS bar.
- **The FINRA backfill leg was not built** — the audit spike measured a pooled measurable
  fraction of 0.806 against a pre-registered 0.90 bar (DEFER, mechanical not borderline).
- **`shortlist-backtest --fit` cannot tune the live unfitted priors.** It fits **only** the four
  fundamental composite-axis weights (`quality, moat, growth, value`), requires `--source xbrl`,
  and proposes only (never writes `config.yaml`) — so it does not touch the
  `thresholds.accruals`/`thresholds.residual_momentum` bands, and `momentum` isn't a fit axis at
  all. Manual band review against measured IC is the only route, and only if the live legs
  misbehave.
- **Two committed double-sort spread claims are RETRACTED** (13D and 13D/A both now span zero);
  8-K still excludes zero. `docs/audits/2026-08-03-evaluator-rederivation.md` is the current
  record — quote it, not the older audits.

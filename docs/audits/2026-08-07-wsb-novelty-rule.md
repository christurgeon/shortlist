# WSB re-enable — rank-novelty replaces the velocity rule (2026-08-07, corrected 2026-08-08)

**What this is:** the evidence and design behind re-enabling `wsb_hype` as a discovery
originator, under a new **rank-novelty** qualification rule emitting the distinct key
`wsb:novel`, plus a **contention-triggered per-originator slot cap** in `budget.select`.

Committed here rather than under the gitignored `docs/superpowers/specs/` per the CLAUDE.md
"commit the evidence" rule — that path is how two enablement artifacts already evaporated.

**Reproduce every number below:** `python3 docs/audits/scripts/wsb_novelty_replay.py`.
Inputs are **committed** under `docs/audits/raw-2026-08-07-wsb/` (43 distilled daily boards
+ a market-cap snapshot, 288K), so this runs on a fresh clone with no network and no cache.

> **⚠ THIS DOCUMENT WAS CORRECTED ON 2026-08-08 AFTER REVIEW.** Its first revision claimed
> the novelty rule produced **0% mega-caps**. That was an artifact of scoring composition
> against a hand-written 25-name list rather than real market caps: CAT ($387B), UNH
> ($370B), GEV, ANET and NVO were all absent from the list and scored as "clean". On real
> caps the figure is **15%**, not 0%. It also claimed the velocity filter was "the entire
> mega-cap bias"; measured, it accounts for **12%** of novelty emissions. Both claims are
> corrected below. The rule is still a large composition improvement — just not the one
> the first revision advertised.

---

## 1. Why this exists

`wsb_hype` was demoted to `enabled: false` on 2026-07-26 for **crowd-out, not harm**
(`docs/audits/2026-07-26-funnel-composition-audit.md` §1): 60% of 141 ledger picks at a
$310B median market cap, consuming ~4 of the then-10 nightly deep-screen slots. Its own
realized performance (−0.6% vs SPY, n=82) sits mid-pack against the originators that stayed
**on** (`edgar:form4` −0.5% n=17, `edgar:activist_13d` −0.8% n=26).

Two caveats on those inherited figures: `daily_x` is now **15**, not 10, so the slot-share
argument is weaker than it was; and the $310B median predates the **2026-08-06 Finnhub
non-USD market-cap fix**, so ADR caps inside it are inflated.

Turning the signal off removed the funnel's **only non-event discovery channel** — every
other originator requires something to have been *filed*.

## 2. The measurement

43 daily ApeWisdom boards (2026-06-07 → 2026-08-08), top-100 tickers each. The shipped
`deny_list` is applied to **every** rule so the comparison is like-for-like. Market caps are
real (the `nasdaq_universe` snapshot), never a hand-maintained name list.

**Denominators are not interchangeable and both are reported.** *Per calendar day* divides
by days with enough prior history to evaluate; *per emitting day* by days that emitted.

| rule | emis | /cal day | /emit day | median cap | ≥$200B | **$0.3–10B band** |
|---|---|---|---|---|---|---|
| current velocity (shipped) | 348 | 8.1 | 8.1 | $308.7B | 53% | **8%** |
| median-baseline *(killed, §3)* | 283 | 7.4 | 7.6 | $179.5B | 48% | 16% |
| **rank-novelty (14/50/20)** | 42 | 1.1 | 2.1 | **$34.3B** | **15%** | **39%** |

**The headline is the last column.** The 2026-07-26 audit's actual complaint was that
"almost nothing lands in the $0.3–10B band where a retail-sized book can be early." This
takes that band from **8% to 39%** and cuts the median selected cap **9×**. It does *not*
eliminate large caps, and the remaining 15% is not noise — it is names like CAT and UNH that
are genuinely not WSB regulars.

## 3. A design was proposed, measured, and killed

The first proposal was a **per-ticker mention baseline**: qualify a spike relative to the
ticker's *own* trailing median, assuming mega-cap chatter is a stable high plateau a
self-relative ratio would normalize away.

**It barely moves composition** (median $179.5B vs the current $308.7B; ≥$200B 48% vs 53%)
because the premise is wrong. Mega-cap mention counts are a **volatile** plateau:

| ticker | median ratio to own 14d median | days exceeding 2× |
|---|---|---|
| **AAPL** | 1.33 | **14/38 (37%)** |
| TSLA | 0.95 | 6/38 (16%) |
| MSFT | 0.89 | 6/38 (16%) |
| NVDA | 1.06 | 6/38 (16%) |

A ratio rule fires on AAPL on more than a third of all days. **Do not rebuild this design.**

## 4. The velocity filter is *a* cause, not *the* cause

`WsbHypeSignal.scan()` requires `mention_delta_pct is not None`, which
`apewisdom.parse_wsb` sets only when `mentions_24h_ago` is non-zero. This genuinely does
discard a real population — but it is **not** "every ticker absent from yesterday's board",
and correcting that matters because the first revision built its whole story on it:

```
board-absent AND no baseline (truly unemittable): 186
board-absent BUT carrying a baseline:             707
```

ApeWisdom tracks ~784 tickers across 8 pages; **we cache page 1 only**, so a ticker missing
from *our* board usually still has a valid 24h baseline in *their* data. The genuinely
unemittable population contributes **5 of 42 (12%)** of novelty emissions.

So the filter is a real defect worth removing on its own merits, and it is **not** where the
composition improvement comes from. The working mechanism is rank-regularity (§5). Note the
novelty rule changes several things at once — qualification axis, mention floor (30→20),
sort key (delta→mentions), deny list — so no single one of them owns the result.

## 5. The mechanism — rank-regularity

Qualify a ticker only when its **best rank across the prior 14 boards is worse than 50**, or
it was absent from all of them. Rank succeeds where a mention ratio fails because rank is
**relative to the board**: mega-caps occupy the top ~30 regardless of news, so
rank-regularity excludes the perennials close to deterministically.

**On the parameter sweep — read this as a mechanism check, NOT a generalisation test.**

| (lookback, max_rank, min_mentions) | tune ≥$200B | holdout ≥$200B | holdout /emit day |
|---|---|---|---|
| (14, 30, 20) | 29% | 14% | 2.9 |
| **(14, 50, 20)** | **12%** | **16%** | **2.8** |
| (14, 75, 20) | 0% | 8% | 1.7 |
| (21, 50, 20) | 12% | 19% | 2.6 |

Splitting into tune/holdout halves does **not** validate generalisation here: the perennials
are structurally ineligible in *both* halves (AAPL, MSFT, NVDA, TSLA and the rest carry a
prior-best rank ≤ 50 on **every** day of the window), so the two halves re-measure the same
deterministic exclusion. What the sweep does show is that the outcome is **not fragile to
the parameters** — every `max_rank ≥ 50` cell lands in a similar band on both halves.

`(14, 50, 20)` was chosen for the volume/composition trade, not because it won a contest;
several cells are close, and stricter `max_rank` buys lower cap share at ~40% less volume.

**Sparse-history sensitivity.** The cache has gaps, so a nominal 14-day window is often
fewer boards. Thinner history means fewer tickers look like regulars:

| prior boards | days | emissions | /cal day | ≥$200B |
|---|---|---|---|---|
| 5–7 | 3 | 6 | 2.0 | 20% |
| 8–10 | 3 | 6 | 2.0 | 17% |
| 11–14 | 32 | 30 | 0.9 | 13% |

Thin history is **more permissive on volume and modestly worse on composition**. The thin
buckets are 3 days each — directional only. Mitigated by `min_history_days` (abstain below
5 boards) and by the slot cap bounding any volume surprise.

## 6. What is NOT claimed

- **Composition was measured. Value was not.** The rule surfaces names with a
  retail-lottery profile (`LCID HTZ SOUN DJT SELF SLS PENG ONDS APLD`) that Bali's
  MAX-effect literature associates with *under*performance — though note that is 9 of 41
  uniques, and the median emission is a **$34B** company (CAT, UNH, NKE, UPS, PYPL, ANET,
  TEAM, DDOG, NET). This trades a measured-mediocre signal for an unmeasured one that could
  be worse. **Do not present this as a quality fix.**
- **No forward-return validation.** `scoring.score()` is untouched — this is discovery
  plumbing, shipping as a prior. `scout/preregister/wsb_novelty.yaml` fixes the bar for
  judging it before any return is seen.
- **Shipping ENABLED is a deliberate exception to this repo's contested-prior precedent**
  (`finra_short_interest`, `edgar_8k`, `edgar_buyback` all ship disabled behind a prereg).
  Recorded as the owner's call, not an oversight, on the grounds that the measured claim is
  about composition rather than returns. The prereg makes the cohort separable regardless.
- **The slot cap did not fix the crowd-out.** At ~2 emissions/emitting day WSB cannot reach
  `max_slots: 5`, and `after_prefilter > 15` on only **2 of 25** production sessions. It is
  a general guard for the next noisy originator. Credit the novelty rule, not the cap.
- **`max_slots: 5`, and every novelty threshold, are unfitted priors.**

## 7. Design as built

### 7.1 Units

| unit | kind | responsibility |
|---|---|---|
| `data/apewisdom.py: read_cached_boards` | I/O leaf | read prior day files already on disk; never fetch, never raise, skip corrupt |
| `scout/wsb_novelty.py` | **pure leaf** | `board_regulars` / `assess` / `qualify_board`. No I/O |
| `WsbHypeSignal._scan_novelty` | signal | wires the two; emits `wsb:novel` |
| `budget.select` | pure | contention-triggered per-originator cap |
| `daily.py` | orchestrator | builds `caps_by_signal`, names capped drops |

**No `ScoutState` change** — the baseline derives from the cache the scout already writes,
so there is no new persisted shape, no forward-compat surface, no concurrent-write hazard.

### 7.2 A distinct emission key

Emissions are `wsb:novel`, **not** `wsb:hype`. The velocity rule's ~82 live picks are
already pooled under `wsb:hype` in the firehose and the selection ledger; reusing the key
would blend two populations into one cohort and make the ledger permanently unable to
measure either. Same reasoning that gave the 8-K negative veto its own `edgar:8k_negative`.

`weights_by_signal` and `caps_by_signal` are both built from the emissions actually produced
(`daily.py:_scan_discovery`), keyed by the emission string — so changing the key does not
silently drop the signal to a default weight.

### 7.3 The slot cap

1. `len(candidates) <= daily_x` → not applied at all; no contention to arbitrate.
2. Otherwise walk in interest order. **≥2 distinct discovery signals ⇒ exempt** (confluence).
   Otherwise charge the single discovery signal; if its quota is spent, defer.
3. Remaining slots are **backfilled** from the deferred list — a cap never wastes a slot.

Consequences stated plainly: the cap is a **re-ordering of the drop set, not a hard quota**.
An originator supplying every candidate on a quiet night still takes every slot. It gives
*other* originators first refusal on slots beyond the quota, and returns them if unclaimed.

Counting uses `is_discovery` emissions and **distinct signal strings**: boosters run before
`select`, and 13F emits once per fund, so two funds on one ticker is not confluence.

### 7.4 Drop reporting

`dropped_for_budget` keeps its meaning (everything not chosen). A new `RunManifest.capped`
counts only names the cap **displaced** — those the uncapped ranking would have screened —
and each is named like the floors' drops:

```
BUDGET CAP: TSLA dropped — wsb:novel quota 5 (interest 0.42)
```

Names merely ranked below the cut are *not* reported as cap drops (pinned by test).

### 7.5 Error handling

Fewer than `min_history_days` (5) readable boards → the rule **abstains, emitting nothing**,
and reports `ran=True` with a stated reason. Two deliberate choices:

- It does **not** fall back to the velocity rule — that would reinstate the composition it
  exists to fix, on exactly the runs nobody is watching.
- `ran=True` because `models.run_health` treats an enabled discovery signal with `ran=False`
  as a *failed* originator and marks the whole run degraded; a cold cache would otherwise
  report every run as broken for `min_history_days` sessions.

### 7.6 Config and absence contracts

Removing `scout.wsb_hype.novelty` restores the byte-identical velocity signal; removing
every `max_slots` makes `select` byte-identical to the uncapped ranking. Both are pinned by
tests whose doubles **raise** if the new path is touched, so they cannot pass as tautologies.

`cache_dir` is config-driven: with a cwd-relative default, running the scout from another
directory would silently see no history and abstain.

---

## 8. Status

Implemented and merged on this branch; `wsb_hype` ships `enabled: true, weight: 0.5,
max_slots: 5`. The shipped `qualify_board` was cross-checked against this document's replay
script on all 43 days — **they agree exactly**. Forward returns accrue to
`scout/preregister/wsb_novelty.yaml`; nothing here measures them.

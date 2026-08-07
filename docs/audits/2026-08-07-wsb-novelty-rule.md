# WSB re-enable — the mega-cap bias is a code defect, not a threshold choice (2026-08-07)

**What this is:** the evidence and design behind re-enabling `wsb_hype` as a discovery
originator. Two independent changes: a **rank-novelty qualification rule** for the signal, and
a **contention-triggered per-originator slot cap** in `budget.select`.

Committed here rather than under the gitignored `docs/superpowers/specs/` per the CLAUDE.md
"commit the evidence" rule — that path is how two enablement artifacts already evaporated.

**Reproduce every number below:** `python3 docs/audits/scripts/wsb_novelty_replay.py`
(stdlib only, no network, reads the `.cache/apewisdom/` day files the scout already writes).

---

## 1. Why this exists

`wsb_hype` was demoted to `enabled: false` on 2026-07-26 for **crowd-out, not harm**
(`docs/audits/2026-07-26-funnel-composition-audit.md` §1): 60% of 141 ledger picks at a
**$310B median market cap**, consuming ~4 of 10 nightly deep-screen slots. Its own realized
performance (−0.6% vs SPY, n=82) sits mid-pack against the originators that stayed **on**
(`edgar:form4` −0.5% n=17, `edgar:activist_13d` −0.8% n=26).

Turning it off removed the funnel's **only non-event discovery channel** — every other
originator requires something to have been *filed*. The goal here is to keep that channel
while removing the composition defect.

## 2. The measurement

42 cached daily ApeWisdom boards (2026-06-07 → 2026-08-07), top-100 tickers each.

**Denominators are not interchangeable and both are reported.** *Per calendar day* divides by
days with enough prior history to evaluate; *per emitting day* divides by days that produced
at least one emission. An earlier draft of this work mixed the two.

| rule | emissions | /cal day | /emit day | mega-cap share |
|---|---|---|---|---|
| current (`delta >= +50%`) | 361 | 8.6 | 8.6 | **40%** |
| median-baseline *(killed, see §3)* | 294 | 7.9 | 8.2 | **39%** |
| **rank-novelty (14d, rank > 50, ≥20 mentions)** | 43 | 1.2 | 2.1 | **0%** |

"Mega-cap" is a fixed hand-maintained name list in the replay script, not a live market-cap
lookup — deliberately, so the script stays reproducible offline.

## 3. A design was proposed, measured, and killed

The first proposal was a **per-ticker mention baseline**: qualify a spike relative to that
ticker's *own* trailing median, on the theory that mega-cap chatter is a stable high plateau a
self-relative ratio would normalize away.

**It does not work — 39% vs the current 40%, and AAPL got *more* frequent under it.** The
premise was wrong. Mega-cap mention counts are a **volatile** high plateau, not a stable one:

| ticker | median ratio to own 14d median | days exceeding 2× |
|---|---|---|
| **AAPL** | 1.33 | **14/37 (38%)** |
| TSLA | 0.98 | 6/37 (16%) |
| MSFT | 0.90 | 6/37 (16%) |
| NVDA | 1.07 | 6/37 (16%) |

A ratio rule fires on AAPL on more than a third of all days. **Do not rebuild this design.**

## 4. Root cause — the defect is one line of code

`WsbHypeSignal.scan()` requires `mention_delta_pct is not None`, and
`data/apewisdom.py:parse_wsb` only sets that when `mentions_24h_ago` is non-zero. **A ticker
absent from yesterday's board is therefore structurally unemittable.** The signal documents
this as intentional:

> Discovery requires a measurable 24h baseline (mention_delta_pct is not None): unlike the
> advisory social_hype flag, a brand-new spike with no baseline is NOT surfaced here —
> discovery needs evidence of velocity, not just volume.

Measured cost of that choice: **~7.2 tickers/day** arrive with no prior-day baseline. Of those
clearing 20 mentions (n=9 over the window — small, treat as directional), the **median arrival
rank is 19** — hot on arrival, straight onto the board, and every one discarded.

The signal can only emit names that were *already there*, which is the definition of a board
regular. That is the entire mega-cap bias. It is not a threshold that was set too low.

## 5. What works — rank-novelty

Qualify a ticker only when its **best rank across the prior 14 boards is worse than 50**, or it
was absent from all of them — i.e. it is *not a board regular* — plus a ≥20 mention floor.

Rank succeeds where the mention ratio failed because **rank is relative to the board**:
mega-caps occupy the top ~30 regardless of news, so rank-regularity is a robust perennial
detector while mention-count ratios are not.

**Holdout (tune on the first 21 days, verify on the last 21):**

| (lookback, max_rank, min_mentions) | tune mega% | **holdout mega%** | holdout /emit day |
|---|---|---|---|
| (14, 30, 20) | 6% | 2% | 3.0 |
| (14, 30, 30) | 11% | 4% | 1.9 |
| **(14, 50, 20)** | **0%** | **0%** | **2.9** |
| (14, 50, 30) | 0% | 0% | 1.9 |
| (14, 75, 20) | 0% | 0% | 1.7 |
| (21, 50, 20) | 0% | 0% | 2.8 |

The winner selected on the tune half **alone** is `(14, 50, 20)`, and it reproduces at **0%
out of sample**. Every configuration with `max_rank >= 50` is clean on **both** halves — the
parameter surface is flat, so this is not a tuned artifact. Only `max_rank = 30` leaks.

This matters because the first version of this analysis picked parameters by sweeping 12
configurations over all 42 days and quoted the winning cell as though it were a validation. It
was in-sample. The holdout above is what makes the number quotable.

**Sparse-history sensitivity.** The cache has gaps, so a nominal 14-day window is often fewer
boards. Thinner history means fewer tickers look like regulars, which makes the rule **more
permissive**:

| prior boards | days | emissions | /cal day | mega% |
|---|---|---|---|---|
| 5–7 | 3 | 6 | 2.0 | 0% |
| 8–10 | 3 | 6 | 2.0 | 0% |
| 11–14 | 31 | 31 | 1.0 | 0% |

Volume roughly doubles on thin history; **purity is unaffected (0% in every bucket)**. The
thin buckets are 3 days each — directional, not conclusive. Mitigated by `min_history_days`
(abstain below 5 boards) and by the slot cap bounding any volume surprise.

## 6. What is NOT claimed

- **Composition was measured. Value was not.** 0% mega-cap says the rule stops surfacing names
  where no edge exists. It does **not** say the names it surfaces instead are good. Its actual
  output includes `LCID HTZ SOUN DJT SELF SLS PENG ONDS APLD` — a retail-lottery profile that
  Bali's MAX-effect literature associates with *under*performance. This trades a
  measured-mediocre signal for an unmeasured one that could be worse. The investability floor
  and the scorer are the downstream skeptics; the picks ledger settles it over calendar time.
  **Do not present this as a quality fix.**
- **No forward-return validation, and none is proposed as a gate.** This is discovery
  plumbing — `scoring.score()` is untouched — so it ships as a prior under `AUTONOMOUS_SCOUT.md`
  §9, not behind the rank-IC bar new *scoring* legs must clear.
- **The slot cap did not fix the crowd-out.** At 2.1 emissions/emitting day the WSB cap will
  essentially never bind. Its value is as a general mechanism for the next noisy originator.
  Credit the novelty rule, not the cap.
- **`max_slots: 5` is an unfitted prior.** Nothing measures it.

## 7. Design

### 7.1 Units

| unit | kind | responsibility |
|---|---|---|
| `data/apewisdom.py` (extended) | I/O leaf | `read_cached_boards(cache_dir, before, lookback_days)` — read day files already on disk; never fetch, never raise, skip missing/corrupt days |
| `scout/wsb_novelty.py` | **new pure leaf** | `board_regulars(boards, max_regular_rank) -> set[str]` + the qualification predicate. No I/O |
| `WsbHypeSignal` | signal | wires the two |
| `budget.select` (extended) | pure | contention-triggered per-originator cap |
| `daily.py` | orchestrator | names the capped drops |

**No `ScoutState` change.** The baseline is derived from the cache the scout already writes, so
there is no new persisted shape, no forward-compat surface, and no concurrent-write hazard.
Precedent: the scout FINRA fetcher already shares the harness disk cache.

### 7.2 The slot cap

The cap **never wastes a slot**:

1. `len(candidates) <= daily_x` → no cap applied at all. No contention, nothing to arbitrate.
2. Otherwise walk in interest order. A candidate with **≥2 discovery emissions is exempt**
   (confluence is the strongest thing this funnel finds and must not be deleted by a quota).
   Otherwise it charges to its single discovery signal; if that quota is spent, set it aside.
3. Slots remaining after the walk are **backfilled** from the set-aside list in interest order.

So the cap does not remove a noisy originator's names — it gives every *other* originator first
refusal on slots beyond the quota, and returns them if nobody takes them.

**Counting uses `is_discovery` emissions only.** Boosters run *before* `select`
(`daily.py:776`), so a naive count would read a booster as an originator.

### 7.3 Drop reporting

`dropped_for_budget` is currently a **bare count** — the only drop in the funnel without a
per-name reason, defensible while the sole reason was "ranked below the cut". The cap makes it
heterogeneous: a name can now be dropped while ranked *above* a kept name. So `select` returns
the capped candidates and `daily.py` names them, matching the floors:

```
BUDGET CAP: TSLA dropped — wsb:hype quota 5/5 (interest 0.42)
```

`RunManifest` gains a `capped` counter appended as the **last defaulted field**, per the
`vetoed` / `sec_requests` convention.

### 7.4 Error handling

Fewer than `min_history_days` (5) readable boards → the novelty rule **abstains and the signal
emits nothing**, loudly, via `available()`. It deliberately does **not** fall back to the old
delta rule: that would silently reinstate a 40% mega-cap flood on exactly the runs nobody is
watching. WSB emitting nothing is safe — the event originators carry the night.

### 7.5 Config and absence contracts

The **target** state below. The `enabled: true` flip is deliberately the *last* step and is
not part of the implementation commit — see §8.

```yaml
scout:
  signals:
    wsb_hype: {enabled: true, weight: 0.5, max_slots: 5}
  wsb_hype:
    novelty:
      enabled: true
      lookback_days: 14
      max_regular_rank: 50
      min_mentions: 20
      min_history_days: 5
```

- No `max_slots` on any signal → **byte-identical `select`**, zero behaviour change.
- No `novelty` block → **byte-identical signal** (current delta rule).
- `deny_list` gains the leveraged index products `SOXL TQQQ SQQQ UVXY` — `SOXL` alone appeared
  14 times in the replay and is absent from the shipped list.

Both absence contracts pinned by tests.

### 7.6 Testing

- **Pure novelty leaf:** regular-detection, absence-as-novel, deny list, mention floor,
  `min_history_days` abstention.
- **`select`:** no caps ⇒ byte-identical to today; cap does not apply at/below `daily_x`;
  confluence exemption; backfill never wastes a slot; capped names returned for naming.
- **Absence contracts:** both, with doubles that **raise** if touched, so the assertions cannot
  pass as tautologies (`tests/test_scout_investable_wiring.py` precedent).
- **Manifest:** `capped` round-trips and defaults to 0 when omitted
  (`tests/test_scout_funnel_veto.py:52` template).

---

## 8. Status

Design approved 2026-08-07. Evidence and replay script committed. Implementation follows in the
same branch; `wsb_hype` remains `enabled: false` until the implementation lands and is reviewed.

# 13F material-adds originator — design (2026-08-09)

**Status:** approved by the owner 2026-08-09. Ships **ENABLED** at weight 0.75.

**Why this lives in `docs/audits/` and not `docs/superpowers/specs/`.** The brainstorming
skill defaults specs to `docs/superpowers/specs/`, which is gitignored (`.gitignore:37`,
0 tracked files) and where two enablement artifacts have already evaporated. `CLAUDE.md`'s
design premise requires that anything which **ships enabled and moves live selection** carry
its reasoning in the tracked tree. This signal does, so the spec is tracked.

---

## 1. What this builds, and the one number that justifies the timing

`EdgarThirteenFSignal` surfaces marquee-fund **new** positions. `new_position_diff`
(`scout/thirteenf.py:125`) states the scope limit outright: *"Material ADDS to existing
positions are out of scope (v1: new positions are the sharpest best-idea event)."*

This adds the adds. The motivation is **supply**, which is the binding constraint on the
funnel: the 2026-08-07 session ran `raw 7 → screened 3` with `dropped_for_budget: 0` against
**15** deep-screen slots. `TODO.md`'s prioritisation rule ranks work that improves what
*feeds* the funnel above work that measures forward returns, and explicitly warns against
resolving thin supply by filtering harder.

### 1.1 The deadline is mechanical, not a preference

`_mark_processed` (`signals.py:961`) burns a fund's accession **permanently** once its diff
runs — including on an empty diff, deliberately, so an empty-diff fund does not re-download
both infotables every night. `ScoutState.thirteenf_seen_accessions` persists it.

Therefore: **if the nightly scout processes a fund's Q2 13F before this ships, that quarter's
material adds are unrecoverable until the Q3 filings in November.** Q2 2026 filings are due
**2026-08-14** (45 days after the 2026-06-30 quarter end) and funds may file early. Q1
filings all landed 2026-05-14/15 (`config.yaml:764`).

This is the entire reason the work is scheduled now rather than alongside the other open
items. It is not an alpha claim.

---

## 2. The correctness spine: shares detect, value sizes

**A value-based add detector is confounded by price.** 13F `<value>` is market value at
quarter end. A position whose stock rose 50% with **zero shares bought** shows a ~50%
book-weight increase — indistinguishable from conviction. Detecting adds on weight or value
would surface momentum, mislabelled as fund conviction.

The only price-independent quantity in the filing is the share count, `<sshPrnamt>`. It is
present in every real infotable (and in the existing test fixture,
`tests/test_scout_signals_thirteenf.py:27`) and **`parse_infotable` currently discards it** —
it captures `sshPrnamtType` (to reject PRN convertible-debt rows) but not the amount.

So:

| purpose | quantity | why |
|---|---|---|
| **detect** an add | `shares` (`sshPrnamt`) | price-independent; only changes when the fund trades |
| **size** the emission | `value` book weight | conviction is a share of the book, which is a value concept |

### 2.1 Accepted limitation — stock splits produce false adds

A 2:1 split doubles `sshPrnamt` with no purchase and roughly flat book weight, so it clears a
share-ratio bar. Not solved in v1, for three reasons: splits are rare across a 7-fund
concentrated book; the false positive surfaces a name **a marquee fund already holds**, so the
cost is a wasted slot rather than junk in the digest; and a ratio-clustering guard (flag
ratios near 2.0/3.0/1.5 with near-flat weight) is speculative machinery for an unmeasured
failure rate. **Document it, count it later from the firehose if it turns out to matter.**

Deliberately NOT mitigated by "require shares up AND weight up" — a genuine add during a
price decline shows shares up and weight down, so that rule would drop real signal to catch a
rare artifact.

---

## 3. Thresholds and strength

```
shares_latest / shares_prior >= 1.50        # material_add_ratio
AND  resulting book weight   >= 0.005       # reuses min_position_pct
```

**+50%** (owner's call): a half-again increase is unambiguous accumulation rather than a
rebalance, and it keeps emission volume low enough that adds fill *slack* during the quarterly
burst instead of dominating it.

**Strength treats the increment as the bet:**

```
strength = min(1.0, delta_weight / full_strength_pct)   # delta_weight = w_latest - w_prior
```

A fund adding 5% of book is a full-conviction new bet; a nibble on top of an existing stake is
weak. Reuses the existing `full_strength_pct: 0.05` knob and needs no new calibration. Note
`delta_weight` can be **negative** (shares up, price down) — clamp at 0.0 so strength stays in
`[0, 1]`; such a name still emits, at floor strength, because the share purchase is the signal.

**Weight 0.75**, below new positions' 1.0. The prior is real but weaker: Cohen-Polk-Silli
(2010) "best ideas" is about large-weight positions, and an add moves a holding *toward* that
territory — it is not the fresh best-idea event Martin-Puthenpurackal (2008) replication
measures. Recorded as the owner's deliberate bend of the contested-prior-ships-disabled
precedent, mitigated by the lower weight, the stricter bar, and `max_slots: 4`.

**No prereg YAML.** `edgar_13f` has none either (`scout/preregister/` has 7 files, no
`edgar_13f.yaml`) because its backfill is deferred by design — a point-in-time CUSIP→symbology
replay would leak post-event symbols through today's FTD files. The same blocker applies
identically here, so shipping without a prereg is consistent with the parent signal rather
than an exception to it. Evidence accrues through the picks ledger + firehose.

---

## 4. Units

Five changes. Only the first three are new logic; the last two are seams that exist because
of how `daily.py` and `budget.py` are keyed today (§4.4, §4.5).

### 4.1 `parse_infotable` — capture shares

Add `"shares": float | None` to each row dict, parsed from `sshPrnamt` the same way `value`
is parsed from `value` (comma-stripped, `ValueError → None`). Namespace-agnostic local-tag
matching already handles it; `sshPrnamt` and `sshPrnamtType` are siblings under
`shrsOrPrnAmt` and both fall out of the existing `it.iter()` walk.

Additive to the row dict. No existing consumer reads unknown keys.

### 4.2 `aggregate_positions` — sum shares

Sum `shares` alongside `value` across the multiple `<infoTable>` rows one holding legitimately
spans (sole/shared/none voting splits, combined-manager filings). A row with `shares is None`
contributes 0 to the sum but must not poison the total — track whether *any* row supplied a
usable share count, and leave the aggregate `shares` as `None` when none did, so §4.3 can
abstain rather than read a missing count as zero.

**`value` behaviour must not change at all**, including the existing option/PRN row drops.

### 4.3 `material_add_diff` — new pure function

```python
def material_add_diff(latest, prior, *, min_position_pct=0.005,
                      full_strength_pct=0.05,
                      material_add_ratio=1.50) -> tuple[list[dict], int]
```

Returns `(adds, n_abstained)` — the abstain count is part of the contract, not a side channel,
because a silent abstention is indistinguishable from "no adds found".

CUSIP present in **both** books, `shares` usable in both and `> 0` in prior, ratio clears
`material_add_ratio`, resulting book weight clears `min_position_pct`. Returns dicts shaped
like `new_position_diff`'s plus `shares_latest` / `shares_prior` / `share_ratio` /
`delta_weight`, sorted by `share_ratio` desc then CUSIP (deterministic).

**Abstains — never guesses — when either share count is missing or prior is 0.** A `None`
share count must not read as "grew from nothing". Counted, so the signal status can report it.

Empty/zero-total latest book yields `[]` (no division by zero), matching `new_position_diff`.

### 4.4 `EdgarThirteenFSignal.scan` — second diff, zero extra fetches

Both books are already parsed and aggregated in memory at `signals.py:931-934`. The add diff
runs over the **same two dicts**. Emissions carry `SIGNAL_MATERIAL_ADD =
"edgar:13f_material_add"`, `meta["kind"] = "material_add"`, and the same
`fund_cik`/`fund_name`/`adsh`/`cusip`/`period` join keys the new-position emissions carry.

**Adds get their own `top_n`** (`material_add_top_n`, default 5) rather than sharing the
existing `top_n: 10`. A shared budget would let adds crowd out new positions — the sharper
signal — inside a single filing. New positions are emitted first.

`_mark_processed` semantics are unchanged: one accession, one mark, both diffs.

### 4.5 Two keying seams this forces

**`daily.py:_scan_discovery` — per-emission config keys.** It resolves weight and cap once
from the signal object's `s.name`, then applies them to every emission:

```python
cfg_key = "yahoo_screener" if s.name == "mock" else s.name
w = sig_cfg.get(cfg_key, {}).get("weight", 1.0)
cap = sig_cfg.get(cfg_key, {}).get("max_slots")
for e in ems: weights_by_signal[e.signal] = w
```

A second emission string from one signal object would therefore **silently inherit weight 1.0
and `edgar_13f`'s cap**, and the chosen 0.75 would never take effect. Fix: an optional
`cfg_key_for(emission)` hook on the signal, consulted per emission, defaulting to today's
`cfg_key` when the signal does not define it. **Byte-identical for every existing signal**,
pinned by a test.

**`budget.originator()` — preserve the confluence invariant.** It counts distinct `signal`
strings specifically so two funds opening the same position read as *one* originator agreeing
with itself, not confluence (`budget.py:22-26`). Splitting the string would make fund-A-new +
fund-B-add read as confluence — cap-exempt and rank-boosted — while fund-A-new + fund-B-new
still would not. That asymmetry is an artifact of naming, not a judgment that two funds are
weaker evidence than two signals.

Fix: an explicit family map collapsing `edgar:13f_new_position` and `edgar:13f_material_add`
to `edgar:13f` for **both** `originator()` and the caps lookup, so `caps.get()` still matches
what `originator()` returns. Explicit map, not a `:`-prefix rule — prefix collapsing would
catch unrelated pairs like `edgar:8k` / `edgar:8k_negative` by accident.

Consequence the owner should know: adds and new positions then share **one** cap bucket, so
`max_slots` lives on the **parent** `edgar_13f` key and governs the family. The alternative
(two independent caps) lets the family claim more slots after the split than before, which is
a change nobody asked for.

### 4.6 Config — and why the add's key carries a weight and nothing else

```yaml
  thirteenf:
    material_add:
      enabled: true            # THE on/off switch; false => byte-identical to today
      ratio: 1.50              # shares_latest / shares_prior bar
      top_n: 5                 # per-filing cap, separate from new positions' top_n: 10
  signals:
    edgar_13f: {enabled: true, weight: 1.0, max_slots: 4}   # max_slots is NEW; governs the
                                                            # whole edgar:13f family (§4.5)
    # Weight-only lookup key for the `edgar:13f_material_add` emission string. NOT a
    # buildable signal: deliberately absent from daily.py's _KNOWN_SIGNAL_KEYS, so no
    # `enabled` here — that switch is thirteenf.material_add.enabled above. One switch,
    # one place.
    edgar_13f_material_add: {weight: 0.75}
```

Two things this resolves, both found in spec self-review:

- **No dead knob.** `_enabled_signal_names` (`daily.py:63`) only returns keys present in
  `_KNOWN_SIGNAL_KEYS`, so an `enabled: true` on `edgar_13f_material_add` would be silently
  ignored while *looking* authoritative. It is therefore omitted, with a comment saying why.
  `edgar_13f_material_add` must **not** be added to `_DISCOVERY_SIGNAL_NAMES` — that would
  make `daily.py` try to build a second signal object and re-fetch every infotable.
- **`material_add.enabled: false` makes the whole feature inert** — no second diff, no
  emissions, no state change. The production kill switch.

**`max_slots: 4` on `edgar_13f` is a behaviour change to the existing signal** (uncapped
today) and is called out rather than smuggled in. It is safe *because* `budget.select` never
wastes a slot: on a burst night where 13F emits 30 candidates and other originators 3, the cap
gives the other 3 first refusal and then **backfills 13F into every remaining slot** (12 of 15,
not 4). So it is a re-ordering of the drop set, not a quota — the family can still dominate a
night when it is the only supply, which is exactly the desired behaviour given supply is the
binding constraint.

---

## 5. Testing

Pure units carry the weight; all offline, no network.

**`parse_infotable`** — shares parsed; comma-formatted; missing `sshPrnamt → None`;
non-numeric → `None`; existing `value`/option/PRN assertions still pass unchanged.

**`aggregate_positions`** — shares summed across split voting rows; all-`None` shares →
aggregate `None`, not 0; mixed `None`/present sums the present ones; `value` totals bit-identical
to today.

**`material_add_diff`** — clears/misses the ratio; below the weight floor; new position
(absent from prior) is **not** an add; exit is not an add; prior shares 0 → abstain; missing
shares either side → abstain; negative `delta_weight` clamps to strength 0.0, still emits;
zero-total book → `[]`; ordering deterministic.

**`material_add_diff` × `new_position_diff` are disjoint** — over one pair of books, no CUSIP
appears in both result sets. This is the cohort-contamination guard.

**Signal-level** (injected fakes, existing fixture pattern) — adds emit with the right signal
string and meta; `material_add.enabled: false` emits no add and produces **the same state as
today** (note `_mark_processed` still runs on every processed accession either way — the claim
is "unchanged *relative to today*", NOT "no state written", which would be false and would
mislead an implementer into asserting `processed_accessions == []`); `material_add_top_n` caps
independently of `top_n`; new positions are emitted before adds; and the status line reports
new positions and adds as **separate** counts.

**Back-compat pins** — `_scan_discovery` weight/cap resolution unchanged for a signal without
`cfg_key_for`; `budget.select`/`originator` unchanged for every non-13F signal string; the
whole feature off ⇒ emissions byte-identical to today.

---

## 6. Verification and deploy

Per `docs/audits/2026-08-06-discovery-breadth-plan.md` §7, lint is a hard gate:

```bash
uv run ruff check src tests
uv run pytest -q
```

Deploy — and note `/opt/shortlist` is one commit behind `main` already:

```bash
systemctl is-active shortlist-scout.service       # MUST be inactive; never 22:30-22:35 UTC
sudo bash deploy/install_opt_shortlist.sh         # from THIS checkout, never from /opt/shortlist
git -C /opt/shortlist log --oneline -1            # verify; never trust the exit code
```

**Deploy before the first fund files its Q2 13F** (§1.1). After the first post-deploy burst,
check the manifest's `edgar_13f` detail line for the add count and the abstain count.

---

## 6a. Live validation against real filings (2026-08-09, post-implementation)

Ran the shipped `material_add_diff` over each fund's real Q1-2026 vs Q4-2025 infotable pair
(read-only, outside the scout, production state untouched). This is the offline dress rehearsal
for the Q2 burst.

| fund | book | `sshPrnamt` parsed | new | adds | abstentions |
|---|---|---|---|---|---|
| Berkshire | 29 | 29/29 | 1 | 1 | 0 |
| Pershing Square | 11 | 11/11 | 1 | 0 | 0 |
| Baupost | 22 | 22/22 | 6 | 1 | 0 |
| ValueAct | 18 | 18/18 | 3 | 1 | 0 |
| Third Point | 33 | 33/33 | 10 | 0 | 0 |
| Appaloosa | 31 | 31/31 | 1 | 3 | 0 |
| TCI | 10 | 10/10 | 1 | 0 | 0 |
| **total** | **154** | **154/154** | **23** | **6** | **0** |

Four findings:

1. **`sshPrnamt` coverage is 100%** across 154 real positions and 7 filers. The abstain path is
   correct to have, but on this cohort it never fires — a nonzero count in production is
   therefore a genuine signal that something changed, not routine noise.
2. **Adds would have added ~26% more supply** (6 against 23 new positions) — material, and not
   so much that adds dominate the digest. `top_n: 5` never bound (max 3 adds on one filing).
3. **Face validity is good.** Berkshire ×3.04 Alphabet, Appaloosa ×3.42 Uber / ×2.14 Vistra /
   ×1.98 Amazon, ValueAct ×1.61 Toast. Strength spread 0.18–1.00, not pinned at the cap.
4. **No add in this cohort is a stock-split artifact**, and the check is cheap. §2.1's split
   false positive has a signature — shares up by the split ratio with **flat book weight** — and
   every real add moved weight substantially:

   | Appaloosa add | share ratio | w prior → now | Δw |
   |---|---|---|---|
   | Uber | ×3.42 | 2.21% → 7.68% | +5.47% |
   | Vistra | ×2.14 | 2.22% → 5.12% | +2.90% |
   | Amazon | ×1.98 | 7.34% → 15.16% | +7.82% |

   Amazon's ×1.98 is exactly the "looks like a 2:1 split" case, and `delta_weight` settles it:
   a split leaves weight flat, this doubled it. **If a split guard is ever needed, `|delta_weight|
   ≈ 0` is the discriminator** — but it is still not built, because on real data the false
   positive did not occur.

### End-to-end through the real signal (post-review, 2026-08-09)

The table above exercised `material_add_diff` directly. This run drives the whole
`EdgarThirteenFSignal.scan` path — real SEC fetches, the real CUSIP resolver, the real emission
builder — on a throwaway instance with an empty `seen_accessions` and no state written:

```
2 new 13F positions from 2 filings (2 funds), 4 material add(s)

edgar:13f_material_add  UBER  str=1.00  Appaloosa added to 13F position (Q1 2026): shares +242%
edgar:13f_material_add  AMZN  str=1.00  Appaloosa added to 13F position (Q1 2026): shares +98%
edgar:13f_material_add  GOOGL str=0.78  Berkshire added to 13F position (Q1 2026): shares +204%
edgar:13f_material_add  VST   str=0.58  Appaloosa added to 13F position (Q1 2026): shares +114%
edgar:13f_new_position  SNDK  str=0.60  Appaloosa new 13F position (Q1 2026): 3.0% of book
edgar:13f_new_position  DAL   str=0.20  Berkshire new 13F position (Q1 2026): 1.0% of book
```

Confirms four things the unit tests can only assert against fakes: CUSIP→ticker resolution
works on real adds; the status headline counts new positions and adds **separately**; both
emission strings appear with correct `meta["kind"]` and **no add leaks under the new-position
string** (the cohort-contamination guard, live); and strengths spread 0.20–1.00 rather than
pinning at the cap.

**Still unobserved:** the funnel path beyond emission — dedup, prefilter, the family cap, and
the digest. That needs the live 22:30 run against the Q2 burst.

## 7. Explicitly out of scope

- **Exits** (CUSIP in prior, absent in latest). An exit surfaces a name to *avoid*, and the
  funnel emits buy candidates. Wiring it as a veto is separate machinery that can silently
  delete real picks from an already supply-starved funnel, and marquee exits are weak negative
  evidence (rebalancing, redemptions, tax). The diff seam leaves `material_exit_diff` as a
  clean later sibling. Owner's call, 2026-08-09.
- **Trims** (shares down but position held) — same reasoning as exits.
- **A backfill cohort.** Blocked by the same PiT CUSIP→symbology leak that defers the parent
  signal's backfill. Not a new gap.
- **Split detection.** §2.1.

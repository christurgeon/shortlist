# Short Interest (C1) — FINRA consolidated feed + `crowded_short` soft flag (design)

> **Status:** approved design, ready for implementation plan.
> **Companion docs:** [`docs/DATA_SOURCES.md`](../../DATA_SOURCES.md) §C1 (the candidate this ships),
> [`docs/ASSESSMENT_GAPS.md`](../../ASSESSMENT_GAPS.md) gap #5 (smart-money / alt-data confirmation).
> **Sibling spec (coordinate, don't duplicate):**
> [`2026-06-01-edgar-events-design.md`](2026-06-01-edgar-events-design.md) — introduces the auxiliary
> non-coverage-section mechanism (`_AUX_DEFAULTS`, the aux-merge line) and the screener **"Flags"
> column** that this spec also lands on. Whichever ships first *creates* those; the second *extends*
> them. §2.1 and §5 below call out the exact shared touch-points.

## 0. Why this, why now

The stack scores fundamentals, momentum, value, and insider flow but is **blind to bear positioning**
(`DATA_SOURCES.md` gap #5). **Short interest as a % of shares** and **days-to-cover** are a desk's
direct read on the skeptic case: high + rising short interest into improving fundamentals is either a
**squeeze candidate** or a **credible short thesis worth respecting** — either way it is signal the
current scorer cannot see. It was the **strong runner-up** in the edgar-events decision record
(§10 there): a cleaner *numeric* signal than filing events, deferred only because it is a **new
pipeline** with no synergy to shipped work. This spec builds that pipeline.

The signal is **directionally ambiguous by construction** (squeeze vs. skeptic), so v1 deliberately
does **not** move any sub-score. It ships as a **soft flag** — an advisory ("investigate"), parallel
to the existing hard gates but non-disqualifying — plus the raw fields for the research layer.

### 0.1 Data source — verified live, premise corrected

The `DATA_SOURCES.md` C1 entry pointed at FINRA "Equity Short Interest." Live verification
(2026-06-01) found that endpoint is **frozen and OTC-only**: `otcMarket/EquityShortInterest`'s newest
partition is `2022-09-15`, and listed names (AAPL) 404/204 there. The **correct, live** dataset is
**`ConsolidatedShortInterest`** (NMS-listed + OTC, Rule 4560 amended consolidated tape):

- **Latest partition `2026-05-15`** (current), **covers listed names** — AAPL on 2026-05-15:
  `currentShortPositionQuantity=138,782,718`, `previousShortPositionQuantity=134,675,274` (rising),
  `averageDailyVolumeQuantity=50,565,316`, `daysToCoverQuantity=2.74`.
- **Keyless** — anonymous `POST` returns `200`; no OAuth/key. (Confirmed by live `curl`.)
- Sanity check of the chosen denominator (§3): AAPL shares-out ≈ `market_cap/price` ≈ 14.8B →
  `short_pct_outstanding ≈ 138.78M / 14.8B ≈ 0.9%` — realistic; AAPL is *not* crowded-short.

This correction is the single most important output of the pre-spec review; it is baked into §3.

## 1. Scope

**In scope (v1):**
- A new **keyless harness `Source`** — `FinraSource` — fetching FINRA `ConsolidatedShortInterest`.
- A new auxiliary snapshot section **`ShortInterest`** (raw FINRA facts, point-in-time).
- New `StockMetrics` fields **`short_pct_outstanding`**, **`days_to_cover`**,
  **`short_interest_rising`**, **`short_data_age_days`** (derived in the bridge).
- A new **`flags: list[str]`** on `ScoreCard`, populated by a new `check_flags()` — parallel to
  `check_gates()` but **non-disqualifying**: it never touches `composite`, `passed`, or any sub-score.
  v1 emits one flag: **`crowded_short`**.
- Surfacing in `--json`, the screener **"Flags" column**, and the research-layer brief context.
- Reachable from the screener via `--engine harness` (harness-only `Source`, exactly like `YahooSource`).

**Out of scope (YAGNI — explicitly deferred):**
- **Any sub-score movement or hard gate** from short interest. The signal is two-sided; without a
  backtest (gated on snapshot history, `ASSESSMENT_GAPS.md` §2.1) there is no basis to weight or sign
  it. Flag-only is the disciplined choice.
- **Backtest integration.** The section records `settlement_date` so a *future* point-in-time
  backtest (bi-monthly + 7-business-day publication lag) is correct by construction — but wiring it
  into `shortlist.backtest.*` is a separate item.
- A screener-layer `Provider` (no `providers/` parity). Harness-only, like `YahooSource`.
- **True free-float** denominator. We derive from shares-outstanding (`market_cap/price`) and label it
  honestly as `short_pct_outstanding` (§3.3). A real float feed is a clean follow-up.
- A second flag decomposition (`high_short_interest` + `hard_to_cover` as distinct chips) — reserved
  in §5.3 as a documented alternative; v1 ships the single conjunctive `crowded_short`.

## 2. Data model (`src/shortlist/data/models.py`)

One new dataclass, placed alongside the existing sections:

```python
@dataclass
class ShortInterest:
    settlement_date: Optional[str] = None       # ISO; the cycle this data is AS-OF (point-in-time)
    short_shares: Optional[float] = None         # currentShortPositionQuantity
    prev_short_shares: Optional[float] = None    # previousShortPositionQuantity (prior cycle)
    avg_daily_volume: Optional[float] = None     # averageDailyVolumeQuantity
    days_to_cover: Optional[float] = None         # daysToCoverQuantity — FINRA-supplied, NOT recomputed
    split_flag: bool = False                      # stockSplitFlag — counts not comparable across a split
    revised: bool = False                         # revisionFlag — figure was revised after publication
```

`TickerSnapshot` gains one field, **auxiliary (NOT a `KEY_OBJECT`)**:

```python
@dataclass
class TickerSnapshot:
    ...
    price: Optional[Price] = None
    short_interest: Optional[ShortInterest] = None   # NEW — auxiliary, NOT a KEY_OBJECT
    raw: ...
```

### 2.1 Why `short_interest` is NOT a `KEY_OBJECT` (and the shared aux mechanism)

`KEY_OBJECTS` drives `coverage()` / `missing()` — "do we have the *assessment-ready fundamentals*?".
Short interest is a sparse signal: **most quality large-caps carry little short interest**, and many
names (ETFs, some foreign issuers) are absent from the file entirely. Folding it into the coverage
denominator would make every lightly-shorted name read as "thin coverage" — wrong, and exactly the
same reasoning that excludes `recent` / `monthly_closes` via `_NON_SIGNAL_FIELDS`, and that the
sibling edgar-events spec uses for its `events` section.

Concretely, **reuse the sibling spec's `_AUX_DEFAULTS` mechanism** (don't invent a parallel one):

- `short_interest` is added to `TickerSnapshot` but **not** to `KEY_OBJECTS` and **not** to `_DEFAULTS`.
- It joins `_AUX_DEFAULTS` (the non-coverage round-trip map): `_AUX_DEFAULTS = {"events": Events,
  "short_interest": ShortInterest}`. **If edgar-events has not yet landed, this spec creates
  `_AUX_DEFAULTS` with just `short_interest`;** if it has, this spec adds the one key. Same for the
  aux-merge line in §4 and the "Flags" column in §5.
- `from_dict` rebuilds `short_interest` from `_AUX_DEFAULTS`. It has **no nested list** (unlike
  `events.recent` / `insider.recent`), so the generic `_build` suffices — no special-casing.
- `to_dict` works unchanged (`dataclasses.asdict` recurses).
- `coverage()` / `missing()` are **structurally unchanged** (they iterate `KEY_OBJECTS`), so short
  interest presence/absence is provably invariant to both.

## 3. Fetch (`src/shortlist/data/sources.py`, new `FinraSource`)

`FinraSource` is a **keyless, bulk, fetch-once-per-run** source — the `YahooSource` SPY-reuse
precedent (one expensive fetch held on the instance, reused across every ticker). Registered in
`_REGISTRY` and added to `config.yaml: harness_sources` and the scout `deep_screen_sources`.

```python
class FinraSource(Source):
    name = "finra"
    DATA = "https://api.finra.org/data/group/otcMarket/name/ConsolidatedShortInterest"
    PARTS = "https://api.finra.org/partitions/group/otcMarket/name/ConsolidatedShortInterest"
```

### 3.1 Latest-cycle discovery (`settlementDate` is a partition key)

`settlementDate` is a **partition key**: the data query rejects `sortFields` on it
(`400 "Sorting is allowed only if all partition keys are specified in EQUAL CompareFilter"`), and the
default ordering returns the *oldest* cycle. So the latest cycle is discovered separately:

1. `GET {PARTS}` → `{"availablePartitions": [{"partitions": ["2026-05-15"]}, ...]}`; take
   `max(settlementDate)`. (Verified: returns dates through `2026-05-15`.)
2. Hold the chosen `settlement_date` on the instance; every per-ticker `fetch()` reads the cached
   index for that date.

### 3.2 Bulk load + pagination (one-time per run, disk-cached by settlement date)

The latest partition is **~21,896 rows** with `record-max-limit: 5000` (verified via response
headers), so the full cycle is **~5 paginated `POST`s**:

```
POST {DATA}   Accept: application/json   (JSON; default is CSV/text-plain)
body: {"limit": 5000, "offset": <0,5000,...>,
       "compareFilters": [{"fieldName":"settlementDate","fieldValue":"<latest>","compareType":"EQUAL"}]}
```

Page until a short page (`len < limit`) or `offset >= record-total` (the `record-total` response
header). Rows are indexed into an instance `dict[symbol -> row]` keyed by **normalized symbol** (§3.4).
The full payload (or the normalized index) is **disk-cached at `.cache/finra/<settlement_date>.json`**
— keyed by **settlement date, not run date**, so the cache legitimately survives the ~2 weeks until
the next cycle publishes. Corrupt cache → refetch; write failure non-fatal (Yahoo pattern).

### 3.3 Per-ticker `fetch()` — O(1) lookup → `ShortInterest`

`fetch(ticker)` normalizes the ticker (§3.4), looks it up in the index, and builds a `ShortInterest`
from the row's `currentShortPositionQuantity` / `previousShortPositionQuantity` /
`averageDailyVolumeQuantity` / `daysToCoverQuantity` / `stockSplitFlag` / `revisionFlag` /
`settlementDate`. **`days_to_cover` is taken straight from FINRA's `daysToCoverQuantity`** — never
recomputed (we inherit FINRA's official rounding and its `999.99` zero-volume sentinel, which the
bridge treats as "no usable DTC"). A ticker **absent** from the index → `short_interest` stays `None`
(normal; not an error). Any network/parse failure → empty section + one redacted `finra: <err>`
string; **never aborts the snapshot**.

> **Denominator note (carried into the bridge, not here):** FINRA gives short *shares*, not a percent.
> `short_pct_outstanding` is derived in the bridge as `short_shares / (market_cap/price)` because **no
> shares-outstanding field exists in the snapshot today**. Float ≤ shares-outstanding, so the percent
> is **conservatively under-stated** vs. the float-based number desks quote — a safe direction for a
> flag (won't over-fire). The landmines (ADR ratios can *over*-state; dual-class; adjusted-vs-raw
> price) are handled by the §3.5 sanity clamp.

### 3.4 Symbol normalization (silent-miss guard)

FINRA `symbolCode` is plain for most listed names (`AAPL` → `AAPL`) but uses dotted class shares
(`BRK.A`/`BRK.B`). Our universe tickers may arrive dotted or dashed. A naive `dict[ticker]` lookup
would **silently miss** every dotted/dual-class name (absent → `None` → never flags), reading as "no
short interest" rather than "symbol not matched." Normalize on both sides: uppercase, and try the
ticker as-is plus `.`/`-` variants (`BRK.B` ⇄ `BRK-B` ⇄ `BRKB`). When a **universe** ticker is not
found in a successfully loaded cycle, record a coverage note ("finra: symbol not in cycle") so the
miss is **visible, not silent**.

### 3.5 Failure isolation & scale

One bulk fetch (~5 requests, a few MB) **per run**, then O(1) lookups — unlike the per-ticker sources,
this adds **no per-ticker request load** and needs no caching-layer dependency (a genuine advantage
worth stating in `HARNESS.md`). The partitions GET and the data POSTs are each wrapped so any failure
degrades to an empty index + redacted error; a FINRA outage never blocks a run.

## 4. Merge & coverage

### 4.1 Merge (`merge_snapshots`)

`short_interest` is single-source (only FINRA supplies it), merged via the existing `_pick_first`
on the **shared aux-section line** outside the `KEY_OBJECTS` loop (the line the sibling spec
introduces; this spec adds `short_interest` to it):

```python
for name in KEY_OBJECTS:   # unchanged, coverage-bearing
    ...
# Auxiliary (non-coverage) sections: pick-first from the highest-priority source with data.
for aux in _AUX_DEFAULTS:                      # {"events", "short_interest"}
    instances = [(r.source, getattr(r.partial, aux, None)) for r in ordered if r.partial]
    merged, contributors = _pick_first(instances)
    if merged is not None:
        setattr(snap, aux, merged)
        snap.provenance[aux] = contributors
```

### 4.2 Coverage (`coverage_adapt.py`, `coverage.py`) — no structural change

- A `finra: <err>` string maps to base source `finra` through the existing `_source_of`
  head-splitting; a FINRA failure surfaces as `finra: error` **without** degrading the fundamentals
  coverage other sources provide.
- Because `short_interest` is not a `KEY_OBJECT`, `TickerSnapshot.coverage()` / `missing()` are
  provably invariant to it (§2.1).
- The §3.4 "symbol not in cycle" note is informational only — it does **not** lower the coverage
  fraction (absence of short interest is normal).

## 5. Scorer surface & bridge

### 5.1 Bridge (`src/shortlist/data/bridge.py`) — derive, guard, clamp

At the end of `snapshot_to_metrics`, guarded on `snap.short_interest`:

```python
si = snap.short_interest
if si:
    m.days_to_cover = si.days_to_cover if (si.days_to_cover or 0) < _DTC_SENTINEL else None
    if si.short_shares is not None and m.market_cap and m.price:        # truthy: no /0
        shares_out = m.market_cap / m.price
        pct = si.short_shares / shares_out if shares_out else None
        # Sanity clamp: > ~60% of shares-outstanding for a >$2B name almost always means a
        # broken denominator (ADR ratio, dual-class whole-co cap). Drop it rather than emit a
        # garbage value that could false-fire the flag or mislead --json.
        m.short_pct_outstanding = pct if (pct is not None and pct <= _MAX_PLAUSIBLE_SHORT_PCT) else None
    if si.short_shares is not None and si.prev_short_shares is not None and not si.split_flag:
        m.short_interest_rising = si.short_shares > si.prev_short_shares   # None across a split
    m.short_data_age_days = _age_days(snap.as_of, si.settlement_date)      # as_of - settlement (pure, PIT)
```

`_MAX_PLAUSIBLE_SHORT_PCT = 0.60`, `_DTC_SENTINEL = 999.99` (FINRA's zero-volume cap) are documented
module constants. `_age_days(as_of, settlement)` is pure (no clock read — uses the snapshot's own
capture time) and None-safe (unparseable date → `None`). New `StockMetrics`
fields all default `None`/`None` so they are inert in the pure-screener engine.

### 5.2 Scoring (`scoring.py`, `ScoreCard.flags`) — soft, config-driven, None-safe

`ScoreCard` gains `flags: list[str] = field(default_factory=list)` — **separate from `gates`**;
`passed` (`not self.gates`) and `composite` are untouched. A new `check_flags`, called from `score()`
alongside `check_gates`:

```python
def check_flags(m: StockMetrics, f: dict) -> list[str]:
    out = []
    cs = f["crowded_short"]
    if (m.short_pct_outstanding is not None and m.days_to_cover is not None
            and m.short_pct_outstanding >= cs["min_short_pct_outstanding"]
            and m.days_to_cover >= cs["min_days_to_cover"]
            and (not cs["require_rising"] or m.short_interest_rising is True)
            and (m.short_data_age_days is None or m.short_data_age_days <= cs["max_staleness_days"])):
        out.append("crowded_short")
    return out
```

**AND, not OR** (the expert correction): `short_pct_outstanding` measures *size* of the bear position;
`days_to_cover` measures *unwind cost* — distinct axes that both share `short_shares`, so OR would
double-count and maximize false positives. The conjunction is the desk "squeeze-fuel" definition: a
position that is **both large and hard to cover, and still building**. Fully None-safe → **never fires
when data is absent**, so the default screener engine (no `finra` source) simply never flags.

`score()` adds `flags=check_flags(m, config["flags"])` to the returned `ScoreCard` (no other change).

### 5.3 Config (`config.yaml`)

```yaml
flags:                      # soft, non-disqualifying advisories (parallel to `gates`)
  crowded_short:
    min_short_pct_outstanding: 0.10   # of shares OUTSTANDING (float-based desks quote ~20%; our
                                      # denominator under-states, so the line sits lower). A PRIOR.
    min_days_to_cover: 5.0            # >5 elevated, >10 extreme (desk bands)
    require_rising: true              # static high SI is baseline; the *change* is the signal
    max_staleness_days: 35            # ignore a cycle > ~2 missed publications old (stale-cache guard)
```

Add `finra` to `harness_sources` and to `scout.deep_screen_sources`. Absent keys fall back to these
defaults so existing configs keep working. The thresholds are **defensible priors, not fitted** — same
caveat as the weights; tune once a short-interest backtest exists.

> **Two-flag alternative (reserved, not v1):** emit `high_short_interest` and `hard_to_cover` as
> separate chips instead of the single conjunctive `crowded_short`, preserving both axes. Deferred
> to keep v1 to one well-defined flag; the `check_flags` shape makes adding flags trivial.

### 5.4 Surfacing

- **`--json`:** the `short_interest` section appears in the snapshot block; `flags` appears on each
  card (emitted only when non-empty, the "only when notable" discipline).
- **Screener "Flags" column** (the shared column from the sibling spec): render `crowded_short` as a
  neutral chip (e.g. `SHORT`), distinct from the red **gate** chips. If this spec lands first it
  *creates* the column rendering both `ScoreCard.flags` chips and (later) event chips.
- **stderr summary:** list `flags` next to gates, e.g. `⚑ crowded_short`.
- **Research layer:** the raw short-interest facts reach the brief context via `StockMetrics`
  (`short_pct_outstanding`, `days_to_cover`, `short_interest_rising`, `short_data_age_days`) so a
  Claude brief can note "≈12% of shares short, 6.3 days to cover, rising — investigate the bear case."

## 6. Architecture fit & house rules

- **Two-layer fit:** harness-only `Source`, the `YahooSource` shape. No `providers/` change; the
  screener reaches it through `--engine harness`. Consistent with the two-registry split in `CLAUDE.md`.
- **Redaction:** the FINRA URL carries no key; `finra:` errors append through the same redacted path
  as every other source. No new secret-leak surface.
- **Honest coverage:** short interest never zeroes or inflates a sub-score and never moves the
  coverage denominator (§2.1, §4.2) — the "thin source lowers coverage, never silently zeroes" house
  rule holds by construction. The flag is advisory, never disqualifying.
- **Point-in-time honesty:** `settlement_date` + `short_data_age_days` are surfaced so the bi-monthly
  + 7-bday lag is visible; the `max_staleness_days` guard stops a stale cache masquerading as current.

## 7. Testing

Mirrors the existing keyless-source test structure (pure builders without network; one isolation test;
round-trip + coverage invariance; a gated live smoke test for the external contract).

1. **`FinraSource` index/normalize pure tests** (no network): a fixture page list →
   - row → `ShortInterest` mapping (incl. `999.99` DTC sentinel preserved raw in the section);
   - symbol normalization hits `BRK.B`/`BRK-B`/`BRKB`;
   - latest-partition selection picks `max(settlementDate)` from a fixture partitions payload;
   - pagination loop assembles all rows across multiple short/full pages.
2. **Absence & isolation:** a ticker not in the index → `short_interest is None`, no error; a forced
   fetch exception → empty section + exactly one redacted `finra:` error, snapshot otherwise intact.
3. **Bridge derivation:** `short_pct_outstanding = short_shares·price/market_cap`; `/0` and `None`
   guards; **sanity clamp** drops a >60% value to `None`; `short_interest_rising` is `None` across a
   split, `True`/`False` otherwise; `days_to_cover` passes through but the `999.99` sentinel → `None`;
   `short_data_age_days` from a fixed `as_of` is correct and `None` on an unparseable date.
4. **`check_flags` truth table:** trips only on `pct≥t ∧ dtc≥t ∧ rising ∧ fresh`; verify each clause
   independently suppresses it; **all-None inputs → `[]`** (screener-engine no-op); a stale cycle
   (`age > max_staleness_days`) → `[]`. Assert `passed`/`composite` are **byte-identical** with vs
   without a `crowded_short` flag present (the zero-impact guarantee).
5. **Merge + round-trip:** `short_interest` survives `merge_snapshots` (provenance `["finra"]`) and a
   `to_dict` → `from_dict` cycle (the §2.1 `_AUX_DEFAULTS` round-trip — **mandatory**, or persisted
   snapshots silently drop the section on reload).
6. **Coverage invariance:** `coverage()` / `missing()` identical with and without `short_interest`;
   a `finra:` error maps to `finra: error` in `snapshot_to_coverage_inputs` without lowering the
   fraction.
7. **Live smoke (gated, `@pytest.mark.live`, skipped by default):** real `FinraSource` against the
   live partition → AAPL row present, fields populated, `days_to_cover` ≈ FINRA's value. Pins the
   §0.1 external contract (dataset name, `symbolCode` field, keyless access).

## 8. Files touched (implementation map)

| File | Change |
|---|---|
| `src/shortlist/data/models.py` | `ShortInterest` dataclass; `TickerSnapshot.short_interest`; add to `_AUX_DEFAULTS` (create if absent); aux-merge line; `from_dict` aux rebuild |
| `src/shortlist/data/sources.py` | new `FinraSource` (partitions discovery, paginated bulk load, disk cache, symbol-normalized index, O(1) `fetch`); register in `_REGISTRY` |
| `src/shortlist/data/bridge.py` | derive `short_pct_outstanding` / `days_to_cover` / `short_interest_rising` / `short_data_age_days`; sanity clamp + DTC-sentinel + split guards (module constants) |
| `src/shortlist/models.py` | new `StockMetrics` fields: `short_pct_outstanding`, `days_to_cover`, `short_interest_rising`, `short_data_age_days` |
| `src/shortlist/scoring.py` | `ScoreCard.flags`; `check_flags()`; call it from `score()` (no sub-score/gate/composite change) |
| `src/shortlist/screen.py` | `flags` in `--json`; render `flags` chips in the shared "Flags" column (table + plain); stderr summary line |
| research runner | pass short-interest facts into per-ticker brief context |
| `config.yaml` | `flags.crowded_short` block; add `finra` to `harness_sources` + `scout.deep_screen_sources` |
| `tests/` | the seven test groups in §7 |
| `docs/DATA_SOURCES.md` | C1 → "shipped (harness)"; **correct the endpoint** to `ConsolidatedShortInterest` and note the OTC-only/frozen trap |
| `HARNESS.md` / `CLAUDE.md` | new `finra` source + the `flags` concept (soft vs hard gate); keyless FINRA endpoint + `symbolCode` gotcha; bulk-once-per-run scale note |

## 9. Coordination with the sibling edgar-events spec

Both specs are additive and compose; **either order works**:
- **`_AUX_DEFAULTS`, the aux-merge loop, and the "Flags" column** are shared infrastructure. The first
  spec to land creates them; the second extends (adds its key / its chip). §2.1, §4.1, §5.4 are
  written to do whichever is needed.
- `ScoreCard.flags: list[str]` (this spec) is the **general soft-advisory mechanism**; edgar-events'
  event chips can render in the same column. They do not conflict — edgar carries event facts as
  booleans; this carries a computed threshold advisory as a string. Implementer should grep for an
  existing `ScoreCard.flags` before adding it.

## 10. Decision record (choices locked with the user)

| Decision | Choice | Rationale |
|---|---|---|
| Scoring role | **Soft flag only** | Signal is two-sided (squeeze vs skeptic); no basis to sign/weight without a backtest. Never moves `composite`/`passed`. |
| Layer | **Harness `Source` only** | Bulk-file fetch fits the async harness; screener gets it via `--engine harness` (Yahoo precedent). No `providers/` duplication. |
| `%` denominator | **Shares-outstanding (`market_cap/price`), labeled `short_pct_outstanding`** | No float feed exists; outstanding under-states (conservative). Honest label, not `_float`. |
| Flag logic | **AND + rising** (`pct ∧ dtc ∧ rising ∧ fresh`) | Size and cover are distinct axes; OR double-counts and over-fires. Change (rising) is the real signal. |
| Data source | **`ConsolidatedShortInterest`** (not `EquityShortInterest`) | Live verification: the original is frozen (2022) + OTC-only; consolidated is current and covers listed names. |
| Backtest | **Deferred** | Gated on snapshot history (`ASSESSMENT_GAPS.md` §2.1); `settlement_date` recorded for future PIT correctness. |

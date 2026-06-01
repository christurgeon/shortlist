# EDGAR Events — filing-stream event awareness (design)

> **Status:** approved design, ready for implementation plan.
> **Companion docs:** [`docs/DATA_SOURCES.md`](../../DATA_SOURCES.md) (the data-feed roadmap this
> closes A1's events half of), [`docs/ASSESSMENT_GAPS.md`](../../ASSESSMENT_GAPS.md) (gap #6 event
> awareness, gap #5 smart-money). Sibling specs: `2026-05-31-harness-scoring-bridge-yahoo-design.md`,
> `2026-06-01-edgar-value-and-harness-consolidation.md` (the EDGAR financials work this extends).

## 0. Why this, why now

A fundamentals snapshot is a point-in-time bet. An **8-K**, an **activist 13D**, or a **planned
insider sale (Form 144)** filed the day after we score a name can invalidate that bet — and today
the stack is blind to all of it (`DATA_SOURCES.md` gap #6). This was selected over the other
differentiated-edge candidates (short interest, 13F, Quiver) because it is **keyless**, has the
**highest synergy** with work already in the tree (the shipped EDGAR financials extractor and the
scout's Form-4 daily-index ingestion), and the **lowest marginal build** — events are filing-index
*metadata*, so the whole feature costs roughly **one extra SEC request per ticker** and adds no new
vendor, key, or rate-limiter.

It is the unbuilt half of `DATA_SOURCES.md` A1: the **financials** half shipped (commit `a205931`,
`providers/_edgar_facts.py` + `EdgarSource._build_financials_snapshot`); the **events** half — the
8-K / 13D / 13G / 144 detection the doc describes — is what this spec delivers.

## 1. Scope

**In scope (v1):**
- A new per-ticker **`events`** section on `TickerSnapshot`, populated by the harness `EdgarSource`.
- Four event types, all from a single filing-index read: **8-K** (material event), **SC 13D /
  SC 13D/A** (activist 5%+ stake), **SC 13G / SC 13G/A** (passive 5%+ stake), **Form 144** (planned
  insider sale).
- Four derived boolean flags + a structured `recent` event list.
- **Pure enrichment**: flags surface in `--json`, the screener table `Flags` column, and the
  research-layer brief context. They touch **no sub-score and no gate**.
- Reachable from the screener via `--engine harness` (harness-only `Source`, exactly like Yahoo).

**Out of scope (YAGNI — explicitly deferred):**
- Per-filing document parsing: 8-K item codes, 13D dissident identity / stake %, 144 share counts.
  v1 is **index-level metadata only** (form type, filing date, accession, index URL). This is the
  single decision that keeps the cost at ~1 SEC call/ticker; richer parsing is a clean follow-up.
- A screener-layer `Provider` (no `providers/` parity). Harness-only, like `YahooSource`.
- Any numeric sub-score or hard gate driven by events (see §5; a soft hook is reserved but ships off).
- 13F, short interest, Quiver, attention proxies — separate roadmap items, not this spec.

## 2. Data model (`src/shortlist/data/models.py`)

Two new dataclasses, placed alongside the existing sections:

```python
@dataclass
class FilingEvent:
    form: str                         # "8-K", "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A", "144"
    filed: str                        # ISO date (filing date)
    accession: Optional[str] = None
    url: Optional[str] = None         # filing index URL — carries NO key (redaction-safe)

@dataclass
class Events:
    recent: list[FilingEvent] = field(default_factory=list)  # within lookback, newest-first
    recent_8k: bool = False
    activist_13d: bool = False        # any SC 13D or SC 13D/A in window
    passive_13g: bool = False         # any SC 13G or SC 13G/A in window
    planned_insider_sale_144: bool = False  # any Form 144 in window
```

`TickerSnapshot` gains one field:

```python
@dataclass
class TickerSnapshot:
    ...
    price: Optional[Price] = None
    events: Optional[Events] = None   # NEW — auxiliary, NOT a KEY_OBJECT
    raw: ...
```

### 2.1 Why `events` is NOT a `KEY_OBJECT`

`KEY_OBJECTS` drives `coverage()` and `missing()` — it answers "do we have the *assessment-ready
fundamentals* we need?". Events are signals/flags, not fundamentals; folding them into the coverage
denominator would make a name with no recent filings read as "thin coverage," which is wrong. This is
the same reasoning that already excludes `recent` / `monthly_closes` via `_NON_SIGNAL_FIELDS`.

Concretely:
- `events` is added to `TickerSnapshot` but **not** to `KEY_OBJECTS` and **not** to `_DEFAULTS`.
- A new parallel map `_AUX_DEFAULTS = {"events": Events}` is added so `from_dict` can round-trip it.
- `from_dict` rebuilds `events` from `_AUX_DEFAULTS` and, like the existing `insider.recent` handling,
  rebuilds `events.recent` as a list of `FilingEvent` (the generic `_build` only does the top object).
- `to_dict` already works unchanged (`dataclasses.asdict` recurses).
- `coverage()` / `missing()` are **structurally unchanged**: because they iterate `KEY_OBJECTS`,
  events presence or absence cannot move either number.

## 3. Fetch (`src/shortlist/data/sources.py`, `EdgarSource`)

Events are a **third failure-isolated section** in `_fetch_sync`, mirroring the existing financials
block one-for-one. The cardinal rule from the financials work holds: **an events failure must never
drop a successfully fetched insider or statements result.**

```python
def _fetch_sync(self, ticker: str) -> SourceResult:
    res = self._fetch_insider(ticker)            # always sets res.partial
    # Financials: isolated (existing)
    try:
        fin_snap = self._build_financials_snapshot(ticker, self._fetch_financials_object(ticker))
        if fin_snap.statements is not None:
            res.partial.statements = fin_snap.statements
    except Exception as e:
        res.errors.append(f"edgar-financials: {e}")
    # Events: isolated (NEW)
    try:
        ev = self._build_events(self._fetch_filings_index(ticker))
        if ev is not None:
            res.partial.events = ev
    except Exception as e:
        res.errors.append(f"edgar-events: {e}")   # base reduces to "edgar" in coverage_adapt
    return res
```

Two methods, split along the same network/pure seam as `_fetch_financials_object` /
`_build_financials_snapshot`:

- **`_fetch_filings_index(ticker)` — network, mockable seam.** Uses edgartools
  `Company(ticker).get_filings(form=self._event_forms).latest(self._index_limit)` and returns a plain
  list of `{form, filed, accession, url}` dicts (decoupling the pure builder from edgartools types).
  Reuses the source's existing CIK resolution and the shared `_edgar_semaphore` — **no new
  rate-limiter** (SEC fair-access is already bounded). `_index_limit` default 40 (same as the Form-4
  fetch); the lookback filter in the builder is the real bound.
- **`_build_events(records)` — pure, unit-testable without network.** Filters to
  `filed >= today - lookback_days`, sorts newest-first, builds `FilingEvent`s, and sets the four
  booleans by form classification. Returns `None` when there are no in-window filings (so an absent
  `events` section reads as "no recent events," consistent with how other sections degrade).

Config plumbed through `__init__` (alongside `lookback_days`): `event_forms`, `event_lookback_days`,
`index_limit`.

### 3.1 The one caveat the implementer must verify (FMP-slug-style)

The exact **form strings** edgartools' `get_filings(form=[...])` filter accepts/returns
(`"SC 13D"` vs `"SC 13D/A"` vs `"SCHEDULE 13D"`, `"144"` vs `"Form 144"`) **must be confirmed against
a live pull before wiring** — the same discipline the FMP `/stable/` slugs needed. The classifier in
`_build_events` should match **case-insensitively on a normalized prefix** (e.g. startswith `"SC 13D"`
→ activist, startswith `"SC 13G"` → passive, exact `"8-K"` → 8-K, prefix `"144"` → planned sale) so a
trailing `/A` amendment is captured. A gated live smoke test (§7) pins this down.

### 3.2 Alternative considered (documented fallback)

The raw keyless `https://data.sec.gov/submissions/CIK{cik}.json` endpoint (already validated in
`scratch/raw/sec/submissions.json`, one httpx GET, returns every recent filing's form/date/accession)
is a cleaner and cheaper data source in isolation. It was **not** chosen for v1 because it requires its
own ticker→CIK map and a second rate-limiter outside `EdgarSource`'s thread/semaphore model, adding
surface for no signal gain. It remains the **documented fallback** if edgartools' form-filter
semantics prove unreliable: `_fetch_filings_index` is the single seam to swap, and `_build_events`
(consuming plain dicts) stays unchanged.

## 4. Merge & coverage

### 4.1 Merge (`merge_snapshots`)

`events` merges via the existing `_pick_first` strategy (only EDGAR supplies it, so first-with-data
wins), handled by **one dedicated line outside the `KEY_OBJECTS` loop** — the loop body is reserved
for coverage-bearing objects:

```python
def merge_snapshots(ticker, results, priority):
    ...
    for name in KEY_OBJECTS:   # unchanged
        ...
    # Auxiliary (non-coverage) sections: pick-first from the highest-priority source with data.
    ev_instances = [(r.source, getattr(r.partial, "events", None)) for r in ordered if r.partial]
    merged_ev, contributors = _pick_first(ev_instances)
    if merged_ev is not None:
        snap.events = merged_ev
        snap.provenance["events"] = contributors
    ...
```

### 4.2 Coverage (`coverage_adapt.py`, `coverage.py`) — no structural change

- An `edgar-events:` error string already reduces to base source `edgar` via
  `coverage_adapt._source_of` (its existing `-` / `.` head-splitting handles the `edgar-events`
  prefix), so an events fetch failure surfaces as `edgar: error` **without** falsely degrading the
  fundamentals coverage that EDGAR's statements/insider provide.
- Because `events` is not a `KEY_OBJECT`, `TickerSnapshot.coverage()` is provably invariant to it.

## 5. Scorer surface & bridge — pure enrichment

Decision (confirmed): events are **enrichment only**. No sub-score, no gate. The reserved soft hook
ships **off**.

- **`bridge.snapshot_to_metrics`** copies the four booleans and the event list onto **new
  `StockMetrics` fields**: `recent_8k`, `activist_13d`, `passive_13g`, `planned_insider_sale_144`
  (default `False`) and `filing_events: list` (default `[]`). All defaults are falsy → **provably zero
  impact on every existing sub-score** (the scorer reads none of these new fields in its `_avg`
  inputs). Add at the end of `snapshot_to_metrics`, guarded on `snap.events`.
- **`scoring.score()` / `ScoreCard`**: carry the flags + event list onto the card as informational
  fields. The scorer's six sub-scores, composite, and four gates are **untouched**.
- **Surfacing:**
  - `--json`: a new top-level `events` block per card (flags + `recent` list), emitted only when the
    name has in-window events (same "only when notable" discipline as the `coverage` block).
  - Screener table **`Flags` column** (`screen.py:_print_table` / `_print_plain`): append short chips
    — `13D`, `13G`, `8K`, `144` — distinct from the red **gate** chips (events are neutral/informational,
    rendered without the gate's `dim red` style).
  - **Research layer**: pass the event list into the brief context so a Claude brief can note, e.g.,
    "filed an SC 13D 6 days ago — possible activist involvement." (Wire where the research runner
    assembles per-ticker context; events are facts, surfaced as facts.)
- **Reserved, ships off:** a `config.yaml` block (§6) `edgar_events.soft_notes` defaulting to all-off,
  reserving a future non-blocking coverage *note* (e.g. "recent 8-K — snapshot may be stale"). Not
  wired into any gate or score in v1; documented so the extension point is obvious.

## 6. Config (`config.yaml`)

```yaml
edgar_events:
  lookback_days: 90
  index_limit: 40
  forms: ["8-K", "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A", "144"]
  soft_notes:            # reserved; all OFF in v1 (pure enrichment)
    stale_after_8k: false
    activist_13d_note: false
```

Read in `EdgarSource.__init__` (consistent with how `lookback_days` is already passed). Absent keys
fall back to the defaults above so existing configs keep working.

## 7. Testing

Mirrors the existing EDGAR test structure (pure builders tested without network; one isolation test;
round-trip and coverage invariance; a gated live smoke test for the external-contract caveat).

1. **`_build_events` pure tests** (no network): fixture record lists →
   - correct flag classification for each of the four form families (incl. `/A` amendments);
   - lookback boundary (a filing exactly at / just past `lookback_days`);
   - newest-first ordering of `recent`;
   - empty / all-out-of-window → returns `None`.
2. **Isolation test:** `_fetch_filings_index` raising leaves `insider` and `statements` intact and
   appends exactly one `edgar-events:` error (mirror of the financials-isolation test).
3. **Merge round-trip:** `events` survives `merge_snapshots` (provenance `["edgar"]`) and a
   `to_dict` → `from_dict` cycle, including `events.recent` rebuilt as `FilingEvent` instances.
4. **Coverage invariance:** `TickerSnapshot.coverage()` / `missing()` return identical values with and
   without an `events` section; an `edgar-events:` error maps to `edgar: error` in
   `snapshot_to_coverage_inputs`.
5. **Bridge propagation:** flags reach `StockMetrics`; assert all six sub-scores and the composite for
   a fixture snapshot are **byte-identical** with vs without events present (the zero-impact guarantee).
6. **Live smoke (gated, `@pytest.mark.live`, skipped by default):** real `EdgarSource` against a known
   filer to confirm the §3.1 form-string filter actually returns 13D/13G/8-K/144 rows.

## 8. Architecture fit & house rules

- **Two-layer fit:** harness-only `Source` extension — the same shape as `YahooSource`. No
  `providers/` change; the screener reaches events through `--engine harness`. Consistent with
  `CLAUDE.md`'s two-registry split.
- **Redaction:** event `url`s are public SEC index links carrying no key; any `edgar-events:` error
  string is appended through the same path as the existing EDGAR errors. No new secret-leak surface.
- **Honest coverage:** events never zero or inflate a sub-score, and never move the coverage
  denominator (§2.1, §4.2) — satisfying the "thin source lowers coverage, never silently zeroes"
  house rule by construction.

## 9. Files touched (implementation map)

| File | Change |
|---|---|
| `src/shortlist/data/models.py` | `FilingEvent`, `Events` dataclasses; `TickerSnapshot.events`; `_AUX_DEFAULTS`; `from_dict` events rebuild; `merge_snapshots` aux-section line |
| `src/shortlist/data/sources.py` | `EdgarSource.__init__` config; `_fetch_filings_index` (seam); `_build_events` (pure); events block in `_fetch_sync` |
| `src/shortlist/data/bridge.py` | copy flags + event list onto `StockMetrics` (guarded on `snap.events`) |
| `src/shortlist/models.py` | new `StockMetrics` fields: `recent_8k`, `activist_13d`, `passive_13g`, `planned_insider_sale_144`, `filing_events` |
| `src/shortlist/scoring.py` | carry flags/event list onto `ScoreCard` (no score/gate change) |
| `src/shortlist/screen.py` | `--json` events block; `Flags` column chips (table + plain) |
| research runner | pass event list into per-ticker brief context |
| `config.yaml` | `edgar_events` block (§6) |
| `tests/` | the six test groups in §7 |
| `docs/DATA_SOURCES.md` | mark A1 events half done; reconcile "what we pull today" |

## 10. Decision record (the candidates not chosen)

This spec is the output of a ranked evaluation of every `DATA_SOURCES.md` candidate against the
objective **differentiated edge**, constraint **free keys OK**. The full ranking, for the record:

| Rank | Candidate | Verdict |
|---|---|---|
| **1** | **EDGAR Events** (this spec) | **Chosen** — keyless, highest synergy, lowest build, closes gap #6 + 13D smart-money |
| 2 | Short interest (FINRA bulk, C1) | Strong runner-up; cleaner *numeric* signal but new pipeline, not yet validated in `scratch/` |
| 3 | Finnhub news + earnings-surprise (B2) | Cheapest win (keys in hand, validated) but less differentiated |
| 4 | 13F institutional flow (C3) | Deferred — high build (holdings→ticker inversion), quarterly/45-day-lagged |
| 5 | Attention: Wikimedia + GDELT (A4/A5) | Cheap, low-weight confirmation; later |
| 6 | Alpha Vantage (B1) | 25/day quota hostile; `eps_revision` already filled by Finnhub |
| — | Quiver (C2) | Deferred by cost constraint (best endpoints paid) |
| — | Tiingo (B3) | Off-objective (robustness, not edge) |
| — | Piotroski/Altman/Beneish (D1–D3) | Off-objective (analytical depth); strong under a different goal; need extra XBRL balance-sheet fields first |

The runners-up (short interest, then the Finnhub untapped endpoints) are the natural **next** edge
additions once this lands.

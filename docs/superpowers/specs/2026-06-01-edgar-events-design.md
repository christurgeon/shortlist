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

**Honest framing (post-adversarial-review):** "highest synergy" means *operational* reuse — the same
SEC source, the same `SEC_IDENTITY`, the same `edgartools` dependency, the same `_edgar_semaphore`
fair-access bound, and the same failure-isolation pattern as the financials block. It does **not** mean
shared code: the scout's Form-4 daily-index path (`scout/edgar_index.py`) and this per-ticker
`EdgarSource` path are parallel stacks with no common module. The cost is **~1 additional SEC
submissions fetch per ticker on average** — but a deep-history filer triggers edgartools'
paginated `_load_older_filings`, which is several `download_json` calls, so it is an average, not a
hard guarantee. CIK is re-resolved per call (a fresh `Company(ticker)`), exactly as the existing
`_fetch_insider` / `_fetch_financials_object` methods already do — there is no CIK cache to reuse.

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
  `Company(ticker).get_filings(form=self._event_forms)` and **normalizes the result to a plain list of
  `{form, filed, accession, url}` dicts** before returning (decoupling the pure builder from
  edgartools types). **`.latest(n)` is polymorphic and must be handled carefully**: it returns a
  single `EntityFiling` for `n == 1`, an `EntityFilings` collection for `n > 1`, and **`None` when
  there are zero matches** (live-confirmed, `edgar/entity/filings.py`). So `_fetch_filings_index` must
  guard: `res is None → return []`; a bare `EntityFiling` → wrap in a one-element list; a collection →
  iterate. (Simplest robust form: take the filtered `get_filings(...)` collection directly, slice to
  `index_limit`, and iterate — avoiding the `.latest` single/None polymorphism entirely.) Reuses the
  shared `_edgar_semaphore` — **no new rate-limiter** (SEC fair-access is already bounded). The
  per-filing attributes are network-free in-memory reads: `filing.form`, `filing.filing_date` (a real
  `date`), `filing.accession_no`, `filing.url` (a key-free `sec.gov/Archives/...` index link).
  `index_limit` default 40; the lookback filter in the builder is the real bound.
- **`_build_events(records)` — pure, unit-testable without network.** Filters to
  `filed >= today - lookback_days`, sorts newest-first, builds `FilingEvent`s, and sets the four
  booleans **strictly from the in-window `recent` list** (a bool is True iff `recent` contains a
  matching form). Returns `None` when there are no in-window filings. **Invariant (guards finding §4
  below): the four booleans are derived only from `recent`, so an empty `recent` ⇒ all booleans False
  ⇒ builder returns `None`.** It must never return a populated-but-all-falsy `Events`.

Config (`event_forms`, `event_lookback_days`, `index_limit`) reaches `EdgarSource.__init__` only after
the config-threading change described in §3.3 — it is **not** a localized `__init__` edit.

### 3.1 The form-string contract (live-verified in the adversarial pass)

The exact **form strings** edgartools' `get_filings(form=[...])` filter accepts were confirmed against
live SEC data (AAPL/HLF pulls):
- `form=` **accepts a list** and filters via **exact match** (`edgar/filtering.py`).
- The fetch defaults to **`amendments=True`, which auto-appends the `/A` variants** — so listing
  `"8-K"` also matches `"8-K/A"`, `"SC 13G"` matches `"SC 13G/A"`, etc. The forms list does **not**
  need to enumerate `/A` forms.
- Live pulls returned exactly `8-K`, `8-K/A`, `SC 13D`, `SC 13G`, `SC 13G/A`, `144`, `144/A`.
- **Caveat (real gap):** because the filter is exact-match, SEC's distinct XML-variant form values
  `SCHEDULE 13D` / `SCHEDULE 13G` (`edgar/reference/data/secforms.csv`) are **missed unless added to
  the forms list**. The builder's prefix classification only runs *after* the fetch, so it cannot
  recover forms the exact-match fetch excluded. **The §6 forms list therefore includes both the
  `SC 13D`/`SC 13G` and `SCHEDULE 13D`/`SCHEDULE 13G` spellings.**

The classifier in `_build_events` matches case-insensitively on a normalized prefix (startswith
`"SC 13D"` *or* `"SCHEDULE 13D"` → activist; `"SC 13G"`/`"SCHEDULE 13G"` → passive; exact `"8-K"` →
8-K; prefix `"144"` → planned sale), which also absorbs the `/A` amendments. The gated live smoke test
(§7) re-pins this against SEC if edgartools is upgraded.

### 3.2 Alternative considered (documented fallback)

The raw keyless `https://data.sec.gov/submissions/CIK{cik}.json` endpoint (already validated in
`scratch/raw/sec/submissions.json`, one httpx GET, returns every recent filing's form/date/accession)
is a cleaner and cheaper data source in isolation. It was **not** chosen for v1 because it requires its
own ticker→CIK map and a second rate-limiter outside `EdgarSource`'s thread/semaphore model, adding
surface for no signal gain. It remains the **documented fallback** if edgartools' form-filter
semantics prove unreliable: `_fetch_filings_index` is the single seam to swap, and `_build_events`
(consuming plain dicts) stays unchanged.

### 3.3 Config plumbing — a real change, not a localized edit (adversarial finding #1)

The spec originally implied `edgar_events` config is "read in `__init__`, consistent with how
`lookback_days` is passed." **That precedent does not exist.** Verified:
- `build_sources(names)` constructs every source as `_REGISTRY[n]()` — **no args, no config**
  (`sources.py:599`).
- `collect(tickers, source_names, priority)` takes **no config** (`collector.py`); `run_harness` calls
  `collect(tickers, source_names)` without forwarding the `config` it holds (`screen.py:63`).
- `EdgarSource.__init__`'s `lookback_days=183` is a **hardcoded default nothing overrides**
  (`sources.py:274`).

So `EdgarSource` currently has **zero access to config**. Threading `edgar_events` through requires a
deliberate change across the construction path. **Chosen approach:** add an optional `config: dict |
None = None` parameter to `build_sources` and `collect`/`collect_async`, forward it from `run_harness`,
and have `build_sources` pass `config` into each source constructor that accepts one (`EdgarSource`).
`EdgarSource.__init__` gains `config: dict | None = None` and reads `(config or {}).get("edgar_events",
{})` with the §6 defaults. This keeps every other source's zero-arg construction working (the param is
optional and ignored by sources that don't take it — `build_sources` introspects or passes via a small
try/except, matching the existing skip-on-error construction). This is a discrete implementation-plan
task, sequenced **before** the events fetch can be config-driven; until it lands, `_build_events` uses
the module-default forms/lookback.

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

**Sharp edge (adversarial finding #4):** `_pick_first` calls `_has_data`, which is
`any(v not in (None, [], "") ...)`. A `bool` `False` **is not** in `(None, [], "")`, so an
all-False/empty `Events(recent=[], recent_8k=False, ...)` would read as "has data" and be selected.
The **only** thing preventing this is the §3 builder invariant: `_build_events` returns `None` (not an
empty `Events`) when there are no in-window filings, and the booleans are derived strictly from a
non-empty `recent`. This is load-bearing, so it gets an explicit test (§7.1, §7.5): the builder must
never emit a populated-but-all-falsy `Events`. (We deliberately do *not* add a custom truthiness
method to `Events` — the None-discipline at the single construction site is simpler than a second
guard the merge would have to know about.)

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
  and `filing_events`. **They default to `None`, not `False`/`[]` (adversarial finding #8).** Reason:
  the screener `merge.merge` iterates *all* `StockMetrics` fields and stamps `out.sources[field]` for
  any value that `is not None` (`merge.py:25-46`). With `False`/`[]` defaults, the screener path (which
  never populates events) would pollute every record's `sources`/provenance map with phantom
  `recent_8k → <provider>` entries. Defaulting to `None` means the screener path leaves them `None`
  → unstamped. The bridge sets them explicitly (to real bools / the list) **only when `snap.events`
  is present**, guarded at the end of `snapshot_to_metrics`. Zero impact on every existing sub-score is
  still **provable**: `scoring.score()` reads none of these fields (verified `scoring.py:24-96`).
- **`scoring.score()` / `ScoreCard`**: carry the flags + event list onto the card as informational
  fields. The scorer's six sub-scores, composite, and four gates are **untouched**.
- **Surfacing:**
  - `--json`: a new top-level `events` block per card (flags + `recent` list), emitted only when the
    name has in-window events (same "only when notable" discipline as the `coverage` block). Confirm
    the actual JSON assembly site (it may live in `cli.py`, not `screen.py`) and emit there.
  - Screener table **`Flags` column** (`screen.py:_print_table` / `_print_plain`): append short chips
    — `13D`, `13G`, `8K`, `144`. **Note (adversarial finding #5): rich's `style=` on `add_row` is
    *row-level*** — the existing `style = "dim red" if c.gates else None` colors the whole row. To
    render event chips neutrally *alongside* a red gate chip in the same cell, build the `Flags` cell
    as a string with **inline rich markup** (e.g. gate chips wrapped in `[dim red]…[/]`, event chips
    plain) and drop the blanket row `style` for the flags interaction — or keep the row style for
    gates and accept that on a gated row the event chip inherits the row color. `_print_plain`
    (`screen.py:114-120`) has **no color at all**, so there the chips are plain text appended to the
    `FLAGS` field. Pick the inline-markup approach for the rich table; document the plain fallback.
  - **Research layer (adversarial finding #7 — concrete point):** the runner is
    `research/__init__.py:_enrich_card`; the prompt is built by `assess._build_user_prompt(filing,
    config)` (the `card` is currently unused). Integration is **two edits**: (a) the bridge already
    puts `filing_events` on `StockMetrics`, so it rides through on `card.metrics`; (b) extend
    `_build_user_prompt` to accept and inject `card.metrics.filing_events` (e.g. "Recent filings: SC
    13D filed 2026-05-26") into the prompt. Events are facts, surfaced as facts — not interpretive.
- **Reserved, ships off:** a `config.yaml` block (§6) `edgar_events.soft_notes` defaulting to all-off,
  reserving a future non-blocking coverage *note* (e.g. "recent 8-K — snapshot may be stale"). Not
  wired into any gate or score in v1; documented so the extension point is obvious.

## 6. Config (`config.yaml`)

```yaml
edgar_events:
  lookback_days: 90
  index_limit: 40
  # /A amendments are auto-included by edgartools (amendments=True); the SCHEDULE 13D/13G
  # spellings are listed explicitly because the fetch filter is exact-match (see §3.1).
  forms: ["8-K", "SC 13D", "SC 13G", "144", "SCHEDULE 13D", "SCHEDULE 13G"]
  soft_notes:            # reserved; all OFF in v1 (pure enrichment)
    stale_after_8k: false
    activist_13d_note: false
```

Read in `EdgarSource.__init__` **after** the config-threading change in §3.3 (it is not currently
reachable from config). Absent keys fall back to the module defaults above so existing configs keep
working.

## 7. Testing

Mirrors the existing EDGAR test structure (pure builders tested without network; one isolation test;
round-trip and coverage invariance; a gated live smoke test for the external-contract caveat).

1. **`_build_events` pure tests** (no network): fixture record lists →
   - correct flag classification for each of the four form families (incl. `/A` amendments and the
     `SCHEDULE 13D`/`SCHEDULE 13G` spellings);
   - lookback boundary (a filing exactly at / just past `lookback_days`);
   - newest-first ordering of `recent`;
   - empty / all-out-of-window → returns `None`;
   - **never returns a populated-but-all-falsy `Events`** (the load-bearing invariant from §3/§4):
     any non-`None` return has a non-empty `recent` and ≥1 True flag.
   - **`_fetch_filings_index` normalization** (with a stubbed edgartools seam): `None` result → `[]`;
     a single `EntityFiling` → one-element list; a collection → full list (adversarial finding #3).
2. **Isolation test:** `_fetch_filings_index` raising leaves `insider` and `statements` intact and
   appends exactly one `edgar-events:` error (mirror of the financials-isolation test).
3. **Merge round-trip:** `events` survives `merge_snapshots` (provenance `["edgar"]`) and a
   `to_dict` → `from_dict` cycle, including `events.recent` rebuilt as `FilingEvent` instances.
4. **Coverage invariance:** `TickerSnapshot.coverage()` / `missing()` return identical values with and
   without an `events` section; an `edgar-events:` error maps to `edgar: error` in
   `snapshot_to_coverage_inputs`.
5. **Bridge propagation:** flags reach `StockMetrics`; assert all six sub-scores and the composite for
   a fixture snapshot are **byte-identical** with vs without events present (the zero-impact guarantee).
6. **Live smoke (gated, skipped by default):** real `EdgarSource` against a known filer to confirm the
   §3.1 form-string filter actually returns 13D/13G/8-K/144 rows. **The marker must be registered
   first (adversarial finding #6):** the repo has **no** `[tool.pytest.ini_options]`, `pytest.ini`, or
   `conftest.py`, so a bare `@pytest.mark.live` would (a) raise `PytestUnknownMarkWarning` and (b)
   **run in CI by default, hitting SEC**. The plan must add a `conftest.py` that registers the `live`
   marker and skips it unless `--run-live` is passed (or sets `addopts = "-m 'not live'"` in
   `pyproject.toml`). This is a prerequisite task, not a free annotation.

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
| `src/shortlist/data/models.py` | `FilingEvent`, `Events` dataclasses; `TickerSnapshot.events`; `_AUX_DEFAULTS`; `from_dict` events + nested `events.recent` rebuild (mirror `insider.recent`); `merge_snapshots` aux-section line |
| `src/shortlist/data/sources.py` | `EdgarSource.__init__(config=…)`; `_fetch_filings_index` (seam, with None/single/collection normalization); `_build_events` (pure, None-discipline invariant); events block in `_fetch_sync`; **`build_sources(names, config=None)`** forwarding config to source constructors |
| `src/shortlist/data/collector.py` | `collect`/`collect_async` accept + forward `config` (§3.3) |
| `src/shortlist/screen.py` | `run_harness` forwards `config` into `collect`; `--json` events block (confirm site, may be `cli.py`); `Flags` column chips via inline rich markup (table) + plain text (`_print_plain`) |
| `src/shortlist/data/bridge.py` | copy flags + event list onto `StockMetrics`, guarded on `snap.events` |
| `src/shortlist/models.py` | new `StockMetrics` fields, **default `None`**: `recent_8k`, `activist_13d`, `passive_13g`, `planned_insider_sale_144`, `filing_events` |
| `src/shortlist/scoring.py` | carry flags/event list onto `ScoreCard` (no score/gate change) |
| `src/shortlist/research/assess.py` (+ `__init__.py`) | extend `_build_user_prompt` to inject `card.metrics.filing_events` (§5) |
| `config.yaml` | `edgar_events` block (§6) |
| `conftest.py` *(new)* + `pyproject.toml` | register the `live` pytest marker and default-skip it (§7.6) |
| `tests/` | the test groups in §7 |
| `docs/DATA_SOURCES.md` | mark A1 events half done; reconcile "what we pull today" |

**No code change needed (verified):** `store.py` / `accumulate.py` persist `events` automatically —
`store.save` uses `TickerSnapshot.to_dict()` (asdict recurses) and reload goes through `from_dict`,
which the `_AUX_DEFAULTS` block covers. `MockSource`/`mockdata.py` leave `events=None` (fine; the
default), so `--demo` shows no events — if a round-trip test wants a mock events fixture, add one to
`mockdata.py`, otherwise no change.

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

## 11. Reconciliation log (adversarial review)

An adversarial agent pressure-tested every technical claim against the code and live edgartools pulls.
Outcome and where each was folded in:

| # | Finding | Severity | Resolution |
|---|---|---|---|
| 1 | Config is **not** reachable in `EdgarSource.__init__`; needs threading through `build_sources`/`collect`/`run_harness` | BLOCKER | §3.3 added; §6 + §9 updated to make it a discrete prerequisite task |
| 2 | "~1 SEC call" is an average not a guarantee; "reuses CIK resolution" is false (no cache) | SHOULD-FIX | §0 "honest framing" + §3 reworded |
| 3 | `.latest(n)` is polymorphic (single / collection / `None`) | SHOULD-FIX | §3 normalization spec + §7.1 test |
| 4 | `_has_data` treats all-False `Events` as data; builder None-discipline is the only guard | SHOULD-FIX | §4.1 sharp-edge note + load-bearing invariant test (§7.1/§7.5) |
| 5 | rich `style=` is row-level — can't mix red gate + neutral event chips without inline markup | SHOULD-FIX | §5 inline-markup spec + plain-text fallback |
| 6 | `@pytest.mark.live` unregistered → would run in CI, hit SEC | SHOULD-FIX | §7.6 + `conftest.py`/`pyproject` task in §9 |
| 7 | Research integration is a 2-edit change (`_build_user_prompt` signature), not "pass the list" | SHOULD-FIX | §5 concrete integration point |
| 8 | `merge.py` would pollute `sources` map with phantom False/[] event fields | NITPICK | §5: `StockMetrics` event fields default `None` |
| 9 | `from_dict` must rebuild nested `events.recent` as `FilingEvent`s (not raw dicts) | NITPICK | §2.1 + §9 emphasized |
| 10 | Coverage invariance + `edgar-events:`→`edgar` reduction | CONFIRMED | no change (claim held) |
| 11 | Form-string contract: list accepted, exact-match, `amendments=True` auto-adds `/A`; **`SCHEDULE 13D/13G` missed unless listed** | CONFIRMED + gap | §3.1 rewritten; §6 forms list adds SCHEDULE spellings |
| 12 | MockSource has no events path | NITPICK | §9 note (default `None`, fine) |
| 13 | `store.py`/`accumulate.py` persistence auto-covered by to_dict/from_dict | INFO | §9 "no code change" note |
| 14 | Scout "synergy" is operational, not shared code | NITPICK | §0 honest framing |

The data-model invariants, the form-string contract, and the zero-score-impact guarantee survived
verification. The corrections above are all reflected in the sections; no open blockers remain.

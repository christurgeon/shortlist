# EDGAR Events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **STATUS (2026-06-01): CLEARED TO EXECUTE.** The short-interest work merged (PR #12, `8af64b6`); its
> `ScoreCard.flags` populate (`scoring.py:check_flags`) + render (`screen.py:_flags_cell`, `_card_dict`)
> mechanism is landed, so Task 8 now plugs into it conflict-free (event advisories append to
> `check_flags`; rendering is automatic). All anchors re-verified against the squashed main. Baseline
> suite green (313 passed / 3 skipped). Executing subagent-driven on a worktree.

**Goal:** Add a per-ticker `events` section to the harness `TickerSnapshot` — recent 8-K / SC 13D / SC 13G / Form 144 from the SEC filing index — surfaced as pure-enrichment soft flags (via `ScoreCard.flags`) in the screener table, a structured `--json` events block, and the research brief, with zero impact on any existing score or gate.

**Architecture:** A new failure-isolated section on `EdgarSource` (mirrors the existing financials block) feeds a new `Events` dataclass on `TickerSnapshot`. A module-level pure builder does all classification/filtering (unit-testable without network). The event flags ride to the scorer through the existing `bridge.snapshot_to_metrics` → `StockMetrics` → `ScoreCard.metrics` path; nothing in `scoring.py` changes. Reached from the screener via `--engine harness`, exactly like Yahoo.

**Tech Stack:** Python 3.12, `edgartools` (optional dep, already used by `EdgarSource`), `httpx`/async harness, `pytest`, `uv`.

**Spec:** `docs/superpowers/specs/2026-06-01-edgar-events-design.md` (read it first; this plan implements it including the §11 adversarial reconciliations).

> **IMPORTANT — tree moved since authoring (post-plan-review reconciliation).** The parallel
> short-interest work landed (`c1649f5`) and **already built the generic aux-section infrastructure**
> this plan needs: `_AUX_DEFAULTS` (a dict of non-coverage sections), the `from_dict` aux round-trip
> loop, and a generic `merge_snapshots` aux loop that merges every `_AUX_DEFAULTS` entry via
> `_pick_first`. So `events` is wired by **extending** that infrastructure, not building it:
> - Task 2 **adds `"events": Events` to the existing `_AUX_DEFAULTS`** (does not redefine it) and adds
>   **only** the nested `events.recent` rebuild to `from_dict` (the aux loop already exists).
> - Task 4 needs **no merge code** — the generic aux loop merges `events` for free once registered;
>   it keeps only regression tests.
> - Task 6 must also fix `tests/test_screen_engine.py::fake_collect` (it breaks when `run_harness`
>   passes `config=`).
> Line numbers below are approximate (the short-interest + fmp-429 commits shifted them); match on the
> quoted code, not the line.

---

## File structure

| File | Responsibility |
|---|---|
| `conftest.py` *(new, repo root)* | Register + default-skip the `live` pytest marker |
| `src/shortlist/data/models.py` | `FilingEvent` + `Events` dataclasses; `TickerSnapshot.events`; **extend** existing `_AUX_DEFAULTS` with `events`; add nested `events.recent` rebuild to `from_dict`. (The aux `from_dict` loop and the `merge_snapshots` aux loop already exist — no new merge code.) Pure builder lives in `sources.py`. |
| `src/shortlist/data/sources.py` | `classify_event_form` + `build_events_section` (pure); `EdgarSource.__init__(config=…)`; `_fetch_filings_index` (network seam + normalization); events block in `_fetch_sync`; `build_sources(names, config=…)` |
| `src/shortlist/data/collector.py` | `collect`/`collect_async` accept + forward `config` |
| `src/shortlist/screen.py` | `run_harness` forwards `config`; `_card_dict` events block; `Flags` column chips |
| `src/shortlist/models.py` | `StockMetrics` event fields (default `None`) |
| `src/shortlist/data/bridge.py` | copy flags + event dicts onto `StockMetrics` when `snap.events` present |
| `src/shortlist/research/assess.py` | inject `card.metrics.filing_events` into the prompt |
| `config.yaml` | `edgar_events` block |
| `tests/test_edgar_events.py` *(new)* | builder, model round-trip, merge, source-isolation, bridge, surfacing |
| `docs/DATA_SOURCES.md` | reconcile: mark A1 events half done |

**Sequencing note:** Tasks 1–5 have no dependency on config plumbing (they use module defaults). Task 6 threads real config. Tasks 7–9 are surfacing. Each task ends green and committed.

---

## Task 1: ✅ OBSOLETE — `live` marker already registered (no work)

The spec's adversarial finding #6 assumed no pytest config existed. **That is no longer true:** the
short-interest merge (PR #12) added to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
addopts = "-m 'not live'"
markers = ["live: hits real external APIs; skipped unless -m live"]
```

So the `live` marker is registered and live tests are **deselected by default**, run via
`uv run pytest -m live` (see `tests/test_short_interest.py`). **Do NOT add a `conftest.py` or a
`--run-live` option** — that creates a second, conflicting mechanism and breaks the documented
`-m live` workflow. This task requires no change; `tests/test_edgar_events.py` is created by Task 2.
Task 10's live smoke test just uses `@pytest.mark.live` and is gated by the existing `addopts`.

---

## Task 2: `FilingEvent` + `Events` dataclasses and `TickerSnapshot.events`

**Files:**
- Modify: `src/shortlist/data/models.py`
- Test: `tests/test_edgar_events.py`

- [ ] **Step 1: Write the failing round-trip test**

Append to `tests/test_edgar_events.py`:

```python
from shortlist.data.models import Events, FilingEvent, TickerSnapshot


def _sample_events():
    return Events(
        recent=[FilingEvent(form="SC 13D", filed="2026-05-26",
                            accession="0000-1", url="https://sec.gov/x")],
        activist_13d=True,
    )


def test_events_roundtrips_through_to_from_dict():
    snap = TickerSnapshot(ticker="AAPL")
    snap.events = _sample_events()
    rebuilt = TickerSnapshot.from_dict(snap.to_dict())
    assert rebuilt.events is not None
    assert rebuilt.events.activist_13d is True
    assert len(rebuilt.events.recent) == 1
    assert isinstance(rebuilt.events.recent[0], FilingEvent)
    assert rebuilt.events.recent[0].form == "SC 13D"


def test_events_does_not_affect_coverage():
    bare = TickerSnapshot(ticker="AAPL")
    withev = TickerSnapshot(ticker="AAPL")
    withev.events = _sample_events()
    assert bare.coverage() == withev.coverage()
    assert bare.missing() == withev.missing()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_edgar_events.py -k events_ -v`
Expected: FAIL — `ImportError: cannot import name 'Events'`.

- [ ] **Step 3: Add the dataclasses and the field**

In `src/shortlist/data/models.py`, after the `Price` class (before the `# --- Snapshot ---` divider) add:

```python
@dataclass
class FilingEvent:
    form: str                          # "8-K", "SC 13D", "SC 13G", "144", ...
    filed: str                         # ISO date (filing date)
    accession: Optional[str] = None
    url: Optional[str] = None          # public SEC index URL — carries no key


@dataclass
class Events:
    """Recent SEC filing-stream events (enrichment, not a scored section)."""
    recent: list[FilingEvent] = field(default_factory=list)  # in-window, newest-first
    recent_8k: bool = False
    activist_13d: bool = False         # SC 13D / SCHEDULE 13D (and /A) in window
    passive_13g: bool = False          # SC 13G / SCHEDULE 13G (and /A) in window
    planned_insider_sale_144: bool = False  # Form 144 (and /A) in window
```

Add the field to `TickerSnapshot` immediately after the existing `short_interest` aux field (keep aux sections grouped):

```python
    short_interest: Optional["ShortInterest"] = None   # auxiliary — NOT a KEY_OBJECT (sparse signal)
    events: Optional[Events] = None    # auxiliary — NOT a KEY_OBJECT (see _AUX_DEFAULTS)
```

- [ ] **Step 4: Extend `_AUX_DEFAULTS` and add the nested `events.recent` rebuild**

`_AUX_DEFAULTS` already exists (short-interest work). **Extend** it — do not redefine — to add `events`:

```python
_AUX_DEFAULTS = {"short_interest": ShortInterest, "events": Events}
```

The `from_dict` aux loop (`for name, klass in _AUX_DEFAULTS.items(): ...`) already exists and will now also rebuild the top-level `events` object. **Only the nested list needs help:** after the existing `insider.recent` rebuild block in `from_dict`, add the `events.recent` rebuild (mirrors `insider.recent`):

```python
        ev = d.get("events")
        if snap.events is not None and ev and ev.get("recent"):
            snap.events.recent = [_build(FilingEvent, e) for e in ev["recent"]]
```

> Do **not** add another `_AUX_DEFAULTS` loop — it already runs. Adding a second definition of `_AUX_DEFAULTS` would drop `short_interest` from round-trip and break `tests/test_short_interest.py`.

- [ ] **Step 5: Run to verify the tests pass**

Run: `uv run pytest tests/test_edgar_events.py -k events_ -v`
Expected: PASS (both tests).

- [ ] **Step 6: Run the full suite to confirm no regression**

Run: `uv run pytest -q`
Expected: all pass (existing model/coverage tests unaffected — `events` is outside `KEY_OBJECTS`).

- [ ] **Step 7: Commit**

```bash
git add src/shortlist/data/models.py tests/test_edgar_events.py
git commit -m "feat: add Events/FilingEvent sections to TickerSnapshot (aux, non-coverage)"
```

---

## Task 3: Pure event builder (`classify_event_form` + `build_events_section`)

All classification/lookback logic lives in pure module-level functions so it tests without a network. This is where adversarial findings #3 (normalization is separate, in Task 5), #4 (never emit all-falsy), and #11 (SCHEDULE spellings, /A amendments) are enforced.

**Files:**
- Modify: `src/shortlist/data/sources.py`
- Test: `tests/test_edgar_events.py`

- [ ] **Step 1: Write the failing builder tests**

Append to `tests/test_edgar_events.py`:

```python
from datetime import date

from shortlist.data.sources import build_events_section, classify_event_form


def _rec(form, filed, acc="a", url="u"):
    return {"form": form, "filed": filed, "accession": acc, "url": url}


def test_classify_covers_all_families_and_variants():
    assert classify_event_form("8-K") == "recent_8k"
    assert classify_event_form("8-K/A") == "recent_8k"
    assert classify_event_form("SC 13D") == "activist_13d"
    assert classify_event_form("SC 13D/A") == "activist_13d"
    assert classify_event_form("SCHEDULE 13D") == "activist_13d"
    assert classify_event_form("SC 13G") == "passive_13g"
    assert classify_event_form("SCHEDULE 13G/A") == "passive_13g"
    assert classify_event_form("144") == "planned_insider_sale_144"
    assert classify_event_form("144/A") == "planned_insider_sale_144"
    assert classify_event_form("10-K") is None


def test_build_filters_by_lookback_and_sets_flags():
    today = date(2026, 6, 1)
    recs = [
        _rec("8-K", "2026-05-20"),
        _rec("SC 13D", "2026-04-01"),
        _rec("10-K", "2026-05-15"),          # not an event form -> dropped
        _rec("144", "2026-01-01"),           # outside 90d window -> dropped
    ]
    ev = build_events_section(recs, lookback_days=90, today=today)
    assert ev is not None
    assert ev.recent_8k is True
    assert ev.activist_13d is True
    assert ev.planned_insider_sale_144 is False     # the only 144 was out of window
    assert [e.form for e in ev.recent] == ["8-K", "SC 13D"]   # newest-first, in-window only


def test_build_returns_none_when_no_inwindow_events():
    today = date(2026, 6, 1)
    assert build_events_section([], 90, today) is None
    assert build_events_section([_rec("8-K", "2020-01-01")], 90, today) is None
    assert build_events_section([_rec("10-K", "2026-05-30")], 90, today) is None


def test_build_never_returns_all_falsy_events():
    # Load-bearing invariant (spec §4): a non-None return always has a True flag.
    today = date(2026, 6, 1)
    ev = build_events_section([_rec("8-K", "2026-05-30")], 90, today)
    assert ev is not None
    assert any([ev.recent_8k, ev.activist_13d, ev.passive_13g,
                ev.planned_insider_sale_144])
    assert ev.recent  # and recent is non-empty
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_edgar_events.py -k "classify or build_" -v`
Expected: FAIL — `ImportError: cannot import name 'build_events_section'`.

- [ ] **Step 3: Implement the pure builder**

In `src/shortlist/data/sources.py`, near the top-level helpers (after the imports / module constants, before `class EdgarSource`), add. Ensure `from datetime import date, timedelta` is imported at module top (it already imports `date, timedelta` — confirm; if not, add).

```python
from .models import Events, FilingEvent  # add to existing models import if not already present

# form prefix (upper) -> Events boolean attribute. Prefix match absorbs /A amendments;
# both the "SC 13x" and SEC's "SCHEDULE 13x" spellings are handled (exact-match fetch
# can return either; see spec §3.1).
_EVENT_FORM_PREFIXES = (
    ("SC 13D", "activist_13d"), ("SCHEDULE 13D", "activist_13d"),
    ("SC 13G", "passive_13g"), ("SCHEDULE 13G", "passive_13g"),
    ("8-K", "recent_8k"),
    ("144", "planned_insider_sale_144"),
)


def classify_event_form(form: str) -> Optional[str]:
    """Map a filing form string to its Events flag attribute, or None if not an
    event form. Case-insensitive prefix match (captures /A amendments)."""
    f = (form or "").strip().upper()
    for prefix, attr in _EVENT_FORM_PREFIXES:
        if f.startswith(prefix):
            return attr
    return None


def build_events_section(records: list[dict], lookback_days: int,
                         today: date) -> Optional[Events]:
    """Pure: filter records to the lookback window, classify, and build an Events.
    Returns None when there are no in-window event filings — NEVER an all-falsy
    Events (load-bearing for the merge's _has_data check; spec §4)."""
    cutoff = today - timedelta(days=lookback_days)
    kept: list[tuple[str, FilingEvent]] = []
    for r in records:
        attr = classify_event_form(r.get("form", ""))
        if attr is None:
            continue
        filed = r.get("filed")
        try:
            if date.fromisoformat(filed) < cutoff:
                continue
        except (TypeError, ValueError):
            continue
        kept.append((attr, FilingEvent(
            form=r.get("form", ""), filed=filed,
            accession=r.get("accession"), url=r.get("url"))))
    if not kept:
        return None
    kept.sort(key=lambda p: p[1].filed, reverse=True)   # newest-first
    ev = Events(recent=[fe for _, fe in kept])
    for attr, _ in kept:
        setattr(ev, attr, True)
    return ev
```

- [ ] **Step 4: Run to verify the tests pass**

Run: `uv run pytest tests/test_edgar_events.py -k "classify or build_" -v`
Expected: PASS (all four tests).

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/data/sources.py tests/test_edgar_events.py
git commit -m "feat: pure EDGAR event builder (classify + lookback, None-discipline)"
```

---

## Task 4: Verify `events` merges through the existing aux loop (no source change)

The generic `merge_snapshots` aux loop already merges every `_AUX_DEFAULTS` entry via `_pick_first` + provenance. Once Task 2 registered `events` in `_AUX_DEFAULTS`, events merge for free. This task adds **regression tests only** — no source edit. (The `_build_events` None-discipline from Task 3 guarantees a present `Events` is never all-falsy, so `_pick_first`/`_has_data` can't select an empty section.)

**Files:**
- Test: `tests/test_edgar_events.py`

- [ ] **Step 1: Write the merge regression tests**

Append to `tests/test_edgar_events.py`:

```python
from shortlist.data.models import SourceResult, merge_snapshots


def test_events_merge_picks_edgar_and_records_provenance():
    edgar = SourceResult(source="edgar")
    edgar.partial = TickerSnapshot(ticker="AAPL")
    edgar.partial.events = _sample_events()
    fmp = SourceResult(source="fmp")
    fmp.partial = TickerSnapshot(ticker="AAPL")          # no events
    merged = merge_snapshots("AAPL", [fmp, edgar], priority=["yahoo", "edgar", "fmp"])
    assert merged.events is not None
    assert merged.events.activist_13d is True
    assert merged.provenance["events"] == ["edgar"]


def test_merge_without_events_leaves_section_none():
    fmp = SourceResult(source="fmp")
    fmp.partial = TickerSnapshot(ticker="AAPL")
    merged = merge_snapshots("AAPL", [fmp], priority=["fmp"])
    assert merged.events is None
    assert "events" not in merged.provenance
```

- [ ] **Step 2: Run to verify the tests pass (against the existing aux loop)**

Run: `uv run pytest tests/test_edgar_events.py -k merge -v`
Expected: PASS (both). If they fail, Task 2's `_AUX_DEFAULTS` extension was not applied correctly — fix that, do not add merge code here.

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_edgar_events.py
git commit -m "test: regression-cover EDGAR events merge via the aux loop"
```

---

## Task 5: `EdgarSource` — fetch events as a failure-isolated section

**Files:**
- Modify: `src/shortlist/data/sources.py` (`EdgarSource`)
- Test: `tests/test_edgar_events.py`

- [ ] **Step 1: Write the failing isolation + normalization tests**

Append to `tests/test_edgar_events.py`:

```python
from datetime import date as _date

from shortlist.data.models import Insider, SourceResult
from shortlist.data.sources import EdgarSource


class _FakeFiling:
    """edgartools EntityFiling-like (has .form, so the normalizer treats it as single)."""
    def __init__(self, form, d):
        self.form = form
        self.filing_date = d           # a datetime.date (has .isoformat)
        self.accession_no = "acc"
        self.url = "https://sec.gov/x"


class _StubEdgar(EdgarSource):
    """EdgarSource with network seams stubbed; bypasses __init__/identity. Overrides
    only `_raw_filings` so the REAL `_fetch_filings_index` normalization is exercised."""
    def __init__(self, *, raw=None, insider_snap=None, raise_index=False):
        self._raw = raw
        self._insider_snap = insider_snap
        self._raise_index = raise_index
        self._event_forms = ["8-K", "SC 13D"]
        self._event_lookback_days = 90
        self._index_limit = 40

    def _fetch_insider(self, ticker):
        res = SourceResult(source="edgar")
        res.partial = self._insider_snap or TickerSnapshot(ticker=ticker)
        return res

    def _fetch_financials_object(self, ticker):
        raise RuntimeError("financials skipped in this test")

    def _raw_filings(self, ticker):
        if self._raise_index:
            raise RuntimeError("SEC down")
        return self._raw


def test_events_failure_does_not_drop_insider():
    snap = TickerSnapshot(ticker="AAPL")
    snap.insider = Insider(net_value_6m=1.0, buy_count=1, sell_count=0)
    src = _StubEdgar(insider_snap=snap, raise_index=True)
    res = src._fetch_sync("AAPL")
    assert res.partial.insider.net_value_6m == 1.0          # insider survived
    assert res.partial.events is None
    assert any("edgar-events:" in e for e in res.errors)


def test_events_populate_from_index():
    src = _StubEdgar(raw=[_FakeFiling("8-K", _date.today())])  # today => always in-window
    res = src._fetch_sync("AAPL")
    assert res.partial.events is not None
    assert res.partial.events.recent_8k is True
    assert res.partial.events.recent[0].form == "8-K"


def test_fetch_filings_index_normalizes_none_single_collection():
    src = _StubEdgar()
    src._raw = None                                          # None -> []
    assert src._fetch_filings_index("AAPL") == []
    src._raw = _FakeFiling("8-K", _date(2026, 5, 30))        # single -> one-element list
    out = src._fetch_filings_index("AAPL")
    assert len(out) == 1 and out[0]["form"] == "8-K" and out[0]["filed"] == "2026-05-30"
    src._raw = [_FakeFiling("8-K", _date(2026, 5, 30)),      # collection -> full list
                _FakeFiling("144", _date(2026, 5, 1))]
    assert [r["form"] for r in src._fetch_filings_index("AAPL")] == ["8-K", "144"]
```

> The stub overrides only `_raw_filings`, so the real `_fetch_filings_index` (None/single/collection
> normalization) and the real `_fetch_sync` isolation path are exercised without any network.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_edgar_events.py -k "events_failure or populate_from_index or normalizes" -v`
Expected: FAIL — `_raw_filings`/`_fetch_filings_index`/events handling not implemented yet.

- [ ] **Step 3: Implement the EdgarSource changes**

In `src/shortlist/data/sources.py`, change `EdgarSource.__init__` to accept config and derive event settings (module defaults when absent):

```python
    def __init__(self, identity: Optional[str] = None, lookback_days: int = 183,
                 config: Optional[dict] = None):
        self.identity = identity or os.environ.get("SEC_IDENTITY")
        if not self.identity:
            raise RuntimeError("SEC_IDENTITY (a contact email) is required by the SEC")
        self.lookback_days = lookback_days
        ev = (config or {}).get("edgar_events", {})
        self._event_forms = ev.get(
            "forms", ["8-K", "SC 13D", "SC 13G", "144", "SCHEDULE 13D", "SCHEDULE 13G"])
        self._event_lookback_days = ev.get("lookback_days", 90)
        self._index_limit = ev.get("index_limit", 40)
        from edgar import set_identity
        set_identity(self.identity)
```

Add three methods to `EdgarSource` (the network seam, a record extractor, and the pure-builder bridge):

```python
    def _raw_filings(self, ticker: str):
        """Network seam (mockable): the filtered edgartools filings object."""
        from edgar import Company
        return Company(ticker).get_filings(form=self._event_forms)

    def _fetch_filings_index(self, ticker: str) -> list[dict]:
        """Normalize the edgartools result (None | single EntityFiling | collection)
        into a plain list of {form, filed, accession, url} dicts. Adversarial #3."""
        res = self._raw_filings(ticker)
        if res is None:
            return []
        items = res if hasattr(res, "__iter__") and not hasattr(res, "form") else [res]
        out: list[dict] = []
        for f in list(items)[: self._index_limit]:
            fd = getattr(f, "filing_date", None)
            out.append({
                "form": getattr(f, "form", "") or "",
                "filed": fd.isoformat() if hasattr(fd, "isoformat") else (fd or ""),
                "accession": getattr(f, "accession_no", None),
                "url": getattr(f, "url", None),
            })
        return out

    def _build_events_from_records(self, records: list[dict]):
        return build_events_section(records, self._event_lookback_days, date.today())
```

Add the isolated events block in `_fetch_sync` (after the financials `try/except`):

```python
        # Events are isolated: a failure here must never drop insider/statements.
        try:
            ev = self._build_events_from_records(self._fetch_filings_index(ticker))
            if ev is not None:
                res.partial.events = ev
        except Exception as e:
            res.errors.append(f"edgar-events: {e}")
        return res
```

- [ ] **Step 4: Run to verify the tests pass**

Run: `uv run pytest tests/test_edgar_events.py -k "events_failure or populate_from_index or normalizes" -v`
Expected: PASS (all three).

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/shortlist/data/sources.py tests/test_edgar_events.py
git commit -m "feat: EdgarSource fetches events as a failure-isolated section"
```

---

## Task 6: Thread `config` to `EdgarSource` (build_sources → collect → run_harness)

Adversarial finding #1: `EdgarSource` currently has no path to config. This task wires it so the `edgar_events` block in `config.yaml` actually reaches the source.

**Files:**
- Modify: `src/shortlist/data/sources.py` (`build_sources`)
- Modify: `src/shortlist/data/collector.py` (`collect`, `collect_async`)
- Modify: `src/shortlist/screen.py` (`run_harness`)
- Test: `tests/test_edgar_events.py`

- [ ] **Step 1: Write the failing config-plumbing test**

Append to `tests/test_edgar_events.py`:

```python
import os

from shortlist.data.sources import build_sources


def test_build_sources_passes_config_to_edgar(monkeypatch):
    monkeypatch.setenv("SEC_IDENTITY", "test@example.com")
    monkeypatch.setattr("edgar.set_identity", lambda *_a, **_k: None)
    cfg = {"edgar_events": {"lookback_days": 7, "forms": ["8-K"], "index_limit": 5}}
    sources = build_sources(["edgar"], config=cfg)
    edgar = [s for s in sources if s.name == "edgar"][0]
    assert edgar._event_lookback_days == 7
    assert edgar._event_forms == ["8-K"]
    assert edgar._index_limit == 5


def test_build_sources_without_config_uses_defaults(monkeypatch):
    monkeypatch.setenv("SEC_IDENTITY", "test@example.com")
    monkeypatch.setattr("edgar.set_identity", lambda *_a, **_k: None)
    edgar = [s for s in build_sources(["edgar"]) if s.name == "edgar"][0]
    assert edgar._event_lookback_days == 90
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_edgar_events.py -k build_sources -v`
Expected: FAIL — `build_sources() got an unexpected keyword argument 'config'`.

- [ ] **Step 3: Make `build_sources` config-aware**

In `src/shortlist/data/sources.py`, replace `build_sources`:

```python
def build_sources(names: list[str], config: Optional[dict] = None) -> list[Source]:
    out, skipped = [], []
    for n in names:
        if n not in _REGISTRY:
            raise ValueError(f"unknown source '{n}'. Known: {list(_REGISTRY)}")
        cls = _REGISTRY[n]
        try:
            # Only sources whose __init__ accepts `config` receive it; others stay zero-arg.
            import inspect
            if "config" in inspect.signature(cls.__init__).parameters:
                out.append(cls(config=config))
            else:
                out.append(cls())
        except Exception as e:
            skipped.append(f"{n} ({redact_secrets(str(e))})")
    if skipped:
        print(f"  ! skipped sources: {', '.join(skipped)}")
    return out
```

- [ ] **Step 4: Forward config through the collector**

In `src/shortlist/data/collector.py`, thread `config` (the `collect_async` signature already takes built `sources`, so config only matters at `collect`, which builds them):

```python
def collect(
    tickers: list[str],
    source_names: list[str],
    priority: list[str] | None = None,
    config: dict | None = None,
) -> list[TickerSnapshot]:
    sources = build_sources(source_names, config=config)
    if not sources:
        return []
    return asyncio.run(collect_async([t.upper() for t in tickers], sources, priority))
```

In `src/shortlist/screen.py`, `run_harness`, change the collect call:

```python
    snapshots = collect(tickers, source_names, config=config)
```

- [ ] **Step 5: Fix the existing `fake_collect` test stub (it breaks on the new `config=`)**

`tests/test_screen_engine.py` monkeypatches `collect` with a `fake_collect(tickers, source_names)` that has no `config` param — `run_harness` now passes `config=`, raising `TypeError`. Update its signature to accept and ignore config:

```python
    def fake_collect(tickers, source_names, config=None):
        ...   # body unchanged
```

> Note: `src/shortlist/data/cli.py` (the `shortlist-harness` CLI) calls `collect(tickers, sources)` and never loads `config.yaml`. It is **intentionally left on module-default event settings** — its defaults equal the `config.yaml` defaults, so no behavior changes. Threading config into a CLI that has none is out of scope here.

- [ ] **Step 6: Run to verify the tests pass**

Run: `uv run pytest tests/test_edgar_events.py -k build_sources tests/test_screen_engine.py -v`
Expected: PASS.

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass (existing `collect`/`build_sources` callers use defaults).

- [ ] **Step 8: Commit**

```bash
git add src/shortlist/data/sources.py src/shortlist/data/collector.py src/shortlist/screen.py tests/test_screen_engine.py tests/test_edgar_events.py
git commit -m "feat: thread config to EdgarSource via build_sources/collect/run_harness"
```

---

## Task 7: Bridge event flags onto `StockMetrics` (default None)

**Files:**
- Modify: `src/shortlist/models.py` (`StockMetrics`)
- Modify: `src/shortlist/data/bridge.py`
- Test: `tests/test_edgar_events.py`

- [ ] **Step 1: Write the failing bridge tests**

Append to `tests/test_edgar_events.py`:

```python
from shortlist.data.bridge import snapshot_to_metrics
from shortlist.scoring import score


def _min_config():
    import yaml, pathlib
    return yaml.safe_load((pathlib.Path(__file__).parent.parent / "config.yaml").read_text())


def test_bridge_copies_events_when_present():
    snap = TickerSnapshot(ticker="AAPL")
    snap.events = _sample_events()
    m = snapshot_to_metrics(snap)
    assert m.activist_13d is True
    assert m.recent_8k is False
    assert m.filing_events == [
        {"form": "SC 13D", "filed": "2026-05-26", "accession": "0000-1", "url": "https://sec.gov/x"}]


def test_bridge_leaves_events_none_when_absent():
    m = snapshot_to_metrics(TickerSnapshot(ticker="AAPL"))
    assert m.activist_13d is None
    assert m.filing_events is None


def test_events_have_zero_score_impact():
    snap = TickerSnapshot(ticker="AAPL")
    config = _min_config()
    before = score(snapshot_to_metrics(snap), config)
    snap.events = _sample_events()
    after = score(snapshot_to_metrics(snap), config)
    assert (before.composite, before.quality, before.moat, before.growth,
            before.momentum, before.value, before.insider) == \
           (after.composite, after.quality, after.moat, after.growth,
            after.momentum, after.value, after.insider)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_edgar_events.py -k "bridge or zero_score" -v`
Expected: FAIL — `AttributeError: 'StockMetrics' object has no attribute 'activist_13d'`.

- [ ] **Step 3: Add the StockMetrics fields (default None)**

In `src/shortlist/models.py`, in `StockMetrics`, after the insider-activity fields and before `sources`, add:

```python
    # Filing-stream events (enrichment only; NOT scored — default None so the
    # screener merge.py never stamps phantom provenance for them). Set by the
    # harness bridge when snap.events is present.
    recent_8k: Optional[bool] = None
    activist_13d: Optional[bool] = None
    passive_13g: Optional[bool] = None
    planned_insider_sale_144: Optional[bool] = None
    filing_events: Optional[list] = None   # list of {form, filed, accession, url} dicts
```

- [ ] **Step 4: Copy events in the bridge**

In `src/shortlist/data/bridge.py`, add `import dataclasses` at top if absent, and at the very end of `snapshot_to_metrics` (just before `return m`) add:

```python
    ev = snap.events
    if ev is not None:
        m.recent_8k = ev.recent_8k
        m.activist_13d = ev.activist_13d
        m.passive_13g = ev.passive_13g
        m.planned_insider_sale_144 = ev.planned_insider_sale_144
        m.filing_events = [dataclasses.asdict(e) for e in ev.recent]
```

- [ ] **Step 5: Run to verify the tests pass**

Run: `uv run pytest tests/test_edgar_events.py -k "bridge or zero_score" -v`
Expected: PASS (all three).

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/shortlist/models.py src/shortlist/data/bridge.py tests/test_edgar_events.py
git commit -m "feat: bridge EDGAR event flags onto StockMetrics (enrichment, default None)"
```

---

## Task 8: Surface events via `ScoreCard.flags` + a `--json` events block

The short-interest flags mechanism has **landed** (PR #12, `8af64b6`) and is exactly the shared path to
reuse — no collision:
- `scoring.py:check_flags(m, f)` returns soft advisories (e.g. `["crowded_short"]`), called in `score()`
  as `flags=check_flags(m, config.get("flags") or {})` → populates `ScoreCard.flags`.
- **Rendering is automatic:** `screen.py:_flags_cell(c)` = `",".join(list(c.gates) + list(c.flags)) or "-"`,
  used by **both** `_print_table` and `_print_plain`; and `_card_dict` already emits `"flags": c.flags`.

So event advisories just append to `check_flags`'s output (renders everywhere for free), and the only
`screen.py` edit is an additive structured `--json` `events` block (richer than the flag labels). **No
`_event_chips`, no `_print_table`/`_print_plain` change.**

**Files:**
- Modify: `src/shortlist/scoring.py` (`check_flags` — append event advisories)
- Modify: `src/shortlist/screen.py` (`_card_dict` — structured events block)
- Test: `tests/test_edgar_events.py`

- [ ] **Step 1: Write the failing flags + json tests**

Append to `tests/test_edgar_events.py`:

```python
from shortlist.models import ScoreCard, StockMetrics
from shortlist.scoring import check_flags
from shortlist.screen import _card_dict


def _metrics_with_events():
    m = StockMetrics(ticker="AAPL")
    m.activist_13d = True
    m.recent_8k = True
    m.filing_events = [{"form": "SC 13D", "filed": "2026-05-26", "accession": "x", "url": "u"}]
    return m


def test_check_flags_emits_event_advisories():
    flags = check_flags(_metrics_with_events(), {})
    assert "activist_13d" in flags
    assert "recent_8k" in flags
    assert "passive_13g" not in flags          # not set


def test_check_flags_no_events_no_advisories():
    assert check_flags(StockMetrics(ticker="AAPL"), {}) == []


def _card_with_events():
    return ScoreCard(ticker="AAPL", composite=50.0, quality=None, moat=None, growth=None,
                     momentum=None, value=None, opportunity=None, insider=None,
                     metrics=_metrics_with_events())


def test_card_dict_emits_events_block_only_when_present():
    assert _card_dict(_card_with_events())["events"]["recent"][0]["form"] == "SC 13D"
    plain = ScoreCard(ticker="AAPL", composite=50.0, quality=None, moat=None, growth=None,
                      momentum=None, value=None, opportunity=None, insider=None,
                      metrics=StockMetrics(ticker="AAPL"))
    assert "events" not in _card_dict(plain)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_edgar_events.py -k "check_flags or card_dict_emits" -v`
Expected: FAIL — `assert "activist_13d" in flags` (check_flags doesn't emit them) and `"events"` KeyError.

- [ ] **Step 3: Append event advisories to `check_flags`**

In `src/shortlist/scoring.py`, in `check_flags`, before `return out`, add:

```python
    # Filing-stream event advisories (set by the harness bridge; None on the screener
    # path, so this is a no-op there). Presence-based — no config thresholds.
    for attr in ("activist_13d", "recent_8k", "passive_13g", "planned_insider_sale_144"):
        if getattr(m, attr, None):
            out.append(attr)
```

- [ ] **Step 4: Add the structured `--json` events block**

In `src/shortlist/screen.py`, in `_card_dict`, before `return d`, add:

```python
    if c.metrics is not None and c.metrics.filing_events:
        d["events"] = {
            "recent_8k": bool(c.metrics.recent_8k),
            "activist_13d": bool(c.metrics.activist_13d),
            "passive_13g": bool(c.metrics.passive_13g),
            "planned_insider_sale_144": bool(c.metrics.planned_insider_sale_144),
            "recent": c.metrics.filing_events,
        }
```

- [ ] **Step 5: Run to verify the tests pass**

Run: `uv run pytest tests/test_edgar_events.py -k "check_flags or card_dict_emits" -v`
Expected: PASS (all four).

- [ ] **Step 6: Smoke-test the table renders (offline)**

Run: `uv run shortlist --demo`
Expected: table prints; `Flags` column shows gate/flag chips as before (mock has no events). No error.

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/shortlist/scoring.py src/shortlist/screen.py tests/test_edgar_events.py
git commit -m "feat: surface EDGAR events via ScoreCard.flags + --json events block"
```

---

## Task 9: Inject events into the research brief prompt

**Files:**
- Modify: `src/shortlist/research/assess.py` (`_build_user_prompt`, `assess`)
- Test: `tests/test_edgar_events.py`

- [ ] **Step 1: Write the failing prompt test**

Append to `tests/test_edgar_events.py`:

```python
from shortlist.research.assess import _build_user_prompt
from shortlist.research.models import FilingText


def _filing():
    return FilingText(ticker="AAPL", accession="acc", filing_date="2026-05-01",
                      business="b", mda="m", risk_factors="r")


def test_prompt_includes_recent_filings_when_events_present():
    events = [{"form": "SC 13D", "filed": "2026-05-26", "accession": "x", "url": "u"}]
    p = _build_user_prompt(_filing(), {}, filing_events=events)
    assert "Recent SEC filings" in p
    assert "SC 13D" in p and "2026-05-26" in p


def test_prompt_unchanged_when_no_events():
    base = _build_user_prompt(_filing(), {})
    assert "Recent SEC filings" not in base
```

> If `FilingText`'s constructor differs, adjust the fixture to its real fields (check `src/shortlist/research/models.py`); the assertion targets are what matter.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_edgar_events.py -k prompt -v`
Expected: FAIL — `_build_user_prompt() got an unexpected keyword argument 'filing_events'`.

- [ ] **Step 3: Extend the prompt builder and caller**

In `src/shortlist/research/assess.py`, change `_build_user_prompt` to accept events and append a facts line:

```python
def _build_user_prompt(filing: FilingText, config: dict,
                       filing_events: Optional[list] = None) -> str:
    rcfg = config.get("research", {})
    events_line = ""
    if filing_events:
        items = "; ".join(f"{e['form']} filed {e['filed']}" for e in filing_events[:6])
        events_line = (
            "\n\nRecent SEC filings (context only — do not treat as 10-K text): "
            f"{items}.")
    return (
        f"Ticker: {filing.ticker}\nAccession: {filing.accession}\n\n"
        f"=== ITEM 1 — BUSINESS ===\n{filing.business}\n\n"
        f"=== ITEM 7 — MD&A ===\n{filing.mda}\n\n"
        f"=== ITEM 1A — RISK FACTORS ===\n{filing.risk_factors}\n\n"
        f"Return at most {rcfg.get('max_risks', 8)} risks and "
        f"{rcfg.get('max_red_flags', 8)} red_flags, most material first."
        f"{events_line}"
    )
```

In `assess`, pass the card's events through (the `card` param is already in scope):

```python
    fe = getattr(getattr(card, "metrics", None), "filing_events", None)
    user_prompt = _build_user_prompt(filing, config, filing_events=fe)
```

- [ ] **Step 4: Run to verify the tests pass**

Run: `uv run pytest tests/test_edgar_events.py -k prompt -v`
Expected: PASS (both).

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass (existing research tests still pass — events default None).

- [ ] **Step 6: Commit**

```bash
git add src/shortlist/research/assess.py tests/test_edgar_events.py
git commit -m "feat: inject recent EDGAR filings into the research brief prompt"
```

---

## Task 10: Config block + live smoke test

**Files:**
- Modify: `config.yaml`
- Test: `tests/test_edgar_events.py`

- [ ] **Step 1: Add the `edgar_events` config block**

In `config.yaml`, add a top-level block (place near other source/threshold config):

```yaml
edgar_events:
  lookback_days: 90
  index_limit: 40
  # /A amendments are auto-included by edgartools; SCHEDULE 13D/13G spellings are
  # listed explicitly because the fetch filter is exact-match (see spec §3.1).
  forms: ["8-K", "SC 13D", "SC 13G", "144", "SCHEDULE 13D", "SCHEDULE 13G"]
  soft_notes:        # reserved; all OFF in v1 (pure enrichment)
    stale_after_8k: false
    activist_13d_note: false
```

- [ ] **Step 2: Add the gated live smoke test**

Append to `tests/test_edgar_events.py`:

```python
@pytest.mark.live
def test_live_edgar_events_returns_event_forms():
    """Re-pins the §3.1 form-string contract against real SEC data. Run with
    `uv run pytest -k live_edgar_events -m live` and SEC_IDENTITY set."""
    if not os.environ.get("SEC_IDENTITY"):
        pytest.skip("SEC_IDENTITY not set")
    from shortlist.data.sources import EdgarSource
    src = EdgarSource(config={"edgar_events": {"lookback_days": 3650, "index_limit": 50}})
    records = src._fetch_filings_index("AAPL")
    forms = {r["form"] for r in records}
    assert any(f.startswith("8-K") for f in forms)
    assert any("13" in f for f in forms)   # a 13D or 13G should appear over 10y
```

- [ ] **Step 3: Confirm the live test is deselected by default**

Run: `uv run pytest tests/test_edgar_events.py -q`
Expected: the non-live tests pass; `test_live_edgar_events_returns_event_forms` is deselected (the repo's `addopts = "-m 'not live'"` excludes it — no SEC call in a normal run).

- [ ] **Step 4: (Optional, manual) run the live test once**

Run: `SEC_IDENTITY="you@example.com" uv run pytest tests/test_edgar_events.py -k live_edgar_events -m live -v`
Expected: PASS — confirms the form-string filter returns 8-K and a 13D/13G for AAPL. **If it fails**, the edgartools form-string contract changed; consult spec §3.1/§3.2 (raw `submissions.json` fallback).

- [ ] **Step 5: Commit**

```bash
git add config.yaml tests/test_edgar_events.py
git commit -m "feat: edgar_events config block + gated live form-string smoke test"
```

---

## Task 11: Reconcile the documentation

**Files:**
- Modify: `docs/DATA_SOURCES.md`

- [ ] **Step 1: Update the inventory and the A1 entry**

In `docs/DATA_SOURCES.md`:
- In the §1 "What we pull today" table, change the SEC EDGAR row to note it now also supplies **filing-stream events (8-K / 13D / 13G / 144)** in the harness.
- In §3 A1, mark the **events half done** (the financials half was already done): add a `✅ DONE (harness)` note that `EdgarSource` now emits an `events` section with `recent_8k`/`activist_13d`/`passive_13g`/`planned_insider_sale_144` flags + a `recent` list, surfaced in `--json`, the table, and the research layer; link to `docs/superpowers/specs/2026-06-01-edgar-events-design.md`.
- In §2 gap #6 (news/event awareness), note the 8-K/13D detection is now shipped via A1 events (GDELT/news-tone remains open).
- In §4 sequencing, mark step 2's events portion done; note short interest (C1) is the next edge addition.

- [ ] **Step 2: Verify the doc reads consistently**

Run: `grep -n "events" docs/DATA_SOURCES.md`
Expected: the new notes appear in §1, §2, §3 A1, and §4.

- [ ] **Step 3: Commit**

```bash
git add docs/DATA_SOURCES.md
git commit -m "docs: mark DATA_SOURCES A1 events half shipped; reconcile inventory"
```

---

## Final verification

- [ ] **Run the whole suite**

Run: `uv run pytest -q`
Expected: all pass, the one `live` test reported as skipped.

- [ ] **Offline smoke**

Run: `uv run shortlist --demo`
Expected: ranked table prints; no events shown (mock has none); no errors.

- [ ] **(Optional, manual) live harness smoke on a real filer**

Run: `SEC_IDENTITY="you@example.com" uv run shortlist --tickers AAPL --engine harness --json`
Expected: valid JSON; if AAPL has in-window filings, an `events` block appears on its card.

---

## Self-review (completed by plan author)

- **Spec coverage:** §2 model → Task 2; §3 fetch/builder → Tasks 3,5; §3.1 form strings → Tasks 3,10; §3.3 config plumbing → Task 6; §4 merge/coverage → Tasks 2,4; §5 bridge/surfacing/research → Tasks 7,8,9; §6 config → Task 10; §7 tests → woven through every task; §7.6 live marker → Task 1; §9 file map → all tasks; docs → Task 11. The spec's "carry flags onto ScoreCard" is intentionally **dropped** — flags ride on `card.metrics` (already attached by `score()`), so no `scoring.py` change is needed (strictly fewer edits; noted in Task 8).
- **Placeholder scan:** none — every code step shows full code; the one stub-fixture note in Task 5 and the `FilingText` field caveat in Task 9 are explicit instructions, not deferrals.
- **Type consistency:** `Events`/`FilingEvent` field names are identical across Tasks 2–8; `_event_chips`, `build_events_section`, `classify_event_form`, `_fetch_filings_index`, `_raw_filings`, `_build_events_from_records` names are used consistently; `StockMetrics` event fields (`recent_8k`/`activist_13d`/`passive_13g`/`planned_insider_sale_144`/`filing_events`) match between Tasks 7, 8, 9.

## Plan-review reconciliation (end-to-end pass)

An end-to-end review against the live tree (now at the short-interest + fmp-429 commits) corrected:
- **Tree moved:** the aux-section infra (`_AUX_DEFAULTS`, `from_dict` aux loop, generic `merge_snapshots` aux loop) already exists → Task 2 **extends** `_AUX_DEFAULTS`; Task 4 is **tests-only** (no merge code).
- **Existing-test breakage:** `run_harness(config=)` breaks `tests/test_screen_engine.py::fake_collect` → Task 6 Step 5 updates its signature.
- **Test quality:** Task 5's happy-path test was time-fragile with dead code → rewritten deterministically (today-relative dates) with explicit `_fetch_filings_index` None/single/collection normalization coverage; the stub overrides `_raw_filings` so the real normalizer runs.
- **Scope note:** `data/cli.py` (`shortlist-harness`) stays on module-default event settings by design (equal to config defaults); documented in Task 6, not expanded.
- **Verified clean by the review:** edgartools attribute/heuristic correctness (`.form`/`.filing_date`/`.accession_no`/`.url`; `EntityFilings` iterable without `.form`), rich inline-markup rendering, `FilingText`/`ScoreCard`/`StockMetrics` fixtures, zero-score-impact, config has no schema validation, baseline suite green (290 passed / 3 skipped).

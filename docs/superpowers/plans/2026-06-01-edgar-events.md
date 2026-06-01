# EDGAR Events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-ticker `events` section to the harness `TickerSnapshot` — recent 8-K / SC 13D / SC 13G / Form 144 from the SEC filing index — surfaced as pure-enrichment flags in `--json`, the screener table, and the research brief, with zero impact on any existing score or gate.

**Architecture:** A new failure-isolated section on `EdgarSource` (mirrors the existing financials block) feeds a new `Events` dataclass on `TickerSnapshot`. A module-level pure builder does all classification/filtering (unit-testable without network). The event flags ride to the scorer through the existing `bridge.snapshot_to_metrics` → `StockMetrics` → `ScoreCard.metrics` path; nothing in `scoring.py` changes. Reached from the screener via `--engine harness`, exactly like Yahoo.

**Tech Stack:** Python 3.12, `edgartools` (optional dep, already used by `EdgarSource`), `httpx`/async harness, `pytest`, `uv`.

**Spec:** `docs/superpowers/specs/2026-06-01-edgar-events-design.md` (read it first; this plan implements it including the §11 adversarial reconciliations).

---

## File structure

| File | Responsibility |
|---|---|
| `conftest.py` *(new, repo root)* | Register + default-skip the `live` pytest marker |
| `src/shortlist/data/models.py` | `FilingEvent` + `Events` dataclasses; `TickerSnapshot.events`; `_AUX_DEFAULTS`; `from_dict` events rebuild; pure `build_events_section`/`classify_event_form` could live here OR in sources.py — **placed in `sources.py`** to keep model file free of edgartools-shaped logic; `merge_snapshots` aux line |
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

## Task 1: Register the `live` pytest marker (prerequisite)

Adversarial finding #6: the repo has no `conftest.py`/pytest config, so a `@pytest.mark.live` test would run in CI and hit SEC. This task makes `live` tests skip unless `--run-live` is passed.

**Files:**
- Create: `conftest.py` (repo root)
- Test: `tests/test_edgar_events.py` (new — start the file here)

- [ ] **Step 1: Write `conftest.py`**

```python
# conftest.py — repo-root pytest config: gate network-hitting tests behind --run-live.
import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-live", action="store_true", default=False,
        help="run @pytest.mark.live tests that hit external networks (SEC, etc.)",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "live: test hits an external network; skipped unless --run-live is passed")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-live"):
        return
    skip_live = pytest.mark.skip(reason="needs --run-live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
```

- [ ] **Step 2: Write a sentinel test proving the gate works**

Create `tests/test_edgar_events.py`:

```python
import pytest


@pytest.mark.live
def test_live_marker_is_skipped_by_default():
    # If the conftest gate works, this never runs in a normal `pytest` invocation.
    raise AssertionError("live test ran without --run-live")
```

- [ ] **Step 3: Run the suite and confirm the live test is skipped, not failed**

Run: `uv run pytest tests/test_edgar_events.py -v`
Expected: `test_live_marker_is_skipped_by_default SKIPPED (needs --run-live)`, 0 failures, no `PytestUnknownMarkWarning`.

- [ ] **Step 4: Commit**

```bash
git add conftest.py tests/test_edgar_events.py
git commit -m "test: register live pytest marker, default-skip network tests"
```

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

Add the field to `TickerSnapshot` (after `price`):

```python
    price: Optional[Price] = None
    events: Optional[Events] = None    # auxiliary: NOT a KEY_OBJECT (see _AUX_DEFAULTS)
```

- [ ] **Step 4: Wire `_AUX_DEFAULTS` and `from_dict`**

After the existing `_DEFAULTS = {...}` block add:

```python
# Auxiliary (non-coverage) top-level sections: round-tripped by from_dict but
# deliberately excluded from KEY_OBJECTS so they never move coverage()/missing().
_AUX_DEFAULTS = {"events": Events}
```

In `TickerSnapshot.from_dict`, after the `for name, klass in _DEFAULTS.items(): ...` loop and the existing `insider.recent` rebuild block, add:

```python
        for name, klass in _AUX_DEFAULTS.items():
            snap.__dict__[name] = _build(klass, d.get(name))
        ev = d.get("events")
        if snap.events is not None and ev and ev.get("recent"):
            snap.events.recent = [_build(FilingEvent, e) for e in ev["recent"]]
```

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

## Task 4: Merge `events` through `merge_snapshots`

**Files:**
- Modify: `src/shortlist/data/models.py` (`merge_snapshots`)
- Test: `tests/test_edgar_events.py`

- [ ] **Step 1: Write the failing merge test**

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

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_edgar_events.py -k merge -v`
Expected: FAIL — `merged.events is None` (the merge doesn't handle events yet).

- [ ] **Step 3: Add the aux-section merge line**

In `src/shortlist/data/models.py`, inside `merge_snapshots`, after the `for name in KEY_OBJECTS:` loop and before the `for r in ordered:` raw/errors loop, add:

```python
    # Auxiliary (non-coverage) sections: pick-first from the highest-priority
    # source that has data. Only EDGAR supplies `events` today. _build_events'
    # None-discipline guarantees a present Events is never all-falsy, so
    # _pick_first/_has_data can't select an empty section.
    ev_instances = [(r.source, getattr(r.partial, "events", None)) for r in ordered if r.partial]
    merged_ev, ev_contrib = _pick_first(ev_instances)
    if merged_ev is not None:
        snap.events = merged_ev
        snap.provenance["events"] = ev_contrib
```

- [ ] **Step 4: Run to verify the tests pass**

Run: `uv run pytest tests/test_edgar_events.py -k merge -v`
Expected: PASS (both tests).

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/shortlist/data/models.py tests/test_edgar_events.py
git commit -m "feat: merge EDGAR events as an auxiliary (non-coverage) section"
```

---

## Task 5: `EdgarSource` — fetch events as a failure-isolated section

**Files:**
- Modify: `src/shortlist/data/sources.py` (`EdgarSource`)
- Test: `tests/test_edgar_events.py`

- [ ] **Step 1: Write the failing isolation + normalization tests**

Append to `tests/test_edgar_events.py`:

```python
from shortlist.data.models import Insider


class _StubEdgar(__import__("shortlist.data.sources", fromlist=["EdgarSource"]).EdgarSource):
    """EdgarSource with network seams stubbed; bypasses __init__/identity."""
    def __init__(self, index_result, insider_snap=None):
        self._index_result = index_result
        self._insider_snap = insider_snap
        self._event_forms = ["8-K", "SC 13D"]
        self._event_lookback_days = 90
        self._index_limit = 40

    def _fetch_insider(self, ticker):
        from shortlist.data.models import SourceResult, TickerSnapshot
        res = SourceResult(source="edgar")
        res.partial = self._insider_snap or TickerSnapshot(ticker=ticker)
        return res

    def _fetch_financials_object(self, ticker):
        raise RuntimeError("financials skipped in this test")

    def _raw_filings(self, ticker):
        # stand-in for the edgartools call inside _fetch_filings_index
        return self._index_result


def test_events_failure_does_not_drop_insider():
    from shortlist.data.models import TickerSnapshot
    snap = TickerSnapshot(ticker="AAPL")
    snap.insider = Insider(net_value_6m=1.0, buy_count=1, sell_count=0)

    class _Boom(_StubEdgar):
        def _fetch_filings_index(self, ticker):
            raise RuntimeError("SEC down")

    src = _Boom(index_result=None, insider_snap=snap)
    res = src._fetch_sync("AAPL")
    assert res.partial.insider.net_value_6m == 1.0          # insider survived
    assert res.partial.events is None
    assert any("edgar-events:" in e for e in res.errors)


def test_events_populate_from_index():
    today_recs = [{"form": "8-K", "filed": "2026-05-30", "accession": "x", "url": "u"}]

    class _OK(_StubEdgar):
        def _fetch_filings_index(self, ticker):
            return build_events_section  # placeholder; replaced below
    # Use the real _fetch_filings_index by feeding _raw_filings + real builder:
    src = _StubEdgar(index_result=today_recs)
    ev = src._build_events_from_records(today_recs)
    assert ev is not None and ev.recent_8k is True
```

> Note: the stub overrides `_fetch_filings_index` for the failure case and uses a small `_raw_filings` seam + `_build_events_from_records` helper for the happy path, so no network is touched. Implement those seams in Step 3.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_edgar_events.py -k "events_failure or populate_from_index" -v`
Expected: FAIL — `_build_events_from_records` / events handling not implemented.

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

Run: `uv run pytest tests/test_edgar_events.py -k "events_failure or populate_from_index" -v`
Expected: PASS.

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

- [ ] **Step 5: Run to verify the tests pass**

Run: `uv run pytest tests/test_edgar_events.py -k build_sources -v`
Expected: PASS (both).

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass (existing `collect`/`build_sources` callers use defaults).

- [ ] **Step 7: Commit**

```bash
git add src/shortlist/data/sources.py src/shortlist/data/collector.py src/shortlist/screen.py tests/test_edgar_events.py
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

## Task 8: Surface events in `--json` and the screener `Flags` column

**Files:**
- Modify: `src/shortlist/screen.py` (`_card_dict`, `_print_table`, `_print_plain`)
- Test: `tests/test_edgar_events.py`

- [ ] **Step 1: Write the failing surfacing tests**

Append to `tests/test_edgar_events.py`:

```python
from shortlist.models import StockMetrics
from shortlist.models import ScoreCard
from shortlist.screen import _card_dict, _event_chips


def _card_with_events():
    m = StockMetrics(ticker="AAPL")
    m.activist_13d = True
    m.recent_8k = True
    m.filing_events = [{"form": "SC 13D", "filed": "2026-05-26",
                        "accession": "x", "url": "u"}]
    return ScoreCard(ticker="AAPL", composite=50.0, quality=None, moat=None,
                     growth=None, momentum=None, value=None, opportunity=None,
                     insider=None, metrics=m)


def test_card_dict_emits_events_block_only_when_present():
    with_ev = _card_dict(_card_with_events())
    assert with_ev["events"]["activist_13d"] is True
    assert with_ev["events"]["recent"][0]["form"] == "SC 13D"

    plain = ScoreCard(ticker="AAPL", composite=50.0, quality=None, moat=None,
                      growth=None, momentum=None, value=None, opportunity=None,
                      insider=None, metrics=StockMetrics(ticker="AAPL"))
    assert "events" not in _card_dict(plain)


def test_event_chips_builds_neutral_labels():
    assert _event_chips(_card_with_events().metrics) == ["13D", "8K"]
    assert _event_chips(StockMetrics(ticker="AAPL")) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_edgar_events.py -k "card_dict_emits or event_chips" -v`
Expected: FAIL — `cannot import name '_event_chips'`.

- [ ] **Step 3: Implement the chips helper and JSON block**

In `src/shortlist/screen.py`, add a module-level helper (near `_card_dict`):

```python
def _event_chips(m) -> list[str]:
    """Neutral filing-event chips for the Flags column (distinct from red gates)."""
    if m is None:
        return []
    chips = []
    if getattr(m, "activist_13d", None):
        chips.append("13D")
    if getattr(m, "passive_13g", None):
        chips.append("13G")
    if getattr(m, "recent_8k", None):
        chips.append("8K")
    if getattr(m, "planned_insider_sale_144", None):
        chips.append("144")
    return chips
```

In `_card_dict`, before `return d`, add the events block (emitted only when there are events):

```python
    if c.metrics is not None and (c.metrics.filing_events or _event_chips(c.metrics)):
        d["events"] = {
            "recent_8k": bool(c.metrics.recent_8k),
            "activist_13d": bool(c.metrics.activist_13d),
            "passive_13g": bool(c.metrics.passive_13g),
            "planned_insider_sale_144": bool(c.metrics.planned_insider_sale_144),
            "recent": c.metrics.filing_events or [],
        }
```

- [ ] **Step 4: Wire the chips into the table and plain output**

In `_print_table`, replace the `Flags` cell construction so gate chips stay red and event chips are neutral (adversarial #5 — use inline rich markup, drop the row-wide red style):

```python
    for i, c in enumerate(cards, 1):
        up = c.metrics.upside_to_target() if c.metrics else None
        gate_chips = [f"[dim red]{g}[/dim red]" for g in c.gates]
        flag_cell = ",".join(gate_chips + _event_chips(c.metrics)) or "-"
        table.add_row(
            str(i), c.ticker, f"{c.composite:.1f}",
            _f(c.quality), _f(c.moat), _f(c.growth), _f(c.momentum), _f(c.value), _f(c.insider),
            f"{up*100:.0f}%" if up is not None else "-",
            flag_cell,
        )
```

> Removing the row-level `style=` means gated rows are no longer fully dim-red; the gate chips carry the red instead. If you prefer to keep whole-row emphasis for gated names, keep `style="dim red" if c.gates else None` and accept that event chips inherit it on gated rows — pick one and note it in the commit. The plan's default is per-cell markup.

In `_print_plain`, append event chips to the flags field:

```python
    for i, c in enumerate(cards, 1):
        flags = ",".join(c.gates + _event_chips(c.metrics)) or "-"
        print(f"{i:>2} {c.ticker:<6} {c.composite:>5} {_f(c.quality):>5} "
              f"{_f(c.moat):>5} {_f(c.growth):>5} {_f(c.momentum):>5} {_f(c.value):>5} "
              f"{_f(c.insider):>5}  {flags}")
```

- [ ] **Step 5: Run to verify the tests pass**

Run: `uv run pytest tests/test_edgar_events.py -k "card_dict_emits or event_chips" -v`
Expected: PASS.

- [ ] **Step 6: Smoke-test the table renders (no events, offline)**

Run: `uv run shortlist --demo`
Expected: table prints without error; `Flags` column shows gate chips as before (demo/mock has no events).

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/shortlist/screen.py tests/test_edgar_events.py
git commit -m "feat: surface EDGAR events in --json and the Flags column"
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
    `uv run pytest -k live_edgar_events --run-live` and SEC_IDENTITY set."""
    if not os.environ.get("SEC_IDENTITY"):
        pytest.skip("SEC_IDENTITY not set")
    from shortlist.data.sources import EdgarSource
    src = EdgarSource(config={"edgar_events": {"lookback_days": 3650, "index_limit": 50}})
    records = src._fetch_filings_index("AAPL")
    forms = {r["form"] for r in records}
    assert any(f.startswith("8-K") for f in forms)
    assert any("13" in f for f in forms)   # a 13D or 13G should appear over 10y
```

- [ ] **Step 3: Confirm the live test skips by default**

Run: `uv run pytest tests/test_edgar_events.py -k live_edgar_events -v`
Expected: `SKIPPED (needs --run-live)`.

- [ ] **Step 4: (Optional, manual) run the live test once**

Run: `SEC_IDENTITY="you@example.com" uv run pytest tests/test_edgar_events.py -k live_edgar_events --run-live -v`
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
- **Type consistency:** `Events`/`FilingEvent` field names are identical across Tasks 2–8; `_event_chips`, `build_events_section`, `classify_event_form`, `_fetch_filings_index`, `_build_events_from_records` names are used consistently; `StockMetrics` event fields (`recent_8k`/`activist_13d`/`passive_13g`/`planned_insider_sale_144`/`filing_events`) match between Tasks 7, 8, 9.

# Short Interest (C1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a keyless FINRA `ConsolidatedShortInterest` feed to the harness and a non-disqualifying `crowded_short` soft flag, surfacing short-interest facts without moving any sub-score.

**Architecture:** New harness `Source` (`FinraSource`) bulk-loads the latest short-interest cycle once per run and indexes it by symbol (the `YahooSource` fetch-once-reuse precedent). A new auxiliary `ShortInterest` snapshot section (excluded from `KEY_OBJECTS`, so coverage is unaffected) flows through `bridge.py` into new `StockMetrics` fields. A new `ScoreCard.flags` list — parallel to `gates` but never touching `passed`/`composite` — is populated by `check_flags()` and surfaced in `--json`, the screener "Flags" column, and the research brief.

**Tech Stack:** Python 3, `httpx` (async), `dataclasses`, `pytest`, `uv`. Spec: `docs/superpowers/specs/2026-06-01-short-interest-design.md`.

---

## File structure

| File | Responsibility |
|---|---|
| `src/shortlist/data/models.py` | `ShortInterest` dataclass; `TickerSnapshot.short_interest`; `_AUX_DEFAULTS`; aux round-trip in `from_dict`; aux-merge line in `merge_snapshots` |
| `src/shortlist/models.py` | new `StockMetrics` fields; `ScoreCard.flags` |
| `src/shortlist/data/bridge.py` | derive `short_pct_outstanding`/`days_to_cover`/`short_interest_rising`/`short_data_age_days`; `_age_days`; constants |
| `src/shortlist/scoring.py` | `check_flags()`; wire into `score()` |
| `src/shortlist/data/sources.py` | `FinraSource` + pure helpers; register in `_REGISTRY` |
| `src/shortlist/screen.py` | `flags` in `--json`/CSV; "Flags" column renders gates+flags |
| `src/shortlist/research/assess.py` | inject a short-interest quant-context line into the brief prompt |
| `config.yaml` | `flags.crowded_short` block; add `finra` to `harness_sources` + `scout.deep_screen_sources` |
| `docs/DATA_SOURCES.md`, `HARNESS.md`, `CLAUDE.md` | doc updates |
| `tests/test_short_interest.py` (new), `tests/test_bridge.py`, `tests/test_scoring.py`, `tests/test_screen_engine.py` | tests |

Run all tests with `uv run pytest`. Single file: `uv run pytest tests/test_short_interest.py -v`.

---

## Task 1: `ShortInterest` model + auxiliary snapshot plumbing

**Files:**
- Modify: `src/shortlist/data/models.py`
- Test: `tests/test_short_interest.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_short_interest.py`:

```python
from shortlist.data.models import (
    ShortInterest, TickerSnapshot, SourceResult, merge_snapshots,
)


def test_short_interest_defaults():
    si = ShortInterest()
    assert si.settlement_date is None and si.short_shares is None
    assert si.split_flag is False and si.revised is False


def test_short_interest_not_in_coverage_denominator():
    # A snapshot with NO short_interest and one WITH it must report identical coverage.
    base = TickerSnapshot(ticker="AAA")
    withsi = TickerSnapshot(ticker="AAA",
                            short_interest=ShortInterest(short_shares=1.0, settlement_date="2026-05-15"))
    assert withsi.coverage() == base.coverage()
    assert withsi.missing() == base.missing()


def test_short_interest_merges_and_round_trips():
    si = ShortInterest(settlement_date="2026-05-15", short_shares=100.0,
                       prev_short_shares=90.0, days_to_cover=4.2)
    r = SourceResult(source="finra", partial=TickerSnapshot(ticker="AAA", short_interest=si))
    snap = merge_snapshots("AAA", [r], priority=["finra"])
    assert snap.short_interest is not None and snap.short_interest.short_shares == 100.0
    assert snap.provenance["short_interest"] == ["finra"]
    # to_dict -> from_dict preserves the section (else persisted snapshots drop it)
    back = TickerSnapshot.from_dict(snap.to_dict())
    assert back.short_interest is not None
    assert back.short_interest.settlement_date == "2026-05-15"
    assert back.short_interest.days_to_cover == 4.2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_short_interest.py -v`
Expected: FAIL — `ImportError: cannot import name 'ShortInterest'`.

- [ ] **Step 3: Add the dataclass, the snapshot field, the aux map, and the round-trip/merge wiring**

In `src/shortlist/data/models.py`, add the dataclass after `Price` (after its `price_vs_200dma` method, before the `--- Snapshot ---` divider):

```python
@dataclass
class ShortInterest:
    """FINRA consolidated short interest for one symbol, as-of a settlement cycle.
    Raw facts only; short_pct_outstanding is DERIVED in the bridge (needs market cap)."""
    settlement_date: Optional[str] = None        # ISO; the cycle this data is AS-OF (point-in-time)
    short_shares: Optional[float] = None          # currentShortPositionQuantity
    prev_short_shares: Optional[float] = None     # previousShortPositionQuantity (prior cycle)
    avg_daily_volume: Optional[float] = None      # averageDailyVolumeQuantity
    days_to_cover: Optional[float] = None          # daysToCoverQuantity — FINRA-supplied, NOT recomputed
    split_flag: bool = False                       # stockSplitFlag — counts not comparable across a split
    revised: bool = False                          # revisionFlag — figure revised after publication
```

Add the field to `TickerSnapshot` (immediately after `price: Optional[Price] = None`):

```python
    price: Optional[Price] = None
    short_interest: Optional["ShortInterest"] = None   # auxiliary — NOT a KEY_OBJECT (sparse signal)
```

After the existing `_DEFAULTS = {...}` block, add:

```python
# Auxiliary sections live on the snapshot and are merged, but are DELIBERATELY excluded
# from KEY_OBJECTS so they never move coverage()/missing() (sparse signals, not
# assessment-ready fundamentals). from_dict round-trips them via this map.
_AUX_DEFAULTS = {"short_interest": ShortInterest}
```

In `from_dict`, after the `for name, klass in _DEFAULTS.items():` loop (and before the `ins = d.get("insider")` line), add:

```python
        for name, klass in _AUX_DEFAULTS.items():
            snap.__dict__[name] = _build(klass, d.get(name))
```

In `merge_snapshots`, after the `for name in KEY_OBJECTS:` loop ends and before the `for r in ordered:` raw/errors loop, add:

```python
    # Auxiliary (non-coverage) sections: pick-first from the highest-priority source with data.
    for name in _AUX_DEFAULTS:
        instances = [(r.source, getattr(r.partial, name, None)) for r in ordered if r.partial]
        merged, contributors = _pick_first(instances)
        if merged is not None:
            setattr(snap, name, merged)
            snap.provenance[name] = contributors
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_short_interest.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/data/models.py tests/test_short_interest.py
git commit -m "feat(short-interest): ShortInterest snapshot section + aux merge/round-trip"
```

---

## Task 2: `StockMetrics` short-interest fields + `ScoreCard.flags`

**Files:**
- Modify: `src/shortlist/models.py`
- Test: `tests/test_short_interest.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_short_interest.py`:

```python
from shortlist.models import StockMetrics, ScoreCard


def test_stockmetrics_short_fields_default_none():
    m = StockMetrics(ticker="X")
    assert m.short_pct_outstanding is None
    assert m.days_to_cover is None
    assert m.short_interest_rising is None
    assert m.short_data_age_days is None


def test_scorecard_flags_default_empty_and_do_not_affect_passed():
    c = ScoreCard(ticker="X", composite=50.0, quality=None, moat=None, growth=None,
                  momentum=None, value=None, opportunity=None, insider=None)
    assert c.flags == []
    c.flags = ["crowded_short"]
    assert c.passed is True            # flags are advisory: passed depends only on gates
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_short_interest.py -k "short_fields or flags_default" -v`
Expected: FAIL — `AttributeError: ... has no attribute 'short_pct_outstanding'` / `'flags'`.

- [ ] **Step 3: Add the fields**

In `src/shortlist/models.py`, in `StockMetrics`, add a new block after the insider fields (after `insider_sentiment`):

```python
    # Short interest (FINRA consolidated; derived in bridge.py). Soft-flag inputs only.
    short_pct_outstanding: Optional[float] = None  # short_shares / (market_cap/price); under-states float
    days_to_cover: Optional[float] = None          # FINRA-supplied; 999.99 sentinel -> None
    short_interest_rising: Optional[bool] = None   # current > prior cycle; None across a split
    short_data_age_days: Optional[int] = None      # as_of - settlement_date (staleness guard input)
```

In `ScoreCard`, add the `flags` field right after `gates`:

```python
    gates: list[str] = field(default_factory=list)  # tripped hard filters
    flags: list[str] = field(default_factory=list)  # soft advisories (e.g. crowded_short); NOT disqualifying
```

(`passed` already returns `not self.gates`; leave it unchanged so flags never affect it.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_short_interest.py -k "short_fields or flags_default" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/models.py tests/test_short_interest.py
git commit -m "feat(short-interest): StockMetrics short fields + ScoreCard.flags"
```

---

## Task 3: Bridge derivation

**Files:**
- Modify: `src/shortlist/data/bridge.py`
- Test: `tests/test_bridge.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_bridge.py`:

```python
from shortlist.data.models import ShortInterest


def _snap_with_si(**si_kwargs):
    from shortlist.data.models import Profile, Price
    return TickerSnapshot(
        ticker="AAA", as_of="2026-06-01T00:00:00+00:00",
        profile=Profile(market_cap=1.0e10), price=Price(price=100.0),
        short_interest=ShortInterest(settlement_date="2026-05-15", **si_kwargs),
    )


def test_bridge_short_pct_outstanding_and_dtc():
    # shares_out = 1e10/100 = 1e8; short 1e7 => 10% of outstanding
    m = snapshot_to_metrics(_snap_with_si(short_shares=1.0e7, prev_short_shares=9.0e6, days_to_cover=4.2))
    assert abs(m.short_pct_outstanding - 0.10) < 1e-9
    assert m.days_to_cover == 4.2
    assert m.short_interest_rising is True
    assert m.short_data_age_days == 17        # 2026-06-01 minus 2026-05-15


def test_bridge_dtc_sentinel_dropped():
    m = snapshot_to_metrics(_snap_with_si(short_shares=1.0e7, days_to_cover=999.99))
    assert m.days_to_cover is None


def test_bridge_short_pct_sanity_clamp():
    # short > 60% of outstanding => denominator suspect (ADR/dual-class) => dropped
    m = snapshot_to_metrics(_snap_with_si(short_shares=9.0e7))   # 90% of 1e8
    assert m.short_pct_outstanding is None


def test_bridge_rising_none_across_split():
    m = snapshot_to_metrics(_snap_with_si(short_shares=1.0e7, prev_short_shares=9.0e6, split_flag=True))
    assert m.short_interest_rising is None


def test_bridge_no_short_interest_leaves_fields_none():
    m = snapshot_to_metrics(_full_snapshot())   # defined earlier in this file; has no short_interest
    assert m.short_pct_outstanding is None and m.days_to_cover is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_bridge.py -k short -v`
Expected: FAIL — fields stay `None` (no derivation yet).

- [ ] **Step 3: Implement the derivation**

In `src/shortlist/data/bridge.py`, add module constants and a helper near the top (after the imports / `_close_near`):

```python
_MAX_PLAUSIBLE_SHORT_PCT = 0.60   # > this of shares-outstanding => broken denominator (ADR/dual-class)
_DTC_SENTINEL = 999.99            # FINRA's zero-volume days-to-cover cap


def _age_days(as_of: Optional[str], settlement: Optional[str]) -> Optional[int]:
    """Whole days between a snapshot's capture time and the SI settlement date.
    Pure (no clock read) and None-safe (unparseable -> None)."""
    from datetime import date, datetime
    if not as_of or not settlement:
        return None
    try:
        a = datetime.fromisoformat(as_of).date()
        s = date.fromisoformat(settlement)
    except (TypeError, ValueError):
        return None
    return (a - s).days
```

At the end of `snapshot_to_metrics`, just before `return m`, add:

```python
    si = snap.short_interest
    if si:
        dtc = si.days_to_cover
        m.days_to_cover = dtc if (dtc is not None and dtc < _DTC_SENTINEL) else None
        if si.short_shares is not None and m.market_cap and m.price:
            shares_out = m.market_cap / m.price
            pct = si.short_shares / shares_out if shares_out else None
            if pct is not None and 0.0 <= pct <= _MAX_PLAUSIBLE_SHORT_PCT:
                m.short_pct_outstanding = pct
        if (si.short_shares is not None and si.prev_short_shares is not None
                and not si.split_flag):
            m.short_interest_rising = si.short_shares > si.prev_short_shares
        m.short_data_age_days = _age_days(snap.as_of, si.settlement_date)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_bridge.py -k short -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/data/bridge.py tests/test_bridge.py
git commit -m "feat(short-interest): bridge derives short_pct_outstanding/dtc/rising/age"
```

---

## Task 4: `check_flags` + wire into `score()`

**Files:**
- Modify: `src/shortlist/scoring.py`
- Test: `tests/test_scoring.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scoring.py`:

```python
from shortlist.scoring import check_flags

FLAGS_CFG = {"crowded_short": {
    "min_short_pct_outstanding": 0.10, "min_days_to_cover": 5.0,
    "require_rising": True, "max_staleness_days": 35,
}}


def _crowded_metrics(**kw):
    base = dict(short_pct_outstanding=0.15, days_to_cover=6.0,
                short_interest_rising=True, short_data_age_days=10)
    base.update(kw)
    return StockMetrics(ticker="X", **base)


def test_check_flags_trips_on_full_conjunction():
    assert check_flags(_crowded_metrics(), FLAGS_CFG) == ["crowded_short"]


def test_check_flags_each_clause_suppresses():
    assert check_flags(_crowded_metrics(short_pct_outstanding=0.05), FLAGS_CFG) == []
    assert check_flags(_crowded_metrics(days_to_cover=3.0), FLAGS_CFG) == []
    assert check_flags(_crowded_metrics(short_interest_rising=False), FLAGS_CFG) == []
    assert check_flags(_crowded_metrics(short_data_age_days=40), FLAGS_CFG) == []   # stale


def test_check_flags_none_inputs_are_noop():
    assert check_flags(StockMetrics(ticker="X"), FLAGS_CFG) == []
    assert check_flags(_crowded_metrics(), {}) == []          # no flags config -> nothing


def test_score_carries_flags_and_passed_unaffected():
    m = _crowded_metrics(market_cap=5.0e9)
    cfg = dict(CONFIG)
    cfg["flags"] = FLAGS_CFG
    card = score(m, cfg)
    assert "crowded_short" in card.flags
    assert card.passed is True                                # advisory only
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scoring.py -k flags -v`
Expected: FAIL — `ImportError: cannot import name 'check_flags'`.

- [ ] **Step 3: Implement `check_flags` and wire it in**

In `src/shortlist/scoring.py`, add after `check_gates`:

```python
def check_flags(m: StockMetrics, f: dict) -> list[str]:
    """Soft, NON-disqualifying advisories (parallel to check_gates). Fully None-safe:
    returns [] when inputs or config are absent, so the screener engine is a no-op."""
    out: list[str] = []
    cs = f.get("crowded_short") if f else None
    if cs and m.short_pct_outstanding is not None and m.days_to_cover is not None:
        fresh = (m.short_data_age_days is None
                 or m.short_data_age_days <= cs["max_staleness_days"])
        rising_ok = (not cs.get("require_rising")) or (m.short_interest_rising is True)
        if (m.short_pct_outstanding >= cs["min_short_pct_outstanding"]
                and m.days_to_cover >= cs["min_days_to_cover"]
                and rising_ok and fresh):
            out.append("crowded_short")
    return out
```

In `score()`, change the `ScoreCard(...)` construction to pass `flags`. Locate the existing:

```python
        gates=check_gates(m, config["gates"]),
        metrics=m,
```

and replace with:

```python
        gates=check_gates(m, config["gates"]),
        flags=check_flags(m, config.get("flags") or {}),
        metrics=m,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_scoring.py -k flags -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/scoring.py tests/test_scoring.py
git commit -m "feat(short-interest): crowded_short soft flag via check_flags (AND+rising, staleness)"
```

---

## Task 5: Config — flags block + source registration

**Files:**
- Modify: `config.yaml`
- Test: `tests/test_short_interest.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_short_interest.py`:

```python
def test_config_yaml_has_flags_and_finra():
    import yaml
    from pathlib import Path
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    cs = cfg["flags"]["crowded_short"]
    assert cs["min_short_pct_outstanding"] == 0.10
    assert cs["min_days_to_cover"] == 5.0
    assert cs["require_rising"] is True
    assert cs["max_staleness_days"] == 35
    assert "finra" in cfg["harness_sources"]
    assert "finra" in cfg["scout"]["deep_screen_sources"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_short_interest.py -k config_yaml -v`
Expected: FAIL — `KeyError: 'flags'`.

- [ ] **Step 3: Edit `config.yaml`**

Add a new top-level block after the `gates:` block:

```yaml
# Soft, NON-disqualifying advisories (parallel to `gates` — never change composite/passed).
# crowded_short fires only under `--engine harness` with the `finra` source present.
flags:
  crowded_short:
    min_short_pct_outstanding: 0.10   # of shares OUTSTANDING (under-states float; a PRIOR, not fitted)
    min_days_to_cover: 5.0            # >5 elevated, >10 extreme
    require_rising: true              # the change (vs prior cycle), not the static level, is the signal
    max_staleness_days: 35            # ignore a cycle older than ~2 missed publications (stale-cache guard)
```

Change the harness source chain to include `finra` (append at the end so Yahoo still leads the price merge):

```yaml
harness_sources: [yahoo, fmp, finnhub, edgar, finra]
```

In the `scout:` block, change `deep_screen_sources`:

```yaml
  deep_screen_sources: [yahoo, fmp, finnhub, edgar, finra]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_short_interest.py -k config_yaml -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add config.yaml tests/test_short_interest.py
git commit -m "feat(short-interest): config flags.crowded_short + register finra source"
```

---

## Task 6: `FinraSource` — keyless bulk loader + symbol index

**Files:**
- Modify: `src/shortlist/data/sources.py`
- Test: `tests/test_short_interest.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_short_interest.py`:

```python
import asyncio
from shortlist.data.sources import (
    FinraSource, _finra_latest_partition, _finra_norm_symbol,
    _finra_row_to_si, _finra_index,
)


def test_finra_latest_partition_picks_max():
    payload = {"availablePartitions": [
        {"partitions": ["2026-04-30"]}, {"partitions": ["2026-05-15"]},
        {"partitions": ["2026-04-15"]}]}
    assert _finra_latest_partition(payload) == "2026-05-15"
    assert _finra_latest_partition({"availablePartitions": []}) is None


def test_finra_norm_symbol_collapses_separators():
    assert _finra_norm_symbol("brk.b") == "BRKB"
    assert _finra_norm_symbol("BRK-B") == "BRKB"


def test_finra_row_to_si_and_index():
    row = {"symbolCode": "AAPL", "settlementDate": "2026-05-15",
           "currentShortPositionQuantity": "138782718",
           "previousShortPositionQuantity": "134675274",
           "averageDailyVolumeQuantity": "50565316",
           "daysToCoverQuantity": "2.74", "stockSplitFlag": "", "revisionFlag": ""}
    si = _finra_row_to_si(row)
    assert si.short_shares == 138782718.0 and si.days_to_cover == 2.74
    assert si.split_flag is False
    idx = _finra_index([row])
    assert idx["AAPL"]["symbolCode"] == "AAPL"


def _finra_mock(monkeypatch, src, pages):
    """pages: list of row-lists returned per offset call (simulates pagination)."""
    async def fake_parts():
        return {"availablePartitions": [{"partitions": ["2026-05-15"]}]}
    calls = {"n": 0}
    async def fake_page(settlement, offset):
        i = offset // src.PAGE
        return pages[i] if i < len(pages) else []
    monkeypatch.setattr(src, "_fetch_partitions", fake_parts)
    monkeypatch.setattr(src, "_fetch_page", fake_page)


def test_finra_source_builds_short_interest(tmp_path, monkeypatch):
    src = FinraSource(cache_dir=str(tmp_path))
    full = [{"symbolCode": f"S{i}", "settlementDate": "2026-05-15",
             "currentShortPositionQuantity": str(i)} for i in range(src.PAGE)]
    tail = [{"symbolCode": "AAPL", "settlementDate": "2026-05-15",
             "currentShortPositionQuantity": "138782718", "daysToCoverQuantity": "2.74"}]
    _finra_mock(monkeypatch, src, [full, tail])     # two pages: 5000 then 1 (short page ends loop)
    res = asyncio.run(src.fetch("AAPL"))
    assert res.source == "finra"
    assert res.partial.short_interest is not None
    assert res.partial.short_interest.days_to_cover == 2.74
    asyncio.run(src.aclose())


def test_finra_absent_symbol_is_none_not_error(tmp_path, monkeypatch):
    src = FinraSource(cache_dir=str(tmp_path))
    _finra_mock(monkeypatch, src, [[{"symbolCode": "AAPL", "settlementDate": "2026-05-15"}]])
    res = asyncio.run(src.fetch("ZZZZ"))
    assert res.partial.short_interest is None
    assert res.errors == []
    asyncio.run(src.aclose())


def test_finra_load_error_is_non_fatal(tmp_path, monkeypatch):
    src = FinraSource(cache_dir=str(tmp_path))
    async def boom():
        raise RuntimeError("network down")
    monkeypatch.setattr(src, "_fetch_partitions", boom)
    res = asyncio.run(src.fetch("AAPL"))
    assert res.partial.short_interest is None
    assert res.errors and "finra" in res.errors[0]
    asyncio.run(src.aclose())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_short_interest.py -k finra -v`
Expected: FAIL — `ImportError: cannot import name 'FinraSource'`.

- [ ] **Step 3: Implement `FinraSource` + pure helpers**

In `src/shortlist/data/sources.py`, add `ShortInterest` to the model import at the top:

```python
from .models import (
    Analyst, Fundamentals, Insider, InsiderTxn, Price, Profile,
    ShortInterest, SourceResult, Statements, TickerSnapshot,
)
```

Add the pure helpers near the other module-level helpers (e.g. after `_year`):

```python
# --- FINRA short interest (pure helpers) ----------------------------------

def _finra_latest_partition(payload: Any) -> Optional[str]:
    parts = (payload or {}).get("availablePartitions") or []
    dates = [p["partitions"][0] for p in parts if p.get("partitions")]
    return max(dates) if dates else None


def _finra_norm_symbol(sym: str) -> str:
    """Collapse separators so BRK.B / BRK-B / BRKB all match one key."""
    return (sym or "").upper().replace("-", "").replace(".", "")


def _finra_num(row: dict, key: str) -> Optional[float]:
    v = row.get(key)
    try:
        return float(v) if v not in (None, "", "N/A") else None
    except (TypeError, ValueError):
        return None


def _finra_flag(row: dict, key: str) -> bool:
    return str(row.get(key, "")).strip().upper() in ("Y", "YES", "TRUE", "1")


def _finra_row_to_si(row: dict) -> ShortInterest:
    return ShortInterest(
        settlement_date=row.get("settlementDate"),
        short_shares=_finra_num(row, "currentShortPositionQuantity"),
        prev_short_shares=_finra_num(row, "previousShortPositionQuantity"),
        avg_daily_volume=_finra_num(row, "averageDailyVolumeQuantity"),
        days_to_cover=_finra_num(row, "daysToCoverQuantity"),
        split_flag=_finra_flag(row, "stockSplitFlag"),
        revised=_finra_flag(row, "revisionFlag"),
    )


def _finra_index(rows: list) -> dict:
    return {_finra_norm_symbol(r["symbolCode"]): r for r in rows if r.get("symbolCode")}
```

Add the source class (e.g. after `YahooSource`):

```python
class FinraSource(Source):
    """Keyless FINRA ConsolidatedShortInterest. Bulk-loads the latest bi-monthly
    cycle ONCE per run (the YahooSource fetch-once-reuse precedent), indexes by
    normalized symbol, and serves per-ticker lookups as O(1) dict hits. Disk-cached
    by SETTLEMENT DATE so the cache survives the ~2 weeks until the next cycle."""

    name = "finra"
    DATA = "https://api.finra.org/data/group/otcMarket/name/ConsolidatedShortInterest"
    PARTS = "https://api.finra.org/partitions/group/otcMarket/name/ConsolidatedShortInterest"
    PAGE = 5000   # FINRA record-max-limit

    def __init__(self, timeout: float = 30.0, cache_dir: str = ".cache/finra"):
        import httpx  # lazy: only needed for live runs
        self._client = httpx.AsyncClient(timeout=timeout, headers={"Accept": "application/json"})
        self._cache_dir = Path(cache_dir)
        self._index: Optional[dict] = None
        self._settlement: Optional[str] = None
        self._load_error: Optional[str] = None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _fetch_partitions(self) -> Any:
        r = await self._client.get(self.PARTS)
        r.raise_for_status()
        return r.json()

    async def _fetch_page(self, settlement: str, offset: int) -> list:
        body = {"limit": self.PAGE, "offset": offset,
                "compareFilters": [{"fieldName": "settlementDate",
                                    "fieldValue": settlement, "compareType": "EQUAL"}]}
        r = await self._client.post(self.DATA, json=body)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    def _cache_path(self, settlement: str) -> Path:
        return self._cache_dir / f"{settlement}.json"

    def _read_cache(self, settlement: str):
        cp = self._cache_path(settlement)
        try:
            if cp.exists():
                return json.loads(cp.read_text())
        except Exception:
            pass  # corrupt cache -> refetch
        return None

    def _write_cache(self, settlement: str, rows: list) -> None:
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            self._cache_path(settlement).write_text(json.dumps(rows))
        except Exception:
            pass  # cache write failure is non-fatal

    async def _load(self) -> None:
        """Discover the latest cycle and build the symbol index once."""
        if self._index is not None or self._load_error is not None:
            return
        try:
            settlement = _finra_latest_partition(await self._fetch_partitions())
            if not settlement:
                self._index = {}
                return
            rows = self._read_cache(settlement)
            if rows is None:
                rows, offset = [], 0
                while True:
                    page = await self._fetch_page(settlement, offset)
                    rows.extend(page)
                    if len(page) < self.PAGE:
                        break
                    offset += self.PAGE
                self._write_cache(settlement, rows)
            self._index = _finra_index(rows)
            self._settlement = settlement
        except Exception as e:
            self._load_error = redact_secrets(str(e))
            self._index = {}

    async def fetch(self, ticker: str) -> SourceResult:
        res = SourceResult(source=self.name)
        await self._load()
        snap = TickerSnapshot(ticker=ticker)
        if self._load_error:
            res.errors.append(f"finra: {self._load_error}")
            res.partial = snap
            return res
        row = (self._index or {}).get(_finra_norm_symbol(ticker))
        if row is not None:
            snap.short_interest = _finra_row_to_si(row)
        # raw carries the cycle + whether THIS symbol matched (visible, not silent)
        res.raw = {"settlement_date": self._settlement, "matched": row is not None}
        res.partial = snap
        return res
```

Register it in `_REGISTRY`:

```python
_REGISTRY = {
    "yahoo": YahooSource,
    "fmp": FMPSource, "finnhub": FinnhubSource, "edgar": EdgarSource, "mock": MockSource,
    "finra": FinraSource,
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_short_interest.py -k finra -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/data/sources.py tests/test_short_interest.py
git commit -m "feat(short-interest): FinraSource keyless bulk loader + symbol index"
```

---

## Task 7: Surface `flags` in the screener output

**Files:**
- Modify: `src/shortlist/screen.py`
- Test: `tests/test_screen_engine.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_screen_engine.py`:

```python
from shortlist.models import ScoreCard
from shortlist.screen import _card_dict, _flags_cell


def _card(**kw):
    base = dict(ticker="X", composite=50.0, quality=None, moat=None, growth=None,
                momentum=None, value=None, opportunity=None, insider=None)
    base.update(kw)
    return ScoreCard(**base)


def test_flags_cell_merges_gates_and_flags():
    assert _flags_cell(_card()) == "-"
    assert _flags_cell(_card(flags=["crowded_short"])) == "crowded_short"
    assert _flags_cell(_card(gates=["over_leveraged"], flags=["crowded_short"])) \
        == "over_leveraged,crowded_short"


def test_card_dict_includes_flags():
    d = _card_dict(_card(flags=["crowded_short"]))
    assert d["flags"] == ["crowded_short"]
    assert d["gates"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_screen_engine.py -k "flags_cell or card_dict_includes" -v`
Expected: FAIL — `ImportError: cannot import name '_flags_cell'`.

- [ ] **Step 3: Add the helper and wire the three render sites**

In `src/shortlist/screen.py`, add the helper above `_print_table`:

```python
def _flags_cell(c: ScoreCard) -> str:
    """Combined chips for the 'Flags' column: hard gates first, then soft flags."""
    return ",".join(list(c.gates) + list(c.flags)) or "-"
```

In `_print_table`, replace the last row argument `",".join(c.gates) or "-"` with `_flags_cell(c)`:

```python
            f"{up*100:.0f}%" if up is not None else "-",
            _flags_cell(c), style=style,
```

In `_print_plain`, replace `{','.join(c.gates) or '-'}` with `{_flags_cell(c)}`:

```python
              f"{_f(c.insider):>5}  {_flags_cell(c)}")
```

In `_card_dict`, add `flags` next to `gates` in the dict literal:

```python
        "gates": c.gates,
        "flags": c.flags,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_screen_engine.py -k "flags_cell or card_dict_includes" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/screen.py tests/test_screen_engine.py
git commit -m "feat(short-interest): surface flags in table, plain, and --json output"
```

---

## Task 8: Inject short-interest context into the research brief

**Files:**
- Modify: `src/shortlist/research/assess.py`
- Test: `tests/test_screen_research.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_screen_research.py`:

```python
def test_research_prompt_includes_short_interest_context():
    from shortlist.research.assess import _build_user_prompt
    from shortlist.research.filings import FilingText
    from shortlist.models import StockMetrics, ScoreCard

    filing = FilingText(ticker="AAA", accession="x", filing_date="2026-01-01",
                        business="b", mda="m", risk_factors="r")
    card = ScoreCard(ticker="AAA", composite=50.0, quality=None, moat=None, growth=None,
                     momentum=None, value=None, opportunity=None, insider=None,
                     metrics=StockMetrics(ticker="AAA", short_pct_outstanding=0.12,
                                          days_to_cover=6.3, short_interest_rising=True))
    prompt = _build_user_prompt(filing, {}, card)
    assert "QUANT CONTEXT" in prompt
    assert "12.0% of shares" in prompt and "6.3 days to cover" in prompt

    # No metrics -> no quant block, no crash.
    assert "QUANT CONTEXT" not in _build_user_prompt(filing, {}, None)
```

(`FilingText` is defined in `src/shortlist/research/models.py`: required `ticker`, `accession`, `filing_date`; optional `business`/`mda`/`risk_factors`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_screen_research.py -k short_interest_context -v`
Expected: FAIL — `_build_user_prompt() takes 2 positional arguments but 3 were given`.

- [ ] **Step 3: Add the optional `card` param + quant block**

In `src/shortlist/research/assess.py`, change `_build_user_prompt` to accept `card` and append a short factual block:

```python
def _build_user_prompt(filing: FilingText, config: dict, card=None) -> str:
    rcfg = config.get("research", {})
    quant = _short_interest_note(card)
    return (
        f"Ticker: {filing.ticker}\nAccession: {filing.accession}\n\n"
        f"{quant}"
        f"=== ITEM 1 — BUSINESS ===\n{filing.business}\n\n"
        f"=== ITEM 7 — MD&A ===\n{filing.mda}\n\n"
        f"=== ITEM 1A — RISK FACTORS ===\n{filing.risk_factors}\n\n"
        f"Return at most {rcfg.get('max_risks', 8)} risks and "
        f"{rcfg.get('max_red_flags', 8)} red_flags, most material first."
    )


def _short_interest_note(card) -> str:
    m = getattr(card, "metrics", None) if card else None
    if not m or m.short_pct_outstanding is None or m.days_to_cover is None:
        return ""
    trend = "rising" if m.short_interest_rising else "not rising"
    return ("=== QUANT CONTEXT (facts; not from the filing) ===\n"
            f"Short interest: {m.short_pct_outstanding * 100:.1f}% of shares, "
            f"{m.days_to_cover:.1f} days to cover, {trend}. "
            "Weigh whether the filing's risks corroborate or refute the bear case.\n\n")
```

Update the one call site in `assess()` to pass `card`:

```python
    user_prompt = _build_user_prompt(filing, config, card)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_screen_research.py -k short_interest_context -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/research/assess.py tests/test_screen_research.py
git commit -m "feat(short-interest): pass short-interest facts into the research brief prompt"
```

---

## Task 9: Full suite + live smoke test + docs

**Files:**
- Modify: `tests/test_short_interest.py` (live smoke), `docs/DATA_SOURCES.md`, `HARNESS.md`, `/home/chris/shortlist/CLAUDE.md`

- [ ] **Step 1: Run the FULL suite (regression gate)**

Run: `uv run pytest`
Expected: PASS, no regressions. In particular confirm `tests/test_harness.py` and any full-harness test still pass now that `finra` is in `harness_sources` (FinraSource constructs offline — only `fetch` touches the network; full-run tests use mocks or skip on missing keys). If a test does a live full-harness run, mock `FinraSource._fetch_partitions`/`_fetch_page` the same way Task 6 does, or drop `finra` from that test's source list.

- [ ] **Step 2: Add a gated live smoke test**

Append to `tests/test_short_interest.py`:

```python
import pytest


@pytest.mark.live
def test_finra_live_smoke():
    """Real FINRA call. Skipped by default; run with: uv run pytest -m live."""
    src = FinraSource()
    try:
        res = asyncio.run(src.fetch("AAPL"))
    finally:
        asyncio.run(src.aclose())
    si = res.partial.short_interest
    assert si is not None, "AAPL absent from consolidated cycle — contract changed"
    assert si.short_shares and si.short_shares > 0
    assert si.settlement_date and si.settlement_date >= "2026-01-01"
```

If `live` is not yet a registered marker, add to `pyproject.toml` under `[tool.pytest.ini_options]`:

```toml
markers = ["live: hits real external APIs; skipped unless -m live"]
```

Run (optional, network): `uv run pytest tests/test_short_interest.py -m live -v`
Expected (with network): PASS. Default `uv run pytest` skips it.

- [ ] **Step 3: Update `docs/DATA_SOURCES.md` (C1)**

Replace the C1 section (`#### C1. FINRA + Nasdaq short interest`, ~line 188) "Wire-in" bullet and correct the endpoint. The section must now state:
- **Shipped (harness):** `FinraSource` → `ShortInterest` snapshot section → bridge → `crowded_short` soft flag.
- **Endpoint correction:** the live, NMS-covering dataset is **`ConsolidatedShortInterest`** (POST `https://api.finra.org/data/group/otcMarket/name/ConsolidatedShortInterest`, keyless), discovered via the `/partitions/` endpoint. The older `EquityShortInterest` is **frozen (last cycle 2022-09-15) and OTC-only** — do not use it.
- **Denominator caveat:** `%` is of shares-outstanding (`market_cap/price`), labeled `short_pct_outstanding`, conservative vs. float.

- [ ] **Step 4: Update `HARNESS.md`**

Add a short subsection documenting: the new `finra` source (keyless, **one bulk fetch per run**, cached by settlement date — adds no per-ticker request load), the `ShortInterest` section, and the **soft-flag concept** (`flags` vs hard `gates`: advisory, never changes `composite`/`passed`; `crowded_short` only fires under `--engine harness` with `finra` present).

- [ ] **Step 5: Update `/home/chris/shortlist/CLAUDE.md`**

Add notes mirroring the existing "gotchas" style:
- A **"Short interest (harness)"** subsection: dataset is **`ConsolidatedShortInterest`** (not `EquityShortInterest` — that one is frozen + OTC-only); symbol field is **`symbolCode`**; `settlementDate` is a **partition key** (discover the latest via `/partitions/`, can't sort it in the data query); `record-max-limit` 5000 ⇒ paginate; `days_to_cover` is FINRA-supplied (`999.99` = zero-volume sentinel).
- A line in the scoring section: **soft `flags`** are advisory and never affect `passed`/`composite` (distinct from hard `gates`); `crowded_short` = `short_pct_outstanding ≥ t ∧ days_to_cover ≥ t ∧ rising ∧ fresh`.

- [ ] **Step 6: Commit**

```bash
git add tests/test_short_interest.py pyproject.toml docs/DATA_SOURCES.md HARNESS.md CLAUDE.md
git commit -m "docs+test(short-interest): live smoke + DATA_SOURCES/HARNESS/CLAUDE updates"
```

---

## Self-review notes (for the implementer)

- **Coverage invariance is load-bearing:** Task 1's `test_short_interest_not_in_coverage_denominator` must pass — if it fails, `short_interest` leaked into `KEY_OBJECTS`. It belongs only in `_AUX_DEFAULTS`.
- **Zero-impact guarantee:** `crowded_short` must never change `passed`/`composite` (Task 2 + Task 4 tests). If a scoring test's composite shifts, `flags` was wired into a sub-score by mistake.
- **None-safety everywhere:** every new `StockMetrics` field defaults `None`; the default screener engine (no `finra`) never populates them, so `check_flags` is a no-op there (Task 4 `test_check_flags_none_inputs_are_noop`).
- **External contract risk:** the only unverified-at-build assumptions are FINRA's field names / dataset name (pinned by Task 9's live smoke). All other logic is unit-tested offline.
```

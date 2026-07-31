# Statements Year-Joined Merge — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the harness discarding every EDGAR-only `Statements` field whenever FMP wins the merge, by replacing the whole-source `_pick_first` with a year-joined, priority-ordered backfill.

**Architecture:** Three pure helpers plus one bespoke merger in `src/shortlist/data/models.py`, on the existing `_merge_insider` precedent. The highest-priority source with data stays the spine (so no existing field or growth leg changes); fields it left empty are re-indexed onto the spine's `fiscal_years` from lower-priority sources. Six pre-computed latest-FY scalars copy only when the donor's newest fiscal year matches the spine's.

**Tech Stack:** Python 3.11+, stdlib only (`dataclasses`), pytest, uv. No new dependencies.

**Design spec:** `docs/STATEMENTS_MERGE.md` (committed `d5366e6`). Read it before starting.

## Global Constraints

- **No new dependency.** stdlib `dataclasses` only. The repo's only runtime additions are extras.
- **No new dataclass field, no new fetch, no config block, no scoring leg.** This is pure recovery of data the harness already fetches and discards.
- **`scoring.score()` is not edited.** The only live scoring-surface change is the `dilution` advisory flag becoming *able* to fire — that comes from data, not from scorer edits.
- **Join by fiscal year, never by list position.** Every `Statements` consumer aligns by index (`piotroski_f`, `_financial_series`, `cagr`, `[0]`-as-latest), so a positional backfill silently pairs mismatched years.
- **Abstain rather than guess.** A field left empty is correct; a wrong-year value is not.
- **Copy, never alias.** `_pick_first` returns the winner by identity today; the new merger must `dataclasses.replace()` before mutating.
- **Reuse `_is_present`** for emptiness (`None`, `[]`, `""`). Do not introduce a second convention.
- CI, exact commands, lint first: `uv run ruff check src tests` then `uv run pytest -q`.
- Commit after every task.

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/shortlist/data/models.py` | `_newest_year`, `_usable_years`, `_reindex_by_year`, `_STATEMENTS_LATEST_FY_SCALARS`, `_merge_statements`; route `statements` in `merge_snapshots` | Modify |
| `tests/test_statements_merge.py` | All merge behaviour (sibling to `tests/test_insider_merge.py`) | Create |
| `src/shortlist/data/sources/edgar.py:216` | Stale comment that this fix finally makes true | Modify (comment only) |
| `CLAUDE.md` | "Insider merge (harness)" section gains a statements sibling | Modify |
| `TODO.md` | Close 2026-07-20 item 1 | Modify |

---

### Task 1: Pure year-join helpers

**Files:**
- Modify: `src/shortlist/data/models.py` (add helpers next to `_merge_flat`/`_pick_first`, ~line 440-510)
- Test: `tests/test_statements_merge.py` (create)

**Interfaces:**
- Consumes: `_is_present` (already in `models.py:14`)
- Produces:
  - `_newest_year(years: list[Optional[int]]) -> Optional[int]`
  - `_usable_years(st: Statements) -> Optional[list[Optional[int]]]`
  - `_reindex_by_year(donor_years, donor_values, spine_years) -> list`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_statements_merge.py`:

```python
from __future__ import annotations

from shortlist.data.models import (
    SourceResult, Statements, TickerSnapshot, merge_snapshots,
)
from shortlist.data.models import (
    _newest_year, _reindex_by_year, _usable_years,
)


# --- pure helpers ---------------------------------------------------------

def test_newest_year_ignores_none_holes():
    assert _newest_year([2025, 2024, None, 2022]) == 2025
    assert _newest_year([None, None]) is None
    assert _newest_year([]) is None


def test_usable_years_rejects_empty_and_duplicates():
    assert _usable_years(Statements(fiscal_years=[2025, 2024])) == [2025, 2024]
    assert _usable_years(Statements()) is None                       # no key
    assert _usable_years(Statements(fiscal_years=[2025, 2025])) is None  # ambiguous


def test_reindex_places_values_on_matching_years_and_pads_with_none():
    # Donor covers 3 of the spine's 5 years; the two oldest have no data.
    out = _reindex_by_year(
        donor_years=[2025, 2024, 2023],
        donor_values=[15.1, 15.4, 15.8],
        spine_years=[2025, 2024, 2023, 2022, 2021],
    )
    assert out == [15.1, 15.4, 15.8, None, None]


def test_reindex_aligns_by_year_not_position():
    # The donor's newest year is OLDER than the spine's newest. A positional
    # copy would put 9.0 on 2025; the year join must leave 2025 empty.
    out = _reindex_by_year(
        donor_years=[2024, 2023],
        donor_values=[9.0, 8.0],
        spine_years=[2025, 2024, 2023],
    )
    assert out == [None, 9.0, 8.0]


def test_reindex_returns_empty_when_no_year_overlaps():
    out = _reindex_by_year([2019, 2018], [1.0, 2.0], [2025, 2024])
    assert out == []


def test_reindex_never_joins_on_a_none_year():
    # A None year is not a key: it must not match the donor's None-keyed row.
    out = _reindex_by_year([None, 2024], [99.0, 5.0], [None, 2024])
    assert out == [None, 5.0]


def test_reindex_tolerates_a_short_value_series():
    # Ragged input must not raise (mirrors _financial_series' tolerance).
    out = _reindex_by_year([2025, 2024, 2023], [1.0], [2025, 2024])
    assert out == [1.0, None]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_statements_merge.py -q`
Expected: FAIL — `ImportError: cannot import name '_newest_year'`

- [ ] **Step 3: Write the implementation**

In `src/shortlist/data/models.py`, immediately after `_pick_first`/`_has_data` and before the `_INSIDER_TXN_FIELDS` block:

```python
# --- statements merge helpers --------------------------------------------
# `statements` is the one list-bearing section merged across sources. Every
# consumer aligns its parallel series by LIST POSITION (piotroski_f,
# bridge._financial_series, cagr, `[0]`-as-latest), so a backfill must join on
# the fiscal YEAR key or it silently pairs one source's 2022 revenue with
# another's 2023 share count.

def _newest_year(years: list[Optional[int]]) -> Optional[int]:
    """Newest real fiscal year in a spine, ignoring None holes. None if there
    are no usable years (never assumes newest-first ordering)."""
    real = [y for y in years if y is not None]
    return max(real) if real else None


def _usable_years(st: "Statements") -> Optional[list[Optional[int]]]:
    """A Statements' fiscal-year spine, or None when it cannot serve as a join
    key: empty (nothing to key on) or containing duplicates (ambiguous — a
    52/53-week fiscal can put two period ends in one calendar year)."""
    years = st.fiscal_years
    if not years:
        return None
    real = [y for y in years if y is not None]
    if len(set(real)) != len(real):
        return None
    return years


def _reindex_by_year(donor_years: list[Optional[int]],
                     donor_values: list, spine_years: list[Optional[int]]) -> list:
    """Re-index a donor series onto the spine's fiscal-year keys: the returned
    list is spine-length and spine-ordered, with None wherever the donor has no
    row for that year. A None year is NOT a key (an unparseable date on both
    sides must not join to itself). Returns [] when nothing lands, so
    `_is_present` still reads the field as absent rather than as a list of
    Nones. Ragged inputs are tolerated, never raised on."""
    by_year: dict[int, object] = {}
    for y, v in zip(donor_years, donor_values, strict=False):
        if y is not None:
            by_year[y] = v
    out = [by_year.get(y) if y is not None else None for y in spine_years]
    return out if any(v is not None for v in out) else []
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_statements_merge.py -q`
Expected: PASS (7 tests)

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src tests
git add src/shortlist/data/models.py tests/test_statements_merge.py
git commit -m "feat(merge): pure fiscal-year join helpers for the statements merge"
```

---

### Task 2: The bespoke `_merge_statements`

**Files:**
- Modify: `src/shortlist/data/models.py` (add `_STATEMENTS_LATEST_FY_SCALARS` + `_merge_statements`; route in `merge_snapshots`)
- Modify: `src/shortlist/data/sources/edgar.py:216` (comment)
- Test: `tests/test_statements_merge.py` (append)

**Interfaces:**
- Consumes: `_newest_year`, `_usable_years`, `_reindex_by_year` (Task 1); `_is_present`, `_has_data`, `dataclasses.replace`
- Produces: `_merge_statements(instances: list[tuple[str, Optional[Statements]]]) -> tuple[Optional[Statements], list[str]]` — the same `(merged, contributors)` contract as `_merge_flat`/`_merge_insider`/`_pick_first`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_statements_merge.py`:

```python
# --- the merger -----------------------------------------------------------

def _sr(source: str, st: Statements) -> SourceResult:
    return SourceResult(source=source, partial=TickerSnapshot(ticker="X", statements=st))


def _fmp_st() -> Statements:
    """An FMP-shaped Statements: 5 fiscal years, no EDGAR-only fields."""
    return Statements(
        fiscal_years=[2025, 2024, 2023, 2022, 2021],
        revenue=[500.0, 450.0, 400.0, 350.0, 300.0],
        gross_profit=[250.0, 225.0, 200.0, 175.0, 150.0],
        net_income=[50.0, 45.0, 40.0, 35.0, 30.0],
        total_equity=[900.0, 850.0, 800.0, 750.0, 700.0],
    )


def _edgar_st(newest: int = 2025) -> Statements:
    """An EDGAR-shaped Statements: 3 fiscal years, EDGAR-only fields populated."""
    years = [newest, newest - 1, newest - 2]
    return Statements(
        fiscal_years=years,
        fiscal_period_end=[f"{y}-09-28" for y in years],
        revenue=[500.0, 450.0, 400.0],
        diluted_shares=[1102.5, 1050.0, 1000.0],
        diluted_eps=[4.5, 4.2, 4.0],
        total_assets=[3000.0, 2800.0, 2600.0],
        asset_growth=0.0714,
        accruals=-0.02,
        dividends_paid=15.0,
        repurchases=80.0,
        debt_repayments=30.0,
        debt_issuance=10.0,
    )


def _merged(priority=("fmp", "edgar")) -> TickerSnapshot:
    return merge_snapshots(
        "X", [_sr("fmp", _fmp_st()), _sr("edgar", _edgar_st())], priority=list(priority)
    )


def test_edgar_only_fields_survive_an_fmp_won_merge():
    st = _merged().statements
    # FMP keeps the spine: 5 years of revenue, untouched.
    assert st.fiscal_years == [2025, 2024, 2023, 2022, 2021]
    assert st.revenue == [500.0, 450.0, 400.0, 350.0, 300.0]
    assert st.gross_profit[0] == 250.0
    # EDGAR-only lists are recovered, year-joined onto the 5-year spine.
    assert st.diluted_shares == [1102.5, 1050.0, 1000.0, None, None]
    assert st.diluted_eps == [4.5, 4.2, 4.0, None, None]
    assert st.total_assets == [3000.0, 2800.0, 2600.0, None, None]
    assert st.fiscal_period_end == ["2025-09-28", "2024-09-28", "2023-09-28", None, None]


def test_latest_fy_scalars_copy_when_newest_years_agree():
    st = _merged().statements
    assert st.asset_growth == 0.0714
    assert st.accruals == -0.02
    assert st.dividends_paid == 15.0
    assert st.repurchases == 80.0
    assert st.debt_repayments == 30.0
    assert st.debt_issuance == 10.0


def test_latest_fy_scalars_abstain_when_the_donor_vintage_is_older():
    # EDGAR's newest FY is 2024, the spine's is 2025: a "latest FY" scalar would
    # describe a different year than the object's [0] row -> abstain.
    merged = merge_snapshots(
        "X", [_sr("fmp", _fmp_st()), _sr("edgar", _edgar_st(newest=2024))],
        priority=["fmp", "edgar"],
    )
    st = merged.statements
    assert st.asset_growth is None
    assert st.accruals is None
    assert st.repurchases is None
    # ...but the LIST fields still backfill on their matching years.
    assert st.diluted_shares == [None, 1102.5, 1050.0, 1000.0, None]


def test_provenance_lists_both_contributors_in_priority_order():
    assert _merged().provenance["statements"] == ["fmp", "edgar"]


def test_source_partials_are_never_mutated():
    fmp_sr, edgar_sr = _sr("fmp", _fmp_st()), _sr("edgar", _edgar_st())
    merge_snapshots("X", [fmp_sr, edgar_sr], priority=["fmp", "edgar"])
    # The winner is copied, not aliased: the source object is unchanged.
    assert fmp_sr.partial.statements == _fmp_st()
    assert fmp_sr.partial.statements.diluted_shares == []
    assert edgar_sr.partial.statements == _edgar_st()


def test_reverse_direction_fmp_backfills_gross_profit_when_edgar_wins():
    # The claim in sources/edgar.py's comment: "the merge layer fills them from
    # FMP when available." Now true.
    merged = merge_snapshots(
        "X", [_sr("fmp", _fmp_st()), _sr("edgar", _edgar_st())], priority=["edgar", "fmp"]
    )
    st = merged.statements
    assert st.fiscal_years == [2025, 2024, 2023]        # EDGAR spine
    assert st.gross_profit == [250.0, 225.0, 200.0]     # from FMP, year-joined
    assert st.total_equity == [900.0, 850.0, 800.0]
    assert st.diluted_shares == [1102.5, 1050.0, 1000.0]


def test_single_source_merge_is_unchanged():
    only_fmp = merge_snapshots("X", [_sr("fmp", _fmp_st())], priority=["fmp", "edgar"])
    assert only_fmp.statements == _fmp_st()
    assert only_fmp.provenance["statements"] == ["fmp"]
    only_edgar = merge_snapshots("X", [_sr("edgar", _edgar_st())], priority=["fmp", "edgar"])
    assert only_edgar.statements == _edgar_st()
    assert only_edgar.provenance["statements"] == ["edgar"]


def test_spine_without_a_year_key_disables_backfill():
    spine = Statements(revenue=[1.0, 2.0])          # no fiscal_years
    merged = merge_snapshots(
        "X", [_sr("fmp", spine), _sr("edgar", _edgar_st())], priority=["fmp", "edgar"]
    )
    assert merged.statements.diluted_shares == []   # no join key -> no guess
    assert merged.provenance["statements"] == ["fmp"]


def test_a_donor_with_duplicate_years_is_skipped_not_fatal():
    # finnhub-shaped junk donor with an ambiguous spine must not veto edgar.
    dupe = Statements(fiscal_years=[2025, 2025], diluted_shares=[1.0, 2.0])
    merged = merge_snapshots(
        "X",
        [_sr("fmp", _fmp_st()), _sr("finnhub", dupe), _sr("edgar", _edgar_st())],
        priority=["fmp", "finnhub", "edgar"],
    )
    assert merged.statements.diluted_shares == [1102.5, 1050.0, 1000.0, None, None]
    assert merged.provenance["statements"] == ["fmp", "edgar"]


def test_a_donor_year_outside_the_spine_is_dropped_not_shifted():
    # EDGAR has a 2026 fiscal year the FMP spine doesn't carry. That row must be
    # DROPPED, not shifted onto 2025 (which is what a positional copy would do).
    merged = merge_snapshots(
        "X", [_sr("fmp", _fmp_st()), _sr("edgar", _edgar_st(newest=2026))],
        priority=["fmp", "edgar"],
    )
    st = merged.statements
    assert st.diluted_shares == [1050.0, 1000.0, None, None, None]  # 2025, 2024 only
    assert 1102.5 not in st.diluted_shares                          # the 2026 row is gone
    assert st.asset_growth is None            # newest years disagree -> scalars abstain


def test_all_empty_statements_merge_to_none():
    merged = merge_snapshots(
        "X", [_sr("fmp", Statements()), _sr("edgar", Statements())],
        priority=["fmp", "edgar"],
    )
    assert merged.statements is None
    assert "statements" not in merged.provenance


def test_every_statements_field_is_covered_by_the_merger():
    # Guard: a NEW Statements field must land in one of the two buckets (list
    # series or latest-FY scalar) or it will be silently dropped on merge.
    from dataclasses import fields

    from shortlist.data.models import _STATEMENTS_LATEST_FY_SCALARS
    blank = Statements()
    lists = {f.name for f in fields(blank) if isinstance(getattr(blank, f.name), list)}
    assert lists | set(_STATEMENTS_LATEST_FY_SCALARS) == {f.name for f in fields(blank)}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_statements_merge.py -q`
Expected: FAIL — `test_edgar_only_fields_survive_an_fmp_won_merge` asserts `diluted_shares == [...]` but gets `[]` (today's `_pick_first` behaviour), and the `_STATEMENTS_LATEST_FY_SCALARS` import raises `ImportError`.

- [ ] **Step 3: Write the implementation**

In `src/shortlist/data/models.py`, after the Task 1 helpers:

```python
# Pre-computed latest-fiscal-year scalars. The SOURCE aligns their inputs by its
# own statement dates (the bridge can't), so they carry no positional risk — but
# a latest-FY scalar attached to a NEWER spine would read as current in
# --json/CSV with nothing marking the vintage. Copied only on a newest-year
# match; abstain otherwise.
_STATEMENTS_LATEST_FY_SCALARS = (
    "asset_growth", "accruals", "dividends_paid", "repurchases",
    "debt_repayments", "debt_issuance",
)


def _merge_statements(
    instances: list[tuple[str, Optional["Statements"]]],
) -> tuple[Optional["Statements"], list[str]]:
    """Priority-ordered, fiscal-year-joined merge of the one list-bearing
    section. The highest-priority source with data wins the object outright and
    its `fiscal_years` becomes the join key — so the spine's own series (and
    every growth leg derived from them) are byte-identical to the old
    whole-source pick. Fields the spine left EMPTY are then backfilled from
    lower-priority sources, re-indexed onto that spine by YEAR, never by list
    position: every consumer of Statements reads its parallel series by index,
    so a positional backfill would pair one source's 2022 revenue with another's
    2023 share count with no test failing. Source-agnostic: it composes whatever
    `harness_sources` order is configured.

    Abstains (leaves a field empty) rather than guessing: a spine with no or
    duplicate fiscal years disables backfill entirely; an individual donor with
    the same problem is skipped without vetoing the donors after it."""
    present = [(s, o) for s, o in instances if o is not None and _has_data(o)]
    if not present:
        return None, []
    spine_src, spine = present[0]
    merged = dataclasses.replace(spine)      # copy: never alias SourceResult.partial
    contributors = [spine_src]

    spine_years = _usable_years(spine)
    if spine_years is None:
        return merged, contributors          # no join key -> pre-change behaviour

    spine_newest = _newest_year(spine_years)
    list_fields = [f.name for f in fields(merged)
                   if f.name != "fiscal_years"
                   and isinstance(getattr(merged, f.name), list)]

    for src, donor in present[1:]:
        donor_years = _usable_years(donor)
        if donor_years is None:
            continue
        used = False
        for name in list_fields:
            if _is_present(getattr(merged, name)):
                continue                     # the spine already supplied it
            donor_vals = getattr(donor, name)
            if not _is_present(donor_vals):
                continue
            filled = _reindex_by_year(donor_years, donor_vals, spine_years)
            if filled:
                setattr(merged, name, filled)
                used = True
        if spine_newest is not None and _newest_year(donor_years) == spine_newest:
            for name in _STATEMENTS_LATEST_FY_SCALARS:
                if getattr(merged, name) is None and getattr(donor, name) is not None:
                    setattr(merged, name, getattr(donor, name))
                    used = True
        if used and src not in contributors:
            contributors.append(src)
    return merged, contributors
```

Then route it in `merge_snapshots` — replace the `else: merger = _pick_first` branch for the KEY_OBJECTS loop:

```python
        if name == "insider":
            merger = _merge_insider
        elif name == "statements":
            merger = _merge_statements
        elif name in _FLAT:
            merger = _merge_flat
        else:
            merger = _pick_first
```

And update the comment above `_FLAT` (it currently says statements takes "the best whole source", which stops being true):

```python
# Flat objects merge field-by-field; `insider` and `statements` have bespoke
# mergers (above); the aux sections take the best whole source.
_FLAT = {"profile", "fundamentals", "analyst", "price"}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_statements_merge.py -q`
Expected: PASS (all 18 tests)

- [ ] **Step 5: Run the FULL suite — this changes a shared merge path**

Run: `uv run pytest -q`
Expected: PASS. If `tests/test_harness.py`, `tests/test_edgar_events.py`, `tests/test_sources_leverage.py` or `tests/test_bridge_leverage.py` fail, read the failure before touching it: a test asserting `merged.statements is source.statements` is asserting the aliasing bug and should become an equality assertion; a test asserting a field is empty for an FMP-won merge is asserting the defect and should be updated with a comment naming this change. Any OTHER failure means the merger is wrong — fix the merger, not the test.

- [ ] **Step 6: Make the stale edgar.py comment true**

In `src/shortlist/data/sources/edgar.py`, line ~216, replace:

```python
        # gross_profit/total_equity aren't in EdgarFinancials; the merge layer fills them from FMP when available.
```

with:

```python
        # gross_profit/total_equity aren't in EdgarFinancials; _merge_statements
        # year-joins them back in from FMP when available (docs/STATEMENTS_MERGE.md).
```

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check src tests
git add src/shortlist/data/models.py src/shortlist/data/sources/edgar.py tests/test_statements_merge.py
git commit -m "fix(merge): year-joined statements backfill — stop dropping EDGAR-only fields"
```

---

### Task 3: End-to-end regression, live verification, docs

**Files:**
- Test: `tests/test_statements_merge.py` (append)
- Modify: `CLAUDE.md`, `TODO.md`

**Interfaces:**
- Consumes: `_merge_statements` via `merge_snapshots` (Task 2); `shortlist.data.bridge.snapshot_to_metrics`; `shortlist.scoring.score`

- [ ] **Step 1: Write the failing end-to-end test**

Append to `tests/test_statements_merge.py`. This is the test that pins the one live behaviour change — the `dilution` flag becoming able to fire on an FMP-covered name.

`_edgar_st()`'s `diluted_shares` are `[1102.5, 1050.0, 1000.0]` newest-first, so `stats.cagr` (which drops Nones, reverses to oldest-first, then compounds over `len-1` periods) gives `(1102.5/1000) ** (1/2) - 1 = 0.05` — above the shipped `flags.dilution.min_share_cagr` of 0.03.

**Use the REAL `config.yaml`**, via the established idiom in `tests/test_sectors_config.py:9`. Two reasons: `score()` does a bare `config["thresholds"]` / `config["weights"]` lookup and raises `KeyError` on a sparse dict; and the claim being pinned is about *shipped* behaviour, so if someone disables `flags.dilution` this test should fail loudly.

```python
# --- end-to-end: the live behaviour this fix restores ---------------------

def _shipped_config() -> dict:
    import yaml
    from pathlib import Path
    return yaml.safe_load((Path(__file__).parents[1] / "config.yaml").read_text())


def test_dilution_flag_can_now_fire_on_an_fmp_covered_name():
    from shortlist.data.bridge import snapshot_to_metrics
    from shortlist.scoring import score

    cfg = _shipped_config()
    # Self-documenting precondition: the flag ships ON, and the fixture's 5%/yr
    # issuance must clear whatever floor is configured.
    assert cfg["flags"]["dilution"]["min_share_cagr"] <= 0.05

    snap = _merged()                       # fmp wins the spine, edgar backfills
    m = snapshot_to_metrics(snap)
    # cagr over [1102.5, 1050.0, 1000.0] newest-first = 5%/yr net issuance.
    assert m.share_count_cagr is not None
    assert abs(m.share_count_cagr - 0.05) < 1e-9

    assert "dilution" in score(m, cfg).flags


def test_dilution_flag_could_not_fire_before_the_fix():
    # The same FMP snapshot WITHOUT edgar in the chain: share_count_cagr stays
    # None, so the flag is structurally unreachable. This is what every
    # non-402 name looked like before this change.
    from shortlist.data.bridge import snapshot_to_metrics
    from shortlist.scoring import score

    snap = merge_snapshots("X", [_sr("fmp", _fmp_st())], priority=["fmp", "edgar"])
    m = snapshot_to_metrics(snap)
    assert m.share_count_cagr is None

    assert "dilution" not in score(m, _shipped_config()).flags


def test_measurement_inputs_reach_the_metrics():
    # The §3/§5 measurement inputs the accumulation store was persisting empty.
    from shortlist.data.bridge import snapshot_to_metrics

    m = snapshot_to_metrics(_merged())
    assert m.asset_growth == 0.0714
    assert m.accruals == -0.02
    assert m.eps_cagr_ps is not None       # from the recovered diluted_eps
```

- [ ] **Step 2: Run to verify the new tests fail on the pre-Task-2 code and pass now**

Run: `uv run pytest tests/test_statements_merge.py -q`
Expected: PASS. (Sanity-check the regression is real: `git stash` the Task 2 change and confirm `test_dilution_flag_can_now_fire_on_an_fmp_covered_name` FAILS, then `git stash pop`. A regression test that passes without the fix is worthless.)

- [ ] **Step 3: Live verification — no claim without a run**

The repo rule is that a data-path claim needs a real run, not a fixture. Run a real screen on a name FMP does *not* 402-gate (AAPL/MSFT/LMT per CLAUDE.md), on this branch and on `main`, and diff the recovered fields:

```bash
OUT="$(mktemp -d)"
uv run shortlist AAPL --json > "$OUT/after.json"
git stash
uv run shortlist AAPL --json > "$OUT/before.json"
git stash pop
for f in before after; do
  echo "== $f"; python3 -c "import json,sys; d=json.load(open('$OUT/$f.json')); \
r=(d['results'] if isinstance(d,dict) and 'results' in d else d)[0]; \
print({k:r.get(k) for k in ('share_count_cagr','asset_growth','accruals')})"
done
```

(If the `--json` top-level shape isn't a dict with `results`, read `src/shortlist/screen.py:262` for the actual key layout and adjust the extractor — don't guess.)

Expected: `share_count_cagr`, `asset_growth`, `accruals` are `null` in `before.json` and populated in `after.json`. Requires `SEC_IDENTITY` (EDGAR must be in the chain) and an FMP key; if either is missing the run proves nothing — say so plainly rather than reporting a pass. Record the actual before/after values in the commit message.

- [ ] **Step 4: Update CLAUDE.md**

In the "Insider merge (harness)" section, add a sibling paragraph immediately after it:

```markdown
## Statements merge (harness)

`statements` is the other bespoke merger (`data/models.py:_merge_statements`) — it is
**not** `_pick_first` and **not** in `_FLAT`. The highest-priority source with data wins
the object and its `fiscal_years` becomes a **join key**; fields it left empty are
backfilled from lower-priority sources **re-indexed by fiscal YEAR, never by list
position**. This is load-bearing: every consumer (`piotroski_f`, `bridge._financial_series`,
`cagr`, `[0]`-as-latest) reads the parallel series by index, and FMP typically carries 5
fiscal years to EDGAR's ~3 — so a positional backfill pairs mismatched years silently.
The six pre-computed latest-FY scalars (`asset_growth`, `accruals`, and the four §5
financing legs) copy **only when the donor's newest fiscal year matches the spine's**.
Abstains rather than guesses: no/duplicate `fiscal_years` on the spine disables backfill;
a donor with the same problem is skipped without vetoing later donors. Before this
existed, FMP won `statements` wholesale for every non-402 name and **every EDGAR-only
field was discarded** — which made the ON-by-default `dilution` flag structurally
incapable of firing on exactly the best-covered names. Design: `docs/STATEMENTS_MERGE.md`.
```

- [ ] **Step 5: Close the TODO item**

In `TODO.md`, under "## Data audit — 4 fixes shipped..." (2026-07-20), replace the item-1 heading line:

```markdown
1. **FMP-won statements silently drop every EDGAR-only field** — `statements` is a
```

with:

```markdown
1. ~~**FMP-won statements silently drop every EDGAR-only field**~~ — **FIXED 2026-07-30.**
   Resolved by option (b): a bespoke `_merge_statements` (`data/models.py`) year-joins the
   EDGAR-only fields onto the FMP-won spine instead of discarding them. Design +
   verified consequence chain: `docs/STATEMENTS_MERGE.md`; plan:
   `docs/PLAN_STATEMENTS_MERGE.md`. The `dilution` flag can now fire on FMP-covered names.
   **Residual:** already-persisted accumulation snapshots stay degraded — there is no
   retroactive repair, so the store is complete only from the deploy date forward.
   Original text: `statements` is a
```

Leave the rest of the item's prose intact below that line (it documents the reasoning and the rejected option (a)). Then update the section's closing `**Status:**` line — it currently calls item 1 "the highest-value build"; change that clause to note item 1 is shipped and item 2 (the `net_debt_to_ebitda` re-measure) is what remains.

- [ ] **Step 6: Full suite, lint, commit**

```bash
uv run ruff check src tests
uv run pytest -q
git add tests/test_statements_merge.py CLAUDE.md TODO.md
git commit -m "test(merge): pin the dilution flag regression; document the statements merge"
```

---

## Done When

- `uv run ruff check src tests` clean, `uv run pytest -q` green.
- A real `--json` run on a non-402 name shows `share_count_cagr`/`asset_growth`/`accruals` populated where `main` returns null — or an explicit statement of which credential was missing and what therefore went unverified.
- `docs/STATEMENTS_MERGE.md`, `CLAUDE.md` and `TODO.md` agree with the code.
- **Not done here:** deployment. `/opt/shortlist` keeps running the old code until `git pull` + `sudo bash deploy/install_opt_shortlist.sh`; the accumulate timer only starts capturing the recovered fields after that.

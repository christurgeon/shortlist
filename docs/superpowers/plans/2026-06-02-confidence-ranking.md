# Confidence Surfacing + Safe Tiebreak Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface `confidence` (column + `thin` marker) and add confidence as an exact-tie breaker in a single shared `rank_key`, without distorting `composite` or burying strong-but-thin candidates.

**Architecture:** `rank_key(card)` is a `getattr`-based module function in `models.py` returning `(scored, composite, confidence)` — robust to both real `ScoreCard`s and the duck-typed cards `enrich()` accepts. It replaces the inline sort keys at the two screener sort sites and the research re-sort. A display-only `ScoreCard.thin` bool (config-gated via `ranking.thin_below`) is computed in `score()` and surfaced; it never feeds `rank_key`, `passed`, or `composite`.

**Tech Stack:** Python 3.11+, pytest, uv. Files: `src/shortlist/models.py`, `src/shortlist/scoring.py`, `src/shortlist/screen.py`, `src/shortlist/research/__init__.py`, `src/shortlist/scout/report.py`, `config.yaml`.

**Spec:** `docs/superpowers/specs/2026-06-02-confidence-ranking-design.md`

---

## File structure

- `src/shortlist/models.py` — add `rank_key(card)` module function; add `ScoreCard.thin` field (last).
- `src/shortlist/scoring.py` — compute `thin` in `score()` (config-gated); pass `thin=`.
- `src/shortlist/screen.py` — use `rank_key` at both sort sites; surface confidence/thin in JSON, CSV, tables, `_flags_cell`.
- `src/shortlist/research/__init__.py` — `enrich()` re-sort uses `rank_key`.
- `src/shortlist/scout/report.py` — axis line shows confidence.
- `config.yaml` — add `ranking.thin_below`.

---

## Task 1: `rank_key` function + `ScoreCard.thin` field

**Files:**
- Modify: `src/shortlist/models.py` (add module function; append field after `risk`, ~line 131)
- Test: `tests/test_scorecard_fields.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_scorecard_fields.py`:

```python
def test_rank_key_orders_scored_then_composite_then_confidence():
    from shortlist.models import rank_key
    a = _card(composite=80.0, confidence=0.30, scored=True)
    b = _card(composite=78.0, confidence=1.0, scored=True)
    # composite dominates: the thin 80 still ranks ABOVE the complete 78 (no-bury).
    assert sorted([b, a], key=rank_key, reverse=True) == [a, b]
    # equal composite -> confidence breaks the tie (higher first)
    c_hi = _card(composite=80.0, confidence=0.90, scored=True)
    assert sorted([a, c_hi], key=rank_key, reverse=True) == [c_hi, a]
    # scored dominates composite
    not_scored = _card(composite=95.0, confidence=1.0, scored=False)
    scored = _card(composite=50.0, confidence=1.0, scored=True)
    assert sorted([not_scored, scored], key=rank_key, reverse=True) == [scored, not_scored]


def test_rank_key_works_on_duck_typed_card_without_confidence():
    from shortlist.models import rank_key
    class _Loose:
        composite = 70.0
        scored = True
    # no `confidence` attr -> getattr default 1.0, no AttributeError
    assert rank_key(_Loose()) == (True, 70.0, 1.0)


def test_thin_field_defaults_false():
    assert _card().thin is False
    assert _card(thin=True).thin is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_scorecard_fields.py -k "rank_key or thin_field" -v`
Expected: FAIL — `ImportError: cannot import name 'rank_key'` / `unexpected keyword argument 'thin'`.

- [ ] **Step 3: Add the field and function**

In `src/shortlist/models.py`, append `thin` after the `risk` field:

```python
    # 7th sub-score (risk). Appended last so positional construction through the
    # leading fields is unaffected. Composite-only tilt; excluded from confidence.
    risk: Optional[float] = None
    # Display-only coverage advisory (confidence < ranking.thin_below). Derived from
    # confidence; never feeds rank_key/passed/composite. Appended last.
    thin: bool = False
```

Add a module-level function (place it after the `ScoreCard` class definition, near
`passed`/other helpers — top-level, not inside the class):

```python
def rank_key(card) -> tuple:
    """Ranking order, descending: scored first, then composite, then confidence as a
    tiebreaker. composite is rounded to 0.1 (scoring.py), so confidence only decides
    exact ties — a higher composite always wins (we never bury a strong-but-thin name).
    getattr-based so it also works on the duck-typed cards enrich() accepts. Single
    source of truth for every sort site (screen, research, scout)."""
    return (getattr(card, "scored", True), card.composite, getattr(card, "confidence", 1.0))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_scorecard_fields.py -k "rank_key or thin_field" -v`
Expected: PASS (all three)

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/models.py tests/test_scorecard_fields.py
git commit -m "feat(scoring): add rank_key function + ScoreCard.thin field"
```

---

## Task 2: Use `rank_key` at all three sort sites

**Files:**
- Modify: `src/shortlist/screen.py:48` (`run`), `:70` (`run_harness`)
- Modify: `src/shortlist/research/__init__.py:63` (`enrich`)
- Test: `tests/test_screen_engine.py`, `tests/research/test_enrich.py` (regression — must stay green)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_screen_engine.py` (a no-bury + tiebreak integration check on the sort
helper as used by the engine). If the file already imports `_card`/helpers, reuse them;
otherwise this uses `ScoreCard` directly:

```python
def test_rank_key_sort_is_no_bury_and_tiebreaks_on_confidence():
    from shortlist.models import ScoreCard, rank_key
    thin80 = ScoreCard(ticker="THIN", composite=80.0, quality=None, moat=None,
                       growth=None, momentum=None, value=None, opportunity=80.0,
                       insider=None, confidence=0.30, scored=True)
    full78 = ScoreCard(ticker="FULL", composite=78.0, quality=78.0, moat=78.0,
                       growth=78.0, momentum=78.0, value=78.0, opportunity=78.0,
                       insider=78.0, confidence=1.0, scored=True)
    ordered = sorted([full78, thin80], key=rank_key, reverse=True)
    assert [c.ticker for c in ordered] == ["THIN", "FULL"]   # composite dominates
```

- [ ] **Step 2: Run test to verify it fails (or passes trivially) — then make the change**

Run: `uv run pytest tests/test_screen_engine.py::test_rank_key_sort_is_no_bury_and_tiebreaks_on_confidence -v`
Expected: PASS already (it tests `rank_key` from Task 1 directly). This test guards the
invariant; the behavioral change is at the sort sites below.

- [ ] **Step 3: Update the two screener sort sites**

In `src/shortlist/screen.py`, add the import (top of file, with the other `.models`
import) and replace both sort keys. The import line currently reads
`from .models import ScoreCard`; change to:

```python
from .models import ScoreCard, rank_key
```

At `screen.py:48` (in `run`) and `screen.py:70` (in `run_harness`), replace:

```python
    cards.sort(key=lambda c: (c.scored, c.composite), reverse=True)
```

with:

```python
    cards.sort(key=rank_key, reverse=True)
```

- [ ] **Step 4: Update the research re-sort**

In `src/shortlist/research/__init__.py`, add the import and change the `enrich` re-sort.
At the top of the file add (alongside existing imports):

```python
from ..models import rank_key
```

At `research/__init__.py:63`, replace:

```python
    ranked = sorted(cards, key=lambda c: c.composite, reverse=True)
```

with:

```python
    ranked = sorted(cards, key=rank_key, reverse=True)
```

- [ ] **Step 5: Run the affected suites (regression)**

Run: `uv run pytest tests/test_screen_engine.py tests/research/test_enrich.py tests/scout/ -q`
Expected: PASS. The duck-typed `_Card` doubles in `test_enrich.py` (no `confidence`) sort
via the `getattr` default `1.0` — no `AttributeError`. `test_screen_engine.py:20`
(`comps == sorted(comps, reverse=True)`) still holds (composite still dominates).

- [ ] **Step 6: Commit**

```bash
git add src/shortlist/screen.py src/shortlist/research/__init__.py tests/test_screen_engine.py
git commit -m "feat(ranking): single rank_key (scored,composite,confidence) at all sort sites"
```

---

## Task 3: Compute `thin` in `score()` (config-gated)

**Files:**
- Modify: `src/shortlist/scoring.py` — `score()` confidence block (~line 305) and the `ScoreCard(...)` return (~line 318)
- Test: `tests/test_scoring.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_scoring.py`:

```python
def test_thin_flag_set_below_threshold():
    import copy, dataclasses
    rc = copy.deepcopy(CONFIG)
    rc["ranking"] = {"thin_below": 0.5}
    # momentum-only name -> confidence well below 0.5 -> thin
    m = StockMetrics(ticker="MOM", price_vs_200dma=0.2, rel_strength_6m=0.2,
                     eps_revision=0.05)
    card = score(m, rc)
    assert 0.0 < card.confidence < 0.5
    assert card.thin is True


def test_thin_flag_false_above_threshold():
    rc = {**CONFIG, "ranking": {"thin_below": 0.5}}
    card = score(metrics_all_50(), rc)   # fully covered -> confidence 1.0
    assert card.thin is False


def test_thin_noop_when_ranking_absent():
    # CONFIG has no `ranking` block -> thin always False, no KeyError
    m = StockMetrics(ticker="MOM", price_vs_200dma=0.2, rel_strength_6m=0.2,
                     eps_revision=0.05)
    assert score(m, CONFIG).thin is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_scoring.py -k "thin_flag or thin_noop" -v`
Expected: FAIL — `card.thin` is always `False` (field default) because nothing sets it yet.

- [ ] **Step 3: Compute `thin` and pass it through**

In `src/shortlist/scoring.py`, after the `scored = ...` line in `score()`, add:

```python
    # Display-only coverage advisory; config-gated and None-safe (absent block -> False).
    thin_below = (config.get("ranking") or {}).get("thin_below")
    thin = thin_below is not None and confidence < thin_below
```

Then add `thin=thin,` to the `ScoreCard(...)` return, after `risk=_round(ri),`:

```python
        sic_bucket=bucket, confidence=confidence, scored=scored, abstentions=abst,
        risk=_round(ri),
        thin=thin,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_scoring.py -k "thin_flag or thin_noop" -v`
Expected: PASS (all three)

- [ ] **Step 5: Prove `thin` is inert (doesn't leak into ranking/eligibility)**

Add to `tests/test_scorecard_fields.py`:

```python
def test_thin_does_not_affect_rank_key_or_passed():
    from shortlist.models import rank_key
    a = _card(composite=70.0, confidence=0.30, scored=True, thin=True)
    b = _card(composite=70.0, confidence=0.30, scored=True, thin=False)
    assert rank_key(a) == rank_key(b)
    assert a.passed == b.passed
```

Run: `uv run pytest tests/test_scorecard_fields.py::test_thin_does_not_affect_rank_key_or_passed -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/shortlist/scoring.py tests/test_scoring.py tests/test_scorecard_fields.py
git commit -m "feat(scoring): compute display-only thin flag (config ranking.thin_below)"
```

---

## Task 4: Surface confidence + thin (JSON, CSV, tables, flags, scout)

**Files:**
- Modify: `src/shortlist/screen.py` — `_card_dict`, `_write_csv`, `_print_table`, `_print_plain`, `_flags_cell`
- Modify: `src/shortlist/scout/report.py:18-19`
- Test: `tests/test_card_dict_abstention.py`, `tests/test_scoring.py`, `tests/test_screen_engine.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_card_dict_abstention.py`:

```python
def test_card_dict_includes_thin():
    d = _card_dict(_c(thin=True))
    assert d["thin"] is True
```

Add to `tests/test_scoring.py`:

```python
def test_csv_has_confidence_column_after_scored(tmp_path):
    import csv
    from shortlist.screen import _write_csv
    from shortlist.models import ScoreCard
    card = ScoreCard(ticker="T", composite=60.0, quality=None, moat=None, growth=None,
                     momentum=None, value=None, opportunity=None, insider=None,
                     confidence=0.42, scored=True)
    path = tmp_path / "out.csv"
    _write_csv([card], str(path))
    rows = list(csv.reader(path.open()))
    header, row = rows[0], rows[1]
    assert "confidence" in header
    assert header.index("confidence") == header.index("scored") + 1
    assert row[header.index("confidence")] == str(card.confidence)
```

Add to `tests/test_screen_engine.py` (extend the flags-cell coverage):

```python
def test_flags_cell_appends_thin():
    from shortlist.models import ScoreCard
    from shortlist.screen import _flags_cell
    c = ScoreCard(ticker="T", composite=50.0, quality=None, moat=None, growth=None,
                  momentum=None, value=None, opportunity=None, insider=None, thin=True)
    assert "thin" in _flags_cell(c).split(",")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_card_dict_abstention.py::test_card_dict_includes_thin tests/test_scoring.py::test_csv_has_confidence_column_after_scored tests/test_screen_engine.py::test_flags_cell_appends_thin -v`
Expected: FAIL — `KeyError: 'thin'`, `'confidence' not in header`, `'thin' not in cell`.

- [ ] **Step 3: Add `thin` to `_card_dict`**

In `_card_dict` (`screen.py`), add `"thin": c.thin,` right after `"scored": c.scored,`:

```python
        "sic_bucket": c.sic_bucket,
        "confidence": c.confidence,
        "scored": c.scored,
        "thin": c.thin,
    }
```

- [ ] **Step 4: Add `confidence` column to `_write_csv` (after `scored`)**

In `_write_csv`, update header and row in lockstep:

```python
        w.writerow(["rank", "ticker", "composite", "quality", "moat", "growth",
                    "momentum", "value", "opportunity", "insider", "risk",
                    "upside_to_target", "gates", "scored", "confidence", "sic_bucket"])
        for i, c in enumerate(cards, 1):
            d = _card_dict(c)
            w.writerow([i, d["ticker"], d["composite"], d["quality"], d["moat"],
                        d["growth"], d["momentum"], d["value"], d["opportunity"],
                        d["insider"], d["risk"], d["upside_to_target"],
                        "|".join(d["gates"]), d["scored"], d["confidence"], d["sic_bucket"]])
```

- [ ] **Step 5: Append `thin` in `_flags_cell`**

Replace `_flags_cell` (`screen.py:74-76`):

```python
def _flags_cell(c: ScoreCard) -> str:
    """Combined chips for the 'Flags' column: hard gates first, then soft flags, then
    the display-only 'thin' coverage advisory."""
    chips = list(c.gates) + list(c.flags) + (["thin"] if getattr(c, "thin", False) else [])
    return ",".join(chips) or "-"
```

- [ ] **Step 6: Add a `Conf` column to the tables (after `Insdr`, before `Risk`)**

In `_print_table`, add `("Conf", "right", 5),` after the `Insdr` column:

```python
        ("Insdr",   "right", 5),
        ("Conf",    "right", 5),
        ("Risk",    "right", 5),
```

and add the value after `_f(c.insider)` in `table.add_row`:

```python
            _f(c.quality), _f(c.moat), _f(c.growth), _f(c.momentum), _f(c.value), _f(c.insider),
            f"{c.confidence:.2f}",
            _f(c.risk),
```

In `_print_plain`, add `CONF` to the header (after `INSD`) and the value (after
`_f(c.insider)`):

```python
    print(f"{'#':>2} {'TICK':<6} {'COMP':>5} {'QUAL':>5} {'MOAT':>5} {'GRW':>5} "
          f"{'MOM':>5} {'VAL':>5} {'INSD':>5} {'CONF':>5} {'RISK':>5}  FLAGS")
    for i, c in enumerate(cards, 1):
        print(f"{i:>2} {c.ticker:<6} {c.composite:>5} {_f(c.quality):>5} "
              f"{_f(c.moat):>5} {_f(c.growth):>5} {_f(c.momentum):>5} {_f(c.value):>5} "
              f"{_f(c.insider):>5} {c.confidence:>5.2f} {_f(c.risk):>5}  {_flags_cell(c)}")
```

- [ ] **Step 7: Add confidence to the scout report**

In `src/shortlist/scout/report.py:18-19`, append `Conf{...}` to the axis line, using
`getattr` for render robustness:

```python
        conf = getattr(c, "confidence", None)
        conf_str = f" Conf{conf:.2f}" if conf is not None else ""
        lines.append(f"   Q{_n(c.quality)} M{_n(c.moat)} G{_n(c.growth)} "
                     f"Opp{_n(c.opportunity)} Ins{_n(c.insider)} Rsk{_n(c.risk)}{conf_str}")
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/test_card_dict_abstention.py tests/test_scoring.py tests/test_screen_engine.py tests/scout/test_report.py -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add src/shortlist/screen.py src/shortlist/scout/report.py tests/
git commit -m "feat(output): surface confidence column + thin marker (json/csv/tables/scout)"
```

---

## Task 5: Activate `ranking.thin_below` in `config.yaml`

**Files:**
- Modify: `config.yaml` (add a `ranking:` block)
- Test: `tests/test_scoring.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_scoring.py`:

```python
def test_shipped_config_has_ranking_thin_below():
    cfg = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text())
    assert cfg["ranking"]["thin_below"] == 0.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scoring.py::test_shipped_config_has_ranking_thin_below -v`
Expected: FAIL — `KeyError: 'ranking'`.

- [ ] **Step 3: Add the block to `config.yaml`**

Append at the end of `config.yaml` (top-level block, sibling to `weights`/`gates`):

```yaml

# Ranking surface (docs/ASSESSMENT_GAPS.md §2.4). Display-only: does NOT affect the
# sort beyond the existing confidence tiebreak, nor `passed`/`composite`.
ranking:
  thin_below: 0.5   # mark a card "thin" when confidence < this. Omit/null to disable.
```

- [ ] **Step 4: Run test + full scoring suite**

Run: `uv run pytest tests/test_scoring.py -q`
Expected: PASS. (The shipped-config integration test still only checks bounds + the
`opportunity == max(...)` identity, and now `MockProvider` names may render `thin` — no
frozen value breaks.)

- [ ] **Step 5: Commit**

```bash
git add config.yaml tests/test_scoring.py
git commit -m "feat(config): add ranking.thin_below (display-only coverage advisory)"
```

---

## Task 6: Full regression + manual smoke

**Files:** none (verification only)

- [ ] **Step 1: Run the entire suite**

Run: `uv run pytest -q`
Expected: all pass (was 415 passed, 3 skipped; now higher pass count, still 3 skipped, 0 failed).

- [ ] **Step 2: Manual smoke — demo table shows a Conf column and thin markers**

Run: `uv run shortlist --demo`
Expected: a `Conf` column renders; thin names (confidence < 0.5) show `thin` in the Flags
cell; no crash.

- [ ] **Step 3: Manual smoke — JSON contains confidence + thin**

Run: `uv run shortlist --demo --json | head -40`
Expected: each card has `"confidence"` and `"thin"` keys.

- [ ] **Step 4: Manual smoke — CSV has the confidence column**

Run: `uv run shortlist --demo --csv /tmp/conf.csv && head -1 /tmp/conf.csv`
Expected: header contains `confidence` immediately after `scored`.

---

## Self-review notes (coverage vs spec)

- Spec §4.1 (`rank_key` module function, 3 sort sites) → Task 1 (function) + Task 2 (sites).
- Spec §3 (no-bury + tiebreak invariant) → Task 1 `test_rank_key_orders_...` + Task 2 integration test.
- Spec §4.2 (`thin` field + config-gated compute, inert) → Task 1 (field) + Task 3 (compute + inertness test).
- Spec §4.3 (surfacing: JSON thin, CSV confidence after scored, tables Conf after Insdr, `_flags_cell` thin, scout) → Task 4.
- Spec §4.4 (config block) → Task 5.
- Spec §5 test plan items 1-7 → Tasks 1-5 tests + Task 6 regression.
- Spec §6 (continuous tilt / demote floor / 402-aware confidence) → explicitly out of scope; no task.

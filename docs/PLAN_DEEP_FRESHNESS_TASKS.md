# `/deep` freshness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a `/deep` brief go stale when the world moves or the prompt changes, and give the
brief a YoY filing-text-change line at zero extra network cost.

**Architecture:** A new module `research/cachekey.py` composes the on-disk brief key from
(filing accessions) + (a fingerprint over the source of every prompt-shaping module **and** the
`research` config block) + (a digest of a *bucketed* materiality tuple covering everything
`_quant_context` renders) + (a day bucket). `_enrich_card` computes it **before** the
`is_cached` short-circuit. Separately, `fetch_bundle` reuses the prior-year 10-K it already
fetches for the risk diff to compute a Lazy-Prices cosine, surfaced as a prompt-only context
line and a rendered brief line.

**Tech Stack:** Python 3.13, stdlib only (`hashlib`, `inspect`, `math`, `datetime`), pytest, uv.

**Design spec:** `docs/PLAN_DEEP_FRESHNESS.md` — read it first. It records the verified
facts (with file:line) that this plan assumes.

## Global Constraints

- **Plan/spec docs live in tracked `docs/`.** `docs/superpowers/plans/` and
  `docs/superpowers/specs/` are gitignored (`.gitignore:37-38`) — never put an artifact there.
- **No scoring, gate, flag or composite semantics change.** Nothing in this plan may alter
  `passed`, `composite`, `scored`, or the set of flags any name receives.
- **The `filing_text_change` flag stays dormant.** `check_flags` runs inside `score()`
  (`scoring.py:809`) before research runs (`screen.py:188` then `:193`). Do **not** attempt to
  make it fire, and do not mutate `card.metrics.filing_text_similarity` — that would put a
  similarity in `--json` (`screen.py:287`) with no matching flag, which is worse than null.
- **Stdlib only.** No new dependency.
- **Every metric is `Optional` and test cards are duck-typed stubs without `.metrics`**
  (`tests/research/test_enrich.py:6-15`). Every read is `getattr(..., None)`-guarded.
- **Absent config ⇒ documented default.** `research.cache.*` defaults to
  `max_age_days: 1`, `price_band_pct: 0.03`. `research.text_similarity.enabled` defaults to
  `true` (the one deliberate non-no-op, per spec §5.2).
- **Run `uv run pytest` green and `uv run shortlist --demo` offline-clean before the final commit.**
- Work on a feature branch off `main`; do not commit to `main`.

---

### Task 1: `research/cachekey.py` — the pure key module

**Files:**
- Create: `src/shortlist/research/cachekey.py`
- Test: `tests/research/test_cachekey.py`

**Interfaces:**
- Consumes: nothing from earlier tasks. Reads the source of every module in
  `_PROMPT_MODULES` via `inspect`, plus the `research` config block.
- Produces:
  - `PROMPT_FINGERPRINT: str` — 8 hex chars, module-level constant.
  - `context_digest(card, macro=None, config=None) -> str` — 8 hex chars.
  - `brief_key(bundle, card, *, macro=None, config=None, today=None) -> str`.
  - `today` is a `datetime.date` injection point for tests.

- [ ] **Step 1: Write the failing test**

```python
# tests/research/test_cachekey.py
from datetime import date

import pytest

from shortlist.research import cachekey


class _M:
    """StockMetrics stub. Only the attributes cachekey reads."""
    def __init__(self, **kw):
        defaults = dict(price=100.0, market_cap=1e11, short_pct_outstanding=None,
                        days_to_cover=None, short_interest_rising=None,
                        revenue_cagr=0.1, roic=0.2, debt_to_equity=0.5,
                        filing_events=None, insider_recent=None, financial_series=None)
        defaults.update(kw)
        for k, v in defaults.items():
            setattr(self, k, v)


class _Card:
    def __init__(self, metrics=None, **kw):
        defaults = dict(quality=50.0, moat=50.0, growth=50.0, momentum=50.0, value=50.0,
                        insider=50.0, risk=50.0, composite=60.0, confidence=0.8,
                        gates=[], flags=[], sic_bucket="unknown")
        defaults.update(kw)
        for k, v in defaults.items():
            setattr(self, k, v)
        if metrics is not None:
            self.metrics = metrics


class _Bundle:
    def __init__(self, cache_key="acc10k+acc10q"):
        self.cache_key = cache_key
        self.primary_accession = "acc10k"


CFG = {"research": {"cache": {"max_age_days": 1, "price_band_pct": 0.03}}}
DAY = date(2026, 8, 12)


def _key(card, cfg=CFG, today=DAY, macro=None):
    return cachekey.brief_key(_Bundle(), card, macro=macro, config=cfg, today=today)


def test_fingerprint_is_8_hex_chars():
    assert len(cachekey.PROMPT_FINGERPRINT) == 8
    int(cachekey.PROMPT_FINGERPRINT, 16)          # raises if not hex


def test_identical_inputs_give_identical_key():
    assert _key(_Card(metrics=_M())) == _key(_Card(metrics=_M()))


def test_key_contains_the_filing_accessions():
    assert _key(_Card(metrics=_M())).startswith("acc10k+acc10q-")


def test_price_move_inside_band_does_not_change_key():
    # VERIFIED arithmetic at band=0.03: _band(100.0) == _band(100.5) == 155,
    # while _band(101.0) == 156. Bucket EDGES exist by construction, so this
    # asserts the property on a pair known to share a bucket - do NOT "fix" a
    # failure here by widening the band.
    assert _key(_Card(metrics=_M(price=100.0))) == _key(_Card(metrics=_M(price=100.5)))


def test_price_move_outside_band_changes_key():
    assert _key(_Card(metrics=_M(price=100.0))) != _key(_Card(metrics=_M(price=140.0)))


def test_new_gate_changes_key():
    assert _key(_Card(metrics=_M())) != _key(_Card(metrics=_M(), gates=["negative_fcf"]))


def test_new_flag_changes_key():
    assert _key(_Card(metrics=_M())) != _key(_Card(metrics=_M(), flags=["cash_burn"]))


def test_new_filing_event_changes_key():
    ev = [{"form": "8-K", "filed": "2026-08-11", "items": "2.02", "accession": "a", "url": None}]
    assert _key(_Card(metrics=_M())) != _key(_Card(metrics=_M(filing_events=ev)))


def test_filing_events_with_none_fields_do_not_raise():
    ev = [{"form": "8-K", "filed": None, "items": None},
          {"form": None, "filed": "2026-08-01", "items": "5.02"}]
    assert _key(_Card(metrics=_M(filing_events=ev)))      # sorts without TypeError


def test_extra_insider_trade_changes_key():
    one = [{"date": "2026-08-01", "name": "A", "role": "CEO", "kind": "buy", "value": 500000.0}]
    two = one + [{"date": "2026-08-02", "name": "B", "role": "CFO", "kind": "buy",
                  "value": 500000.0}]
    assert _key(_Card(metrics=_M(insider_recent=one))) != \
        _key(_Card(metrics=_M(insider_recent=two)))


def test_financial_series_change_changes_key():
    a = [{"fiscal_year": 2025, "revenue": 1.00e9, "free_cash_flow": 1.0e8}]
    b = [{"fiscal_year": 2025, "revenue": 1.50e9, "free_cash_flow": 1.0e8}]
    assert _key(_Card(metrics=_M(financial_series=a))) != \
        _key(_Card(metrics=_M(financial_series=b)))


def test_macro_regime_changes_key_and_none_is_safe():
    class _Macro:
        regime = "risk-off"
    assert _key(_Card(metrics=_M()), macro=None) != _key(_Card(metrics=_M()), macro=_Macro())


def test_card_without_metrics_does_not_raise():
    """Mirrors tests/research/test_enrich.py:6-15 — the stub card has no .metrics."""
    assert _key(_Card()) == _key(_Card())


def test_none_price_does_not_raise():
    assert _key(_Card(metrics=_M(price=None, market_cap=None)))


def test_zero_price_does_not_take_log():
    assert _key(_Card(metrics=_M(price=0.0)))     # log(0) would raise


def test_day_rollover_changes_key_when_max_age_is_one():
    c = _Card(metrics=_M())
    assert _key(c, today=date(2026, 8, 12)) != _key(c, today=date(2026, 8, 13))


def test_max_age_zero_disables_the_day_bucket():
    cfg = {"research": {"cache": {"max_age_days": 0}}}
    c = _Card(metrics=_M())
    assert _key(c, cfg=cfg, today=date(2026, 8, 12)) == \
        _key(c, cfg=cfg, today=date(2026, 8, 13))


def test_absent_config_uses_documented_defaults():
    c = _Card(metrics=_M())
    # default max_age_days == 1 -> the day bucket is present, so a rollover changes the key
    assert _key(c, cfg={}, today=date(2026, 8, 12)) != _key(c, cfg={}, today=date(2026, 8, 13))


def test_nan_and_inf_metrics_do_not_raise():
    """math.floor(inf) and round(nan) both raise; _num must reject them before
    they reach the live /deep path."""
    inf, nan = float("inf"), float("nan")
    assert _key(_Card(metrics=_M(price=inf, market_cap=nan, roic=nan)))
    trades = [{"value": nan}, {"value": inf}]
    assert _key(_Card(metrics=_M(insider_recent=trades)))


def test_valuation_field_change_changes_key():
    """pe_ttm renders into the prompt's Valuation line, so it must move the key."""
    assert _key(_Card(metrics=_M(pe_ttm=20.0))) != _key(_Card(metrics=_M(pe_ttm=35.0)))


def test_series_column_beyond_revenue_changes_key():
    """The prompt renders every series column, not just revenue/FCF."""
    a = [{"fiscal_year": 2025, "revenue": 1e9, "diluted_shares": 1.00e8}]
    b = [{"fiscal_year": 2025, "revenue": 1e9, "diluted_shares": 1.50e8}]
    assert _key(_Card(metrics=_M(financial_series=a))) != \
        _key(_Card(metrics=_M(financial_series=b)))


def test_research_config_change_changes_key():
    """max_chars/model/max_risks shape the prompt from YAML, not from source."""
    c = _Card(metrics=_M())
    cfg_a = {"research": {"cache": {"max_age_days": 0}, "max_chars": {"mda": 60000}}}
    cfg_b = {"research": {"cache": {"max_age_days": 0}, "max_chars": {"mda": 10000}}}
    assert _key(c, cfg=cfg_a) != _key(c, cfg=cfg_b)


def test_output_root_change_does_not_change_key():
    """output_root is a path, not prompt content."""
    c = _Card(metrics=_M())
    cfg_a = {"research": {"cache": {"max_age_days": 0}, "output_root": "research"}}
    cfg_b = {"research": {"cache": {"max_age_days": 0}, "output_root": "/tmp/x"}}
    assert _key(c, cfg=cfg_a) == _key(c, cfg=cfg_b)


def test_explicit_zero_price_band_is_honoured():
    """0 is falsy: `_num(v) or DEFAULT` would silently restore 0.03."""
    cfg = {"research": {"cache": {"max_age_days": 0, "price_band_pct": 0}}}
    c1, c2 = _Card(metrics=_M(price=100.0)), _Card(metrics=_M(price=100.5))
    assert _key(c1, cfg=cfg) == _key(c2, cfg=cfg)      # band<=0 -> price drops out


def test_prompt_fingerprint_covers_more_than_assess():
    """The fingerprint must span every prompt-shaping module (SCHEMA_HINT lives
    in models.py; the aux context lines live in their own modules)."""
    assert set(cachekey._PROMPT_MODULES) >= {
        "assess", "models", "reverse_dcf", "coverage_caveat", "proxy",
        "gov_contracts", "lobbying", "earnings", "riskdiff"}


def test_fingerprint_fallback_when_source_unavailable(monkeypatch):
    def _boom(_obj):
        raise OSError("source not available")
    monkeypatch.setattr(cachekey.inspect, "getsource", _boom)
    assert cachekey._prompt_fingerprint() == cachekey._FINGERPRINT_FALLBACK
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/research/test_cachekey.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shortlist.research.cachekey'`

- [ ] **Step 3: Write the implementation**

```python
# src/shortlist/research/cachekey.py
"""The on-disk key for one research brief.

WHY THIS EXISTS. The key used to be the filing accessions alone
(`filings.py:fetch_bundle`), so a brief only went stale when a new 10-K/10-Q
landed — even though the prompt also carries price, valuation, macro regime,
insider Form-4s, short interest and recent 8-K events, and even though editing
the prompt itself invalidated nothing.

THREE PARTS, each guarding a different failure mode:

1. `PROMPT_FINGERPRINT` — a hash of the SOURCE of every module that shapes the
   prompt or the guards, plus the `research` config block. Two narrower designs
   were rejected: hashing `_build_user_prompt`/`apply_guards` misses their
   callees (`inspect.getsource` does not follow calls), and hashing `assess.py`
   alone misses `SCHEMA_HINT` (models.py, concatenated at assess.py:87) and
   every context-line renderer in its own module. Config is in the hash because
   `research.max_chars`, `research.model` and the `max_*` caps shape the prompt
   from YAML, not from source.

2. `context_digest` — a hash of a BUCKETED materiality tuple off the ScoreCard.
   Bucketing is the point: hashing a raw price would miss the cache on every
   tick. The completeness rule is that everything `_quant_context` renders from
   the card belongs here; the three auxiliary lines are hashed as their RENDERED
   strings so their internals are covered by construction.

3. a day bucket — `research.cache.max_age_days` (0 disables it).

None-safety is not decorative: every StockMetrics field is Optional, and the
cards in `tests/research/test_enrich.py` are duck-typed stubs with no `.metrics`
at all. Every read below is getattr-guarded, and every numeric coercion rejects
NaN/inf — `math.floor(inf)` and `round(nan)` both raise, and this module runs on
the live `/deep` path where one raised exception would abort the whole batch.
"""
from __future__ import annotations

import hashlib
import inspect
import math
from datetime import date, datetime, timezone
from typing import Any, Optional

_FINGERPRINT_FALLBACK = "00000000"

_DEFAULT_MAX_AGE_DAYS = 1
_DEFAULT_PRICE_BAND = 0.03

# Sub-scores print to the prompt as integers, so bucket to 5 points: a 1-point
# wobble cannot change what the model reads.
_SCORE_STEP = 5.0
_CONFIDENCE_STEP = 0.05

# Every module whose source shapes the prompt or the deterministic guards.
# ADD TO THIS LIST when a new context-line renderer gets its own module.
_PROMPT_MODULES = ("assess", "models", "reverse_dcf", "coverage_caveat", "proxy",
                   "gov_contracts", "lobbying", "earnings", "riskdiff")

# Excluded from the config hash: output_root is a filesystem path, not prompt
# content; cache's own values already move the key mechanically.
_CONFIG_SKIP = ("output_root", "cache")


def _sha8(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:8]


def _module_sources() -> str:
    import importlib
    out = []
    for name in _PROMPT_MODULES:
        mod = importlib.import_module(f".{name}", __package__)
        out.append(inspect.getsource(mod))
    return "\n".join(out)


def _prompt_fingerprint() -> str:
    """8 hex chars over the prompt-shaping module SOURCES. Config is folded in
    per call by brief_key(), since config is a runtime argument. Falls back to a
    constant when source is unavailable (frozen/zipped install): the context and
    day buckets still work, only prompt self-invalidation is lost."""
    try:
        return _sha8(_module_sources())
    except Exception:
        return _FINGERPRINT_FALLBACK


def _config_fingerprint(config: Optional[dict]) -> str:
    """Canonical repr of the `research` config block, minus the keys that do not
    shape the prompt. Sorted so dict insertion order cannot move the key."""
    block = (config or {}).get("research") or {}
    items = sorted((k, repr(v)) for k, v in block.items() if k not in _CONFIG_SKIP)
    return repr(items)


# Source-only fingerprint, computed once. brief_key() folds the config in per
# call, since config is a runtime argument.
PROMPT_FINGERPRINT = _prompt_fingerprint()


def _cache_cfg(config: Optional[dict]) -> dict:
    return ((config or {}).get("research") or {}).get("cache") or {}


def _num(v: Any) -> Optional[float]:
    """float(v) or None. Rejects bools, unparseable values, and NaN/inf — the
    latter two would make math.floor / round raise downstream."""
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


def _band(v: Any, band: float) -> Optional[int]:
    """Log bucket: values within `band` (a fraction, e.g. 0.03) of each other
    usually share a bucket. Bucket EDGES exist by construction — 100.0 and 101.0
    fall either side of one at band=0.03 (155 vs 156). That is expected: the
    guarantee is "a small move usually does not regenerate", not "never"."""
    x = _num(v)
    if x is None or x <= 0 or band <= 0:
        return None
    return math.floor(math.log(x) / math.log(1.0 + band))


def _step(v: Any, step: float) -> Optional[float]:
    x = _num(v)
    return None if x is None else round(round(x / step) * step, 6)


def _sig3(v: Any) -> Optional[float]:
    """Round to 3 significant figures, so a material move busts the key while
    float noise does not."""
    x = _num(v)
    if x is None:
        return None
    if x == 0:
        return 0.0
    return round(x, 2 - int(math.floor(math.log10(abs(x)))))


def _s(v: Any) -> str:
    """Sort-safe string for a possibly-None field (None and str never compare)."""
    return "" if v is None else str(v)


def _aux_lines(m, config: Optional[dict]) -> list[str]:
    """The gov-contract / lobbying / earnings context lines, RENDERED. Hashing
    the rendered string (rather than picked fields) means a future field added
    inside one of these lines is covered automatically. All three are pure and
    network-free. Any failure degrades to "" — never raises."""
    rcfg = (config or {}).get("research") or {}
    out = []
    try:
        from . import earnings as earnings_ctx
        from . import gov_contracts as gov_contracts_ctx
        from . import lobbying as lobbying_ctx
        for mod, key in ((gov_contracts_ctx, "gov_contracts"),
                         (lobbying_ctx, "lobbying"),
                         (earnings_ctx, "earnings")):
            try:
                out.append(_s(mod.context_line(m, rcfg.get(key))))
            except Exception:
                out.append("")
    except Exception:
        return []
    return out


def context_digest(card, macro=None, config: Optional[dict] = None) -> str:
    """8 hex chars over the bucketed materiality tuple. Deliberately EXCLUDES
    DEF 14A proxy facts: they are fetched inside `assess()` (assess.py:594-598),
    so hashing them would force a network call on every cache check. Proxy data
    moves annually; the day bucket covers it."""
    raw_band = _num(_cache_cfg(config).get("price_band_pct"))
    band = _DEFAULT_PRICE_BAND if raw_band is None else raw_band
    m = getattr(card, "metrics", None)
    parts: list[Any] = []

    for name in ("quality", "moat", "growth", "momentum", "value", "insider",
                 "risk", "composite"):
        parts.append(_step(getattr(card, name, None), _SCORE_STEP))
    parts.append(_step(getattr(card, "confidence", None), _CONFIDENCE_STEP))
    parts.append(sorted(_s(g) for g in (getattr(card, "gates", None) or [])))
    parts.append(sorted(_s(f) for f in (getattr(card, "flags", None) or [])))
    parts.append(_s(getattr(card, "sic_bucket", None)))

    parts.append(_band(getattr(m, "price", None), band))
    parts.append(_band(getattr(m, "market_cap", None), band))
    parts.append(_step(getattr(m, "short_pct_outstanding", None), 0.001))
    parts.append(_step(getattr(m, "days_to_cover", None), 0.5))
    parts.append(_s(getattr(m, "short_interest_rising", None)))

    # The full Fundamentals + Valuation lines (assess.py:424-441).
    for name in ("revenue_cagr", "fcf_cagr", "eps_cagr", "revenue_growth_persistence",
                 "gross_margin", "net_margin", "roic", "debt_to_equity",
                 "interest_coverage", "pe_ttm", "pe_median_5y", "fcf_yield", "peg"):
        parts.append(_sig3(getattr(m, name, None)))

    events = getattr(m, "filing_events", None) or []
    parts.append(sorted((_s(e.get("form")), _s(e.get("items")), _s(e.get("filed")))
                        for e in events))

    trades = getattr(m, "insider_recent", None) or []
    net = sum(_num(t.get("value")) or 0.0 for t in trades)
    parts.append((len(trades), round(net / 1e5)))

    # Every column _render_series prints (assess.py:349-366), not just revenue/FCF.
    series = getattr(m, "financial_series", None) or []
    cols = ("revenue", "gross_profit", "net_income", "operating_cash_flow",
            "free_cash_flow", "total_debt", "diluted_eps", "diluted_shares")
    parts.append([(_s(r.get("fiscal_year")), _s(r.get("period_end")),
                   *[_sig3(r.get(c)) for c in cols]) for r in series])

    parts.extend(_aux_lines(m, config))
    parts.append(_s(getattr(macro, "regime", None)) or "none")
    return _sha8(repr(parts))


def brief_key(bundle, card, *, macro=None, config: Optional[dict] = None,
              today: Optional[date] = None) -> str:
    """The on-disk key for one brief: filing accessions + prompt fingerprint +
    context digest + day bucket.

    MUST be computed BEFORE the `is_cached` short-circuit in
    `research/__init__.py`. Computing it only before `report.write` would leave
    the pre-LLM skip keyed on the narrow accession key, so nothing would ever
    regenerate and every legacy brief would read as current forever."""
    cfg = _cache_cfg(config)
    raw_age = _num(cfg.get("max_age_days"))
    max_age = _DEFAULT_MAX_AGE_DAYS if raw_age is None else int(raw_age)
    base = (getattr(bundle, "cache_key", "") or
            getattr(bundle, "primary_accession", "") or "unknown")
    prompt8 = _sha8(PROMPT_FINGERPRINT + _config_fingerprint(config))
    key = f"{base}-p{prompt8}-c{context_digest(card, macro, config)}"
    if max_age > 0:
        day = today or datetime.now(timezone.utc).date()
        # Guard the ordinal arithmetic: a silly max_age (or a bad clock) must not
        # raise ValueError out of a function on the live /deep path.
        try:
            bucket = date.fromordinal((day.toordinal() // max_age) * max_age).isoformat()
        except (ValueError, OverflowError):
            bucket = day.isoformat()
        key = f"{key}-{bucket}"
    return key
```


- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/research/test_cachekey.py -v`
Expected: PASS (26 tests)

If `test_price_move_inside_band_does_not_change_key` fails, do **not** widen the band to force
it — recompute the buckets (`math.floor(math.log(x)/math.log(1.03))`) and pick a pair that
genuinely shares one. Bucket edges exist by construction and are not a bug.

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/research/cachekey.py tests/research/test_cachekey.py
git commit -m "feat(research): context-aware brief cache key module"
```

---

### Task 2: Wire the wide key into `_enrich_card`

**Files:**
- Modify: `src/shortlist/research/__init__.py:53-69`
- Modify: `config.yaml` (add `research.cache`)
- Modify: `src/shortlist/screen.py:214-216` (pass `macro` through — see Step 3)
- Test: `tests/research/test_enrich.py` (extend + update)
- **Verified NOT to need changes:** `tests/research/test_report.py:36,80,92`,
  `tests/research/test_models.py:161,187` — they exercise `report.py`/`models.py` directly with
  hand-supplied keys, and this task modifies neither module.

**Interfaces:**
- Consumes: `cachekey.brief_key(bundle, card, macro=..., config=..., today=...)` from Task 1.
- Produces: `_enrich_card` now writes briefs under the wide key; `assessment.cache_key` holds it.
  `FilingBundle.cache_key` keeps its narrow `+`-joined meaning (still pinned by the live test
  `tests/research/test_filings_integration.py:20`).

- [ ] **Step 1: Write the failing test**

```python
# tests/research/test_enrich.py — ADD these two tests
def test_enrich_ignores_a_brief_cached_under_the_narrow_key(tmp_path):
    """THE INVARIANT. A brief written under the old accession-only key must NOT
    short-circuit the LLM call. This is the test that fails if the wide key is
    computed after assess() instead of before the is_cached check."""
    from shortlist.research import report
    # max_age_days: 0 removes the day bucket so the test cannot flake across a
    # UTC midnight boundary (enrich() has no `today` injection point).
    cfg = {"research": {"output_root": str(tmp_path), "cache": {"max_age_days": 0}}}
    report.write(_assessment("A", key="acc-A"), tmp_path)      # legacy narrow key
    calls = {"n": 0}
    def fake_assess(card, bundle, config, **kw):
        calls["n"] += 1
        return _assessment(card.ticker)
    enrich([_Card("A", 90)], cfg, top_n=1, fetch=lambda t, **k: _bundle(t),
           assess_fn=fake_assess)
    assert calls["n"] == 1


def test_enrich_regenerates_when_context_changes(tmp_path):
    """Same filings, materially different card -> the cached brief must not be reused."""
    from shortlist.research import cachekey, report
    cfg = {"research": {"output_root": str(tmp_path), "cache": {"max_age_days": 0}}}
    card = _Card("A", 90)
    bundle = _bundle("A")
    key = cachekey.brief_key(bundle, card, config=cfg)
    report.write(_assessment("A", key=key), tmp_path)
    calls = {"n": 0}
    def fake_assess(card, bundle, config, **kw):
        calls["n"] += 1
        return _assessment(card.ticker)
    fetch = lambda t, **k: bundle
    enrich([card], cfg, top_n=1, fetch=fetch, assess_fn=fake_assess)
    assert calls["n"] == 0                      # unchanged card -> cache hit
    enrich([_Card("A", 90, gates=["negative_fcf"])], cfg, top_n=1, fetch=fetch,
           assess_fn=fake_assess, require_passed=False)
    assert calls["n"] == 1                      # a new gate -> regenerated
```

Then update the two existing cache tests to pre-seed under the wide key:

```python
# tests/research/test_enrich.py — REPLACE test_enrich_uses_cache_unless_refresh
def test_enrich_uses_cache_unless_refresh(tmp_path):
    from shortlist.research import cachekey, report
    cfg = {"research": {"output_root": str(tmp_path), "cache": {"max_age_days": 0}}}
    card, bundle = _Card("A", 90), _bundle("A", key="acc-A")
    key = cachekey.brief_key(bundle, card, config=cfg)
    report.write(_assessment("A", key=key), tmp_path)
    calls = {"n": 0}
    def fake_assess(card, bundle, config, **kw):
        calls["n"] += 1
        return _assessment(card.ticker)
    fetch = lambda t, **k: bundle
    r = enrich([card], cfg, top_n=1, refresh=False, fetch=fetch, assess_fn=fake_assess)
    assert calls["n"] == 0 and r[0].brief_path and r[0].from_cache is True
    enrich([card], cfg, top_n=1, refresh=True, fetch=fetch, assess_fn=fake_assess)
    assert calls["n"] == 1                     # refresh forces re-assessment
```

`test_enrich_new_10q_invalidates_cache` (line 72) needs the same treatment: build both bundles,
derive each key via `cachekey.brief_key`, pre-seed the first, assert the second regenerates.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/research/test_enrich.py -v`
Expected: `test_enrich_ignores_a_brief_cached_under_the_narrow_key` FAILS with `assert 0 == 1`
(the narrow key still short-circuits).

**Do not stop at "the suite is green."** `test_enrich_new_10q_invalidates_cache`
(`test_enrich.py:72`) will go **green-but-vacuous** after Task 2: it pre-seeds `acc-A+q1` and
fetches `acc-A+q2`, and under the wide key the pre-seeded file can never match *any* wide key —
so it passes for the wrong reason and no longer tests 10-Q invalidation at all. Rewrite it to
derive both keys through `cachekey.brief_key` even though it is not red.

- [ ] **Step 3: Write the implementation**

**Replace lines 53-67 only.** Lines 68-69 are the `except Exception as e: return
ResearchResult(..., skipped=f"research error: ...")` clause that closes the `try` opened at
line 61 — replacing through 69 would delete the `except` and leave a `SyntaxError`.

```python
# src/shortlist/research/__init__.py — replace lines 53-67 (leave 68-69 intact)
    if bundle is None:
        return ResearchResult(card.ticker, skipped=reason_fn(card.ticker))
    # The WIDE key must be computed here, BEFORE the is_cached short-circuit: this
    # is the check that saves the LLM call, and keying it on accessions alone is
    # what let a brief outlive its own inputs. See research/cachekey.py.
    #
    # Degrade, never raise: _enrich_card's contract (docstring, line 41) is that one
    # bad ticker never aborts the batch, and BOTH batch callers (screen.py:227-231,
    # research/phase.py:92) catch only at the batch level — an exception here would
    # return {} for every name in the run.
    try:
        key = cachekey.brief_key(bundle, card, macro=macro, config=config)
    except Exception:
        key = bundle.cache_key
    if not refresh and report.is_cached(card.ticker, key, root):
        bp = report.brief_path(card.ticker, key, root)
        return ResearchResult(card.ticker, brief_path=str(bp), from_cache=True)
    # cap_bundle / assess / report.write (filesystem I/O, prompt building, the LLM
    # call) can all raise; isolate them too so the docstring promise — one failure
    # never aborts the batch — holds for the whole pipeline, not just fetch().
    try:
        from .filings import cap_bundle
        bundle = cap_bundle(bundle, config.get("research", {}).get("max_chars"))
        assessment = assess_fn(card, bundle, config, macro=macro)
        if assessment is None:
            return ResearchResult(card.ticker, skipped="assessment failed")
        # assess() sets the narrow bundle key (assess.py:643); the brief is written
        # under the wide key so the two never diverge on disk.
        assessment.cache_key = key
        bp = report.write(assessment, root, config)
```

Add `cachekey` to the package import at line 9:

```python
from . import cachekey, claude_cli, report
```

**Also pass `macro` through on the CLI path.** `screen.py:216` calls
`enrich(cards, config, top_n=n, refresh=refresh)` with no `macro=`, while the bot passes the real
object (`research/phase.py:81-82`). Once `macro.regime` is in the digest, the same ticker on the
same day would get *different* keys from `shortlist --research` and from the bot's `/deep` —
duplicate briefs, duplicate spend. (The dropped macro is pre-existing: CLI briefs have never
carried the macro line.) Thread `macro` into `_run_research_phase`/`_run_enrich` and pass
`macro=macro` at the `enrich(...)` call.

Add to `config.yaml`, inside the `research:` block next to `output_root`:

```yaml
  cache:                       # brief staleness (docs/PLAN_DEEP_FRESHNESS.md §4)
    max_age_days: 1            # day bucket; 0 = content-addressed only, no time decay
    price_band_pct: 0.03       # regenerate when price/market cap moves ~3%
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/research/ -v`
Expected: PASS. Baseline before this task is **175 passed, 1 skipped**;
`test_report.py:36,80,92` and `test_models.py:161,187` pass untouched because they pin
`FilingBundle.cache_key` and `report.write`'s own fallback, neither of which this task changes.
**If any of them fail, do not loosen the assertion** — re-read the failure: `report.write` must
still key off whatever string it is handed.

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/research/__init__.py config.yaml tests/research/test_enrich.py
git commit -m "feat(research): key briefs on prompt + context + day, not filings alone"
```

---

### Task 3: Prior-year MD&A + the Lazy-Prices similarity on the bundle

**Files:**
- Modify: `src/shortlist/research/filings.py:83-105` (`_prior_year_risk_factors` → `_prior_year_sections`), `:234-261` (`fetch_bundle`)
- Modify: `src/shortlist/research/models.py:85-104` (`FilingBundle`)
- Test: `tests/research/test_filings.py`, `tests/research/test_models.py`

**Interfaces:**
- Consumes: `textsim.combined_similarity(cur_risk, prior_risk, cur_mda, prior_mda) -> Optional[float]`
  (existing, `research/textsim.py:66`).
- Produces: `FilingBundle.text_similarity: Optional[float]` — `None` when disabled or
  uncomputable. **Not** part of `haystack()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/research/test_models.py — ADD
def test_text_similarity_is_not_in_the_haystack():
    """A computed number must never be quotable as a filing fact."""
    from shortlist.research.models import FilingBundle, FilingText
    tenk = FilingText("A", "acc", "2026-01-01", business="b", mda="m", risk_factors="r")
    b = FilingBundle(tenk=tenk, primary_accession="acc", cache_key="acc",
                     filing_date="2026-01-01", text_similarity=0.42)
    assert b.text_similarity == 0.42
    # Assert on the source of truth, not on one formatting of the number: the
    # haystack is exactly the filing texts, nothing else.
    assert b.haystack() == "b\n\nm\n\nr"


# tests/research/test_filings.py — ADD
class _PriorTenK:
    risk_factors = "prior risk text"
    management_discussion = "prior mda text"


class _FakeFiling:
    form = "10-K"
    def __init__(self, filing_date, period):
        self.filing_date, self.period_of_report = filing_date, period
    def obj(self):
        return _PriorTenK()


def _fake_company(rows):
    class _Company:
        def __init__(self, ticker):
            self.ticker = ticker
        def get_filings(self, form):
            return rows
    return _Company


def test_prior_year_sections_returns_risk_and_mda_from_one_filing():
    """Both baselines come from ONE parsed filing - that is what makes the
    similarity free (no second network fetch)."""
    from shortlist.research.filings import _prior_year_sections
    rows = [_FakeFiling("2026-02-01", "2025-12-31"), _FakeFiling("2025-02-01", "2024-12-31")]
    risk, mda = _prior_year_sections("A", company_factory=_fake_company(rows))
    assert risk == "prior risk text"
    assert mda == "prior mda text"


def test_prior_year_sections_empty_without_a_prior_year():
    from shortlist.research.filings import _prior_year_sections
    rows = [_FakeFiling("2026-02-01", "2025-12-31")]          # only one 10-K
    assert _prior_year_sections("A", company_factory=_fake_company(rows)) == ("", "")


def test_prior_year_sections_never_raises():
    from shortlist.research.filings import _prior_year_sections
    def _boom(_ticker):
        raise RuntimeError("edgar exploded")
    assert _prior_year_sections("A", company_factory=_boom) == ("", "")
```

```python
# tests/research/test_filings.py - ADD
def test_similarity_enabled_defaults_on_and_honours_false():
    """enabled: false -> no similarity computed (the byte-identical escape hatch)."""
    from shortlist.research import filings
    cfg = {"research": {"text_similarity": {"enabled": False}}}
    assert filings._similarity_enabled(cfg) is False
    assert filings._similarity_enabled({}) is True          # default ON
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/research/test_models.py tests/research/test_filings.py -v`
Expected: FAIL — `FilingBundle.__init__() got an unexpected keyword argument 'text_similarity'`
and `AttributeError: module 'shortlist.research.filings' has no attribute '_similarity_enabled'`

- [ ] **Step 3: Write the implementation**

```python
# src/shortlist/research/models.py — FilingBundle: add the field and extend the docstring
    tenq_mda: str = ""
    added_risks_text: str = ""
    text_similarity: Optional[float] = None
    # ^ Lazy-Prices YoY cosine (Item 1A + MD&A) vs the prior-year 10-K, computed in
    # fetch_bundle from documents already fetched for the risk diff. PROMPT-ONLY:
    # it is a computed number, not filing text, so it must never enter haystack()
    # or a model could quote it through quote-verification as a filing fact.
```

```python
# src/shortlist/research/filings.py — replace _prior_year_risk_factors
def _similarity_enabled(config: Optional[dict]) -> bool:
    """Lazy-Prices similarity ships ON. Note this is the one research key whose
    ABSENT block is not a no-op — a producer nobody switches on is exactly how
    this signal sat dead with a fully-built consumer (see TODO.md §2a)."""
    block = ((config or {}).get("research") or {}).get("text_similarity") or {}
    return bool(block.get("enabled", True))


def _prior_year_sections(ticker: str, company_factory=None) -> tuple[str, str]:
    """(risk_factors, mda) from the prior fiscal year's 10-K — the diff baseline
    AND the Lazy-Prices baseline, taken from ONE already-parsed filing object so
    the similarity costs no extra network request. Excludes 10-K/A amendments and
    selects by fiscal year (not 'second most recent'). ("", "") if there is no
    genuinely-prior annual report. Never raises.

    `company_factory` exists ONLY so tests can inject a fake without patching
    `sys.modules`; production always takes the lazy `edgar` import below (the
    [edgar] extra is optional, so it must not be imported at module scope).

    BEHAVIOUR CHANGE vs `_prior_year_risk_factors`: the `edgar` import now sits
    INSIDE the try, so a missing [edgar] extra degrades to ("", "") + a stderr
    line instead of raising ImportError. That is unreachable in practice —
    `fetch_10k` runs first in `fetch_bundle` and imports `edgar` at its top — and
    it matches this function's documented never-raises contract."""
    try:
        if company_factory is None:
            from edgar import Company
            company_factory = Company
        filings = company_factory(ticker).get_filings(form="10-K")
        rows = [f for f in filings if str(getattr(f, "form", "")) == "10-K"]
        if len(rows) < 2:
            return "", ""
        rows.sort(key=lambda f: str(getattr(f, "filing_date", "")), reverse=True)
        current_fy = _fiscal_year(rows[0])
        for f in rows[1:]:
            fy = _fiscal_year(f)
            if current_fy is None or fy is None or fy < current_fy:
                tenk = f.obj()
                return _section(tenk, "risk_factors"), _section(tenk, "management_discussion")
        return "", ""
    except Exception as e:
        log_abstain("prior-year 10-K fetch failed", ticker, e)
        return "", ""
```

```python
# src/shortlist/research/filings.py — in fetch_bundle, replace the prior_1a block
    prior_1a, prior_mda = _prior_year_sections(ticker)
    added = riskdiff.added_risk_blocks(tenk.risk_factors, prior_1a, config or {})
    # Lazy-Prices YoY similarity from documents ALREADY in hand — no extra fetch.
    similarity = None
    if _similarity_enabled(config):
        similarity = textsim.combined_similarity(
            tenk.risk_factors, prior_1a, tenk.mda, prior_mda)

    cache_key = f"{tenk.accession}+{tenq_acc}" if tenq_acc else tenk.accession
    return FilingBundle(
        tenk=tenk, primary_accession=tenk.accession, cache_key=cache_key,
        filing_date=tenk.filing_date, tenq_mda=tenq_mda, added_risks_text=added,
        text_similarity=similarity)
```

Also fix the stale comment at `filings.py:71` — `markdown=True` is **not** honoured on
edgartools 5.33.0's primary path (`ten_q.py:411-429` returns `Section.text()` and never reads
the argument). Replace "markdown=True for 10-K parity" with:

```python
        # markdown=True is passed for the legacy-fallback path only; on the current
        # parser path get_item_with_part returns Section.text() and ignores it.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/research/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/research/filings.py src/shortlist/research/models.py \
        tests/research/test_filings.py tests/research/test_models.py
git commit -m "feat(research): compute Lazy-Prices YoY similarity from the prior-year 10-K"
```

---

### Task 4: Surface the similarity in the prompt and the brief

**Files:**
- Modify: `src/shortlist/research/assess.py` (new `_similarity_line`, call site in `_build_user_prompt`, assignment near `:643`)
- Modify: `src/shortlist/research/models.py` (`QualitativeAssessment.text_similarity`)
- Modify: `src/shortlist/research/report.py:117-123`
- Test: `tests/research/test_assess.py`, `tests/research/test_report.py`

**Interfaces:**
- Consumes: `FilingBundle.text_similarity` from Task 3.
- Produces: `QualitativeAssessment.text_similarity: Optional[float]`; a prompt line;
  a `## Filing-text change` line in the rendered brief.

- [ ] **Step 1: Write the failing test**

```python
# tests/research/test_assess.py — ADD
def test_similarity_line_renders_and_stays_out_of_the_haystack():
    from shortlist.research.assess import _similarity_line
    line = _similarity_line(0.62)
    assert "38%" in line                     # 1 - 0.62, rendered as percent rewritten
    assert "0.62" in line
    assert "context only" in line.lower()


def test_similarity_line_absent_when_none():
    from shortlist.research.assess import _similarity_line
    assert _similarity_line(None) == ""


def test_prompt_is_byte_identical_when_similarity_is_none():
    """spec §7: disabled / uncomputable similarity must not perturb the prompt."""
    from shortlist.research.assess import _build_user_prompt
    from shortlist.research.models import FilingBundle, FilingText
    tenk = FilingText("A", "acc", "2026-01-01", business="b", mda="m", risk_factors="r")
    base = FilingBundle(tenk=tenk, primary_accession="acc", cache_key="acc",
                        filing_date="2026-01-01")
    withnone = FilingBundle(tenk=tenk, primary_accession="acc", cache_key="acc",
                            filing_date="2026-01-01", text_similarity=None)
    cfg = {"research": {}}
    assert _build_user_prompt(base, cfg) == _build_user_prompt(withnone, cfg)


# tests/research/test_report.py — ADD
def test_brief_renders_the_similarity_line():
    from shortlist.research.models import Moat, QualitativeAssessment, Thesis
    from shortlist.research.report import to_markdown
    a = QualitativeAssessment(
        ticker="A", as_of="t", filing_accession="acc", filing_date="2026-01-01",
        model="m", cost_usd=0.0, moat=Moat(), thesis=Thesis(takeaway="t"),
        text_similarity=0.62)
    md = to_markdown(a)
    assert "Filing-text change" in md and "38%" in md


def test_brief_omits_the_similarity_line_when_none():
    from shortlist.research.models import Moat, QualitativeAssessment, Thesis
    from shortlist.research.report import to_markdown
    a = QualitativeAssessment(
        ticker="A", as_of="t", filing_accession="acc", filing_date="2026-01-01",
        model="m", cost_usd=0.0, moat=Moat(), thesis=Thesis(takeaway="t"))
    assert "Filing-text change" not in to_markdown(a)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/research/test_assess.py tests/research/test_report.py -v`
Expected: FAIL — `ImportError: cannot import name '_similarity_line'`

- [ ] **Step 3: Write the implementation**

```python
# src/shortlist/research/assess.py — add near _macro_line
def _similarity_line(similarity: Optional[float]) -> str:
    """One prompt context line for the Lazy-Prices YoY text change, or "" to omit.
    PROMPT-ONLY — never the grounding haystack: this is a computed cosine, not
    filing text, and must not survive quote-verification as a filing fact."""
    if similarity is None:
        return ""
    rewritten = max(0.0, min(1.0, 1.0 - float(similarity)))
    return ("\n\nFiling-text change vs the prior-year 10-K (context only — computed, "
            f"NOT filing text): risk-factor + MD&A language is {rewritten * 100:.0f}% "
            f"rewritten (cosine {similarity:.2f}). Cohen-Malloy-Nguyen (2020) associate "
            "large year-over-year rewrites with weaker forward returns; treat it as a "
            "prompt to look for WHAT changed, not as a verdict.")
```

Call it in `_build_user_prompt`, appended after `macro_section`:

```python
    macro_section = _macro_line(macro, rcfg.get("macro"))
    similarity_section = _similarity_line(getattr(bundle, "text_similarity", None))
```

and add `f"{similarity_section}"` as the final line of the returned f-string, after
`f"{macro_section}"`.

Carry it onto the assessment next to the cache key (`assess.py:643`):

```python
                assessment.cache_key = bundle.cache_key
                assessment.text_similarity = getattr(bundle, "text_similarity", None)
```

```python
# src/shortlist/research/models.py — QualitativeAssessment, next to cache_key
    text_similarity: Optional[float] = None   # Lazy-Prices YoY cosine; None == not computed
```

```python
# src/shortlist/research/report.py — in to_markdown, after the Reconciliation block
    if a.text_similarity is not None:
        pct = max(0.0, min(1.0, 1.0 - a.text_similarity)) * 100
        lines += ["", "## Filing-text change (Lazy Prices)",
                  f"- Risk-factor + MD&A language is **{pct:.0f}% rewritten** vs the "
                  f"prior-year 10-K (cosine {a.text_similarity:.2f}). _Computed, not a "
                  "filing quote; advisory context only._"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/research/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/research/assess.py src/shortlist/research/models.py \
        src/shortlist/research/report.py tests/research/
git commit -m "feat(research): surface the Lazy-Prices YoY text change in /deep"
```

---

### Task 5: Docs, full-suite verification, and the producer-less-flag guard

**Files:**
- Modify: `TODO.md` (§2a), `CLAUDE.md`, `docs/RESEARCH.md:80-81`, `config.yaml:383-384,418`,
  `src/shortlist/research/report.py:24`, `src/shortlist/research/models.py:88-91,193`
- Create: `tests/test_flag_producers.py`

**Interfaces:**
- Consumes: nothing. Produces: a standing guard against the next producer-less feature.

- [ ] **Step 1: Write the guard**

A naive `f"{field}=" in source` grep does **not** work here, and the failure is silent in both
directions. Verified in this repo: `bot/report/viewmodel.py:157,158,167` contains `roic=`,
`debt_to_equity=`, `revenue_cagr=` as pure read-and-forward kwargs (false positives), while the
real production writer is `src/shortlist/data/bridge.py:170` — `m.roic = f.roic`, spaced, so
`grep -c "roic=" bridge.py` returns **0** (false negative). A guard that cannot see the repo's
dominant assignment style would stay red forever and never tell anyone to remove the xfail.
Use `ast` instead.

```python
# tests/test_flag_producers.py
"""Every declarative flag must have a producer for the field its rule reads.

WHY: `filing_text_change` shipped wired end-to-end through scoring, config, the
glossary and the bot theme with NOTHING ever setting `filing_text_similarity`
(TODO.md §2a). The flag could never fire. This guard makes the next one visible
at CI time rather than in a code review two months later.
"""
import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "shortlist"

# flag name -> the StockMetrics field its scoring rule reads
FLAG_INPUTS = {"filing_text_change": "filing_text_similarity"}

# Not producers: the dataclass declaration itself, the offline demo factory, and
# the presentation layer (which only forwards values it was handed).
EXCLUDED = ("models.py", "providers/mock.py", "bot/")


def _is_excluded(path: Path) -> bool:
    rel = path.relative_to(SRC).as_posix()
    return any(rel.endswith(e) or rel.startswith(e) or f"/{e}" in rel for e in EXCLUDED)


def _writes_field(tree: ast.AST, field: str) -> bool:
    """True if this module ASSIGNS `<something>.field` or passes `field=` as a
    keyword. Covers `m.roic = x` (bridge.py's dominant style), `m.roic: float = x`
    and `StockMetrics(roic=x)` alike."""
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for tgt in targets:
            if isinstance(tgt, ast.Attribute) and tgt.attr == field:
                return True
        if isinstance(node, ast.keyword) and node.arg == field:
            return True
    return False


def _producers(field: str) -> list[str]:
    out = []
    for path in SRC.rglob("*.py"):
        if _is_excluded(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                      # pragma: no cover
            continue
        if _writes_field(tree, field):
            out.append(path.relative_to(SRC).as_posix())
    return out


def test_ast_producer_detection_sees_the_repo_dominant_style():
    """Self-test the detector against a field with a KNOWN producer, so a broken
    detector cannot make the guard below look meaningful."""
    assert "data/bridge.py" in _producers("roic")


@pytest.mark.xfail(
    strict=True,
    reason="filing_text_change has no producer on the screen path: the similarity is "
           "computed in the research layer, which runs AFTER check_flags "
           "(scoring.py:809 inside score(), vs screen.py:188 then :193). Tracked in "
           "TODO.md §2a. When a collection-time producer ships, this test XPASSes, "
           "strict=True turns that into a failure, and whoever added it deletes "
           "this decorator.")
def test_declared_flag_inputs_have_a_writer():
    for flag, field in FLAG_INPUTS.items():
        assert _producers(field), (
            f"flag {flag!r} reads {field!r}, but nothing in src/ ever assigns it — "
            "the flag can never fire (TODO.md §2a)")
```

- [ ] **Step 2: Run it and confirm BOTH halves behave**

Run: `uv run pytest tests/test_flag_producers.py -v`
Expected: `test_ast_producer_detection_sees_the_repo_dominant_style` **PASSES** (the detector
works) and `test_declared_flag_inputs_have_a_writer` reports **XFAIL** (the true state of the
repo). Neither is a failure. If the second reports XPASS instead, someone has added a producer —
delete the decorator rather than the test.

- [ ] **Step 3: Update every doc the key change falsifies**

The old key shape is stated as fact in several places; leaving them is how the next reader
concludes the cache is accession-keyed and reasons from it.

- `TODO.md` §2a — delete the cache-key bullet (shipped). Add a short entry recording: (a)
  `filing_text_change` has no screen-path producer and `/deep` now computes/displays the
  similarity instead; (b) the 10-Q arm additionally needs Part II Item 1A because `TenQ` has no
  `risk_factors` property (`filings.py:112` reads it and always gets `""`).
- `CLAUDE.md`, research-layer section — one sentence that the brief cache key is
  prompt-, config- and context-aware (`research/cachekey.py`), one that the Lazy-Prices
  similarity is a `/deep` display line and **not** a live flag.
- `docs/RESEARCH.md:80-81` — "cached by filing accession" is now false.
- `config.yaml:383-384` ("brief reproducibility is anchored by accession-keyed caching") and
  `config.yaml:418` (`artifacts: research/<TICKER>/<accession>.{md,json}`).
- `src/shortlist/research/report.py:24` — `is_cached` docstring says "keyed by accession, not
  date", which becomes literally false.
- `src/shortlist/research/models.py:88-91` (`FilingBundle`: "`cache_key` keys the brief on
  disk" — it is now the *filing* half of the key) and `:193` ("composite filing key").
- Check, and correct if stale: `docs/ASSESSMENT_GAPS.md:378,435,461`,
  `docs/PLAN_EDGAR_DILUTED_SHARES.md:125` ("existing briefs will NOT regenerate" — they will),
  `docs/PLAN_EDGAR_ROOT_CAUSE_B.md:145`.

- [ ] **Step 4: Full verification**

```bash
uv run pytest
uv run shortlist --demo
git status --short
```

Expected: suite green (with the one documented XFAIL); `--demo` prints a ranked table with no
network call; `git status` shows only intended files — in particular **nothing written under
`state/`**, which the deploy smoke test depends on.

- [ ] **Step 5: Commit**

```bash
git add TODO.md CLAUDE.md docs/ config.yaml src/shortlist/research/report.py \
        src/shortlist/research/models.py tests/test_flag_producers.py
git commit -m "docs: record /deep freshness landing; guard against producer-less flags"
```

---


## Self-review

**Spec coverage:** §4.1 invariant → Task 2 Step 1 (`test_enrich_ignores_a_brief_cached_under_the_narrow_key`).
§4.2 fingerprint (module set + config) → Task 1, with `test_prompt_fingerprint_covers_more_than_assess`,
`test_research_config_change_changes_key` and `test_output_root_change_does_not_change_key`.
§4.2 digest table → Task 1 `context_digest`; every row is present, including the full
Fundamentals/Valuation scalars, all rendered series columns, `sic_bucket` and the three aux
context lines hashed as rendered strings. §4.2 proxy exclusion → `context_digest` docstring.
§4.3 wiring → Task 2 (inside a `try`, preserving the never-raises contract). §4.4 config →
Task 2 Step 3. §5.2 producer → Task 3. §5.2 surfacing → Task 4. §5.3 dormancy → Global
Constraints + Task 5 strict xfail. §5.5 follow-on → Task 5 Step 3 TODO entry. §6.4 no pruning →
not built, by design. §7 test plan → Tasks 1-5, including the byte-identical-prompt test added
to Task 4. 10-Q Part II remains out of scope per §5.5.

**Placeholders:** none — every code step carries runnable code, including the full xfail body
(a `...` body would XPASS and, under `strict=True`, turn the suite red). Task 3's edgartools
injection is resolved concretely via `company_factory=None`.

**Type consistency:** `brief_key(bundle, card, *, macro, config, today)` and
`context_digest(card, macro, config)` are used with those exact signatures in Tasks 1-2.
`text_similarity` is the field name on both `FilingBundle` (Task 3) and `QualitativeAssessment`
(Task 4); `_similarity_line`, `_similarity_enabled`, `_prior_year_sections` and `_PROMPT_MODULES`
are each defined once and referenced consistently.

**Arithmetic verified by hand** (stdlib `python3`, not assumed):
`_band(100.0)=155`, `_band(100.5)=155`, `_band(101.0)=156`, `_band(140.0)=167` at `band=0.03` —
so the in-band test uses 100.5, not 101.0. `date.fromordinal(0)` raises `ValueError`, hence the
guarded bucket arithmetic. `0.0 or 0.03 == 0.03`, hence the `is None` check on `price_band_pct`.
`math.floor(inf)` and `round(nan)` raise, hence `_num` rejects both.

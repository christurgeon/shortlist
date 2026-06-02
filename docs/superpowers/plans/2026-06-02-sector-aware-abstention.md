# Sector-Aware Applicability & Abstention — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the scorer silently averaging metrics that are structurally undefined for a company's sector; instead detect the sector from SEC SIC codes, abstain inapplicable legs/sub-scores explicitly, mask false-positive gates, and mark too-thin composites `not_scored` — identically on both the screener and `--engine harness` stacks.

**Architecture:** A new pure leaf `sectors.py` maps `SIC → bucket` and answers leg/gate applicability from `config.yaml`. SIC is sourced **only** from EDGAR on both stacks (screener `EdgarProvider`; harness `EdgarSource` via a partial `Profile(sic=…)`), so both engines feed one unchanged `score()` call that applies masking + bucket-gated abstention. `unknown` bucket = bit-identical to today.

**Tech Stack:** Python 3.11, dataclasses, `uv`, pytest, `edgartools` (optional `--extra edgar`).

**Reference spec:** `docs/superpowers/specs/2026-06-02-sector-aware-abstention-design.md`

**Conventions:** run tests with `uv run pytest`. Commit after every green step. Never read `m.sector` (free-text, divergent) for applicability — only `m.sic`.

---

## Task 1: `sectors.py` — the SIC resolver + applicability predicates

**Files:**
- Create: `src/shortlist/sectors.py`
- Test: `tests/test_sectors.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sectors.py
from shortlist.sectors import resolve_bucket, leg_applicable, gate_applicable, extract_sic

CFG = {
    "sectors": {
        "buckets": [
            {"name": "reit",     "sic_ranges": [[6798, 6798]]},
            {"name": "insurer",  "sic_ranges": [[6300, 6399], [6410, 6411]]},
            {"name": "financials","sic_ranges": [[6020, 6099], [6120, 6179],
                                                 [6199, 6199], [6211, 6211], [6712, 6712]]},
        ],
        "masked_legs":  ["gross_margin", "gross_margin_stability", "roic",
                         "fcf_yield", "fcf_cagr", "interest_coverage", "debt_to_equity"],
        "masked_gates": ["negative_fcf", "over_leveraged"],
    },
}

def test_resolve_bucket_boundaries():
    assert resolve_bucket("6211", CFG) == "financials"   # broker-dealer (SCHW)
    assert resolve_bucket(6022, CFG) == "financials"      # state bank, int input
    assert resolve_bucket("6798", CFG) == "reit"
    assert resolve_bucket("6311", CFG) == "insurer"       # life insurance
    assert resolve_bucket("6712", CFG) == "financials"    # bank holding co

def test_reit_not_swallowed_by_financials_ranges():
    # 6798 must resolve reit even though list order/other ranges exist
    assert resolve_bucket("6798", CFG) == "reit"

def test_exchanges_and_advisers_are_unknown():
    assert resolve_bucket("6231", CFG) == "unknown"       # security/commodity exchange (ICE-like)
    assert resolve_bucket("6282", CFG) == "unknown"       # investment advice / asset manager
    assert resolve_bucket("7372", CFG) == "unknown"       # prepackaged software

def test_resolve_bucket_unknown_and_junk():
    assert resolve_bucket(None, CFG) == "unknown"
    assert resolve_bucket("", CFG) == "unknown"
    assert resolve_bucket("None", CFG) == "unknown"
    assert resolve_bucket("abc", CFG) == "unknown"

def test_leg_applicable():
    assert leg_applicable("financials", "fcf_yield", CFG) is False
    assert leg_applicable("financials", "roe", CFG) is True
    assert leg_applicable("unknown", "fcf_yield", CFG) is True   # nothing masked when unknown
    assert leg_applicable("reit", "interest_coverage", CFG) is False

def test_gate_applicable():
    assert gate_applicable("financials", "over_leveraged", CFG) is False
    assert gate_applicable("financials", "below_min_mktcap", CFG) is True
    assert gate_applicable("unknown", "over_leveraged", CFG) is True

def test_extract_sic_normalizes():
    class C:  # duck-typed edgartools Company
        sic = 6211
    assert extract_sic(C()) == "6211"
    class C2:
        sic = "0006798"
    assert extract_sic(C2()) == "6798"
    class C3:
        @property
        def sic(self): raise RuntimeError("boom")
    assert extract_sic(C3()) is None      # never raises
    assert extract_sic(None) is None
```

- [ ] **Step 2: Run the tests, verify they fail**

Run: `uv run pytest tests/test_sectors.py -q`
Expected: FAIL (`ModuleNotFoundError: shortlist.sectors`).

- [ ] **Step 3: Implement `sectors.py`**

```python
# src/shortlist/sectors.py
"""Sector applicability: SIC -> bucket and per-leg/gate applicability.

Pure, dependency-free leaf (pattern: providers/_form4.py). The ONLY interpreter of
config['sectors']. Scoring reads applicability through here; it must never key off
the free-text StockMetrics.sector (source-dependent and divergent across stacks)."""
from __future__ import annotations

from typing import Optional


def extract_sic(company) -> Optional[str]:
    """Best-effort 4-digit SIC string off an edgartools Company. Swallows ALL
    exceptions and coerces missing/empty/'None'/non-numeric -> None, so a SIC
    lookup can never regress an otherwise-successful fetch and both stacks
    normalize identically."""
    if company is None:
        return None
    try:
        raw = getattr(company, "sic", None)
    except Exception:
        return None
    if raw is None:
        return None
    s = str(raw).strip().lstrip("0")    # tolerate zero-padded "0006798"
    return s if s.isdigit() else None   # rejects "", "None", "abc"


def resolve_bucket(sic, config: dict) -> str:
    """Map a SEC SIC (str|int|None) to a bucket name, or 'unknown'. First bucket
    whose inclusive ranges contain the SIC wins; buckets are an ORDERED list so
    resolution never depends on dict-key order."""
    code = _as_int(sic)
    if code is None:
        return "unknown"
    for bucket in config.get("sectors", {}).get("buckets", []):
        for lo, hi in bucket.get("sic_ranges", []):
            if lo <= code <= hi:
                return bucket["name"]
    return "unknown"


def leg_applicable(bucket: str, leg: str, config: dict) -> bool:
    if bucket == "unknown":
        return True
    return leg not in config.get("sectors", {}).get("masked_legs", [])


def gate_applicable(bucket: str, gate: str, config: dict) -> bool:
    if bucket == "unknown":
        return True
    return gate not in config.get("sectors", {}).get("masked_gates", [])


def _as_int(sic) -> Optional[int]:
    if sic is None:
        return None
    s = str(sic).strip()
    if not s or not s.isdigit():
        return None
    return int(s)
```

- [ ] **Step 4: Run the tests, verify they pass**

Run: `uv run pytest tests/test_sectors.py -q`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/sectors.py tests/test_sectors.py
git commit -m "feat: sectors.py — SIC->bucket resolver + leg/gate applicability"
```

---

## Task 2: `config.yaml` — `sectors` + `validity` blocks

**Files:**
- Modify: `config.yaml` (append after the `gates:` block, before `providers:`)
- Test: `tests/test_sectors_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sectors_config.py
from shortlist.config import load_config
from shortlist.sectors import resolve_bucket, leg_applicable

def test_shipped_config_buckets_resolve():
    cfg = load_config()
    assert "sectors" in cfg and "validity" in cfg
    assert resolve_bucket("6211", cfg) == "financials"
    assert resolve_bucket("6798", cfg) == "reit"
    assert resolve_bucket("6311", cfg) == "insurer"
    assert resolve_bucket("6231", cfg) == "unknown"   # exchange stays unknown
    assert leg_applicable("financials", "fcf_yield", cfg) is False

def test_validity_defaults():
    v = load_config()["validity"]
    assert 0.0 < v["min_valid_leg_fraction"] <= 1.0
    assert v["unknown_min_present_legs"] >= 1
    assert 0.0 < v["min_scored_weight"] <= 1.0
```

(Confirm the config loader import path first: `grep -n "def load_config" src/shortlist/config.py`. If the function/module differs, adjust the import — do not invent one.)

- [ ] **Step 2: Run the test, verify it fails**

Run: `uv run pytest tests/test_sectors_config.py -q`
Expected: FAIL (`KeyError: 'sectors'`).

- [ ] **Step 3: Add the config blocks**

Insert into `config.yaml` immediately after the `gates:` block:

```yaml
# Sector applicability (SIC -> bucket). v1 MASKS structurally-undefined legs/gates
# for spread/deposit/property businesses; it does NOT recalibrate surviving bands
# (that is deferred mode-2 work). buckets is an ORDERED LIST: first matching range
# wins. See docs/superpowers/specs/2026-06-02-sector-aware-abstention-design.md.
sectors:
  buckets:
    - name: reit
      sic_ranges: [[6798, 6798]]
    - name: insurer
      sic_ranges: [[6300, 6399], [6410, 6411]]
    - name: financials
      sic_ranges: [[6020, 6099], [6120, 6179], [6199, 6199], [6211, 6211], [6712, 6712]]
  # Legs that are undefined / non-representative for the bucketed institutions.
  # net_margin is intentionally NOT masked (it is defined, only miscalibrated -> deferred).
  masked_legs:  [gross_margin, gross_margin_stability, roic, fcf_yield, fcf_cagr, interest_coverage, debt_to_equity]
  masked_gates: [negative_fcf, over_leveraged]

validity:
  # Bucket-gated: floors apply ONLY to known buckets; 'unknown' stays bit-identical
  # to pre-change behavior (any present leg scores; composite is always 'scored').
  min_valid_leg_fraction: 0.5   # known-bucket sub-score needs >= half its APPLICABLE legs present
  unknown_min_present_legs: 1   # unknown: any present leg scores (no-op back-compat)
  min_scored_weight:      0.34  # known-bucket composite 'scored' iff present/applicable component weight >= this
```

- [ ] **Step 4: Run the test, verify it passes**

Run: `uv run pytest tests/test_sectors_config.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add config.yaml tests/test_sectors_config.py
git commit -m "feat: config sectors+validity blocks (financials/reit/insurer masks)"
```

---

## Task 3: `StockMetrics.sic` + `Profile.sic` fields

**Files:**
- Modify: `src/shortlist/models.py` (add field after `sector`)
- Modify: `src/shortlist/data/models.py` (`Profile`)
- Test: `tests/test_sic_field.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sic_field.py
from shortlist.models import StockMetrics
from shortlist.data.models import Profile

def test_stockmetrics_has_sic_default_none():
    assert StockMetrics(ticker="X").sic is None
    assert StockMetrics(ticker="X", sic="6211").sic == "6211"

def test_profile_has_sic_default_none():
    assert Profile().sic is None
    assert Profile(sic="6798").sic == "6798"
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_sic_field.py -q`
Expected: FAIL (`TypeError: unexpected keyword argument 'sic'`).

- [ ] **Step 3: Add the fields**

In `src/shortlist/models.py`, in `StockMetrics`, directly after `sector: Optional[str] = None`:

```python
    sic: Optional[str] = None   # SEC SIC code (EDGAR-sourced); drives sector bucket
```

In `src/shortlist/data/models.py`, in `Profile`, after `industry`:

```python
    sic: Optional[str] = None
```

- [ ] **Step 4: Run, verify pass**

Run: `uv run pytest tests/test_sic_field.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/models.py src/shortlist/data/models.py tests/test_sic_field.py
git commit -m "feat: StockMetrics.sic and Profile.sic fields"
```

---

## Task 4: `ScoreCard` abstention fields + `passed` tightening

**Files:**
- Modify: `src/shortlist/models.py` (`ScoreCard`, append after `coverage`)
- Test: `tests/test_scorecard_fields.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scorecard_fields.py
from shortlist.models import ScoreCard

def _card(**kw):
    base = dict(ticker="X", composite=50.0, quality=None, moat=None, growth=None,
                momentum=None, value=None, opportunity=None, insider=None)
    base.update(kw)
    return ScoreCard(**base)

def test_new_fields_default_backcompat():
    c = _card()
    assert c.sic_bucket is None
    assert c.confidence == 1.0
    assert c.scored is True
    assert c.abstentions == []

def test_passed_requires_scored_and_no_gates():
    assert _card().passed is True
    assert _card(gates=["over_leveraged"]).passed is False
    assert _card(scored=False).passed is False
    assert _card(gates=["x"], scored=False).passed is False
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_scorecard_fields.py -q`
Expected: FAIL (`unexpected keyword argument 'sic_bucket'`).

- [ ] **Step 3: Implement**

In `src/shortlist/models.py`, append to `ScoreCard` **after** the `coverage` field
(all defaulted, so positional construction up through `insider` is unaffected):

```python
    sic_bucket: Optional[str] = None
    confidence: float = 1.0
    scored: bool = True
    abstentions: list = field(default_factory=list)
```

Replace the `passed` property:

```python
    @property
    def passed(self) -> bool:
        return not self.gates and self.scored
```

- [ ] **Step 4: Run, verify pass**

Run: `uv run pytest tests/test_scorecard_fields.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/models.py tests/test_scorecard_fields.py
git commit -m "feat: ScoreCard sic_bucket/confidence/scored/abstentions; passed needs scored"
```

---

## Task 5: scoring.py — named-leg refactor with bucket-gated abstention

This is the core. Split into 5a (sub-score evaluator + unknown no-op) and 5b (composite confidence/scored + gate masking + abstentions wiring).

### Task 5a: named-leg sub-score evaluator (unknown = bit-identical)

**Files:**
- Modify: `src/shortlist/scoring.py`
- Test: `tests/test_scoring_abstention.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_scoring_abstention.py
from shortlist.models import StockMetrics
from shortlist.scoring import score
from shortlist.config import load_config

CFG = load_config()

def _fin(**kw):
    # a broker-dealer (SIC 6211) with present-but-misleading legs
    base = dict(ticker="SCHWX", sic="6211", market_cap=152e9,
                roe=0.17, net_margin=0.36, interest_coverage=1.5, debt_to_equity=8.0,
                gross_margin=0.55, gross_margin_stability=0.9, roic=0.20,
                fcf_yield=0.02, fcf_cagr=0.30,
                revenue_cagr=0.08, eps_cagr=0.06, revenue_growth_persistence=0.5,
                price_vs_200dma=-0.05, rel_strength_6m=-0.08, eps_revision=0.06,
                pe_ttm=17.0, pe_median_5y=20.0, price=87.0, target_median=115.0,
                peg=None, insider_net_6m=-1.2e6, insider_sentiment=-0.05,
                fcf_positive=True)
    base.update(kw); return StockMetrics(**base)

def test_financial_masks_moat_legs_so_moat_abstains():
    card = score(_fin(), CFG)
    assert card.sic_bucket == "financials"
    assert card.moat is None                      # gross_margin+stability+roic all masked
    names = {(a["field"], a["reason"]) for a in card.abstentions}
    assert ("moat", "inapplicable") in names

def test_financial_masks_fcf_yield_in_value():
    card = score(_fin(), CFG)
    # value still scores on upside+pe (fcf_yield masked, peg missing)
    assert ("fcf_yield", "inapplicable") in {(a["field"], a["reason"]) for a in card.abstentions}

def test_unknown_bucket_is_bit_identical_to_legacy():
    # An operating company with no SIC must score EXACTLY as before this change.
    m = StockMetrics(ticker="OPCO", roe=0.5, net_margin=0.5, interest_coverage=5.0,
                     debt_to_equity=1.0, gross_margin=0.6, gross_margin_stability=0.9,
                     roic=0.2, revenue_cagr=0.1, fcf_cagr=0.1, eps_cagr=0.1,
                     revenue_growth_persistence=0.8, price_vs_200dma=0.1,
                     rel_strength_6m=0.1, eps_revision=0.05, fcf_yield=0.05,
                     peg=1.0, market_cap=5e9, insider_sentiment=0.1)
    card = score(m, CFG)
    assert card.sic_bucket == "unknown"
    assert card.scored is True
    assert card.abstentions == []                 # nothing masked, nothing thin
    assert card.quality is not None and card.moat is not None

def test_unknown_momentum_only_name_still_scored():
    # Today a momentum-only name scores on opportunity alone; must NOT flip not_scored.
    m = StockMetrics(ticker="MOM", price_vs_200dma=0.2, rel_strength_6m=0.2,
                     eps_revision=0.05)
    card = score(m, CFG)
    assert card.scored is True
    assert card.passed is True
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_scoring_abstention.py -q`
Expected: FAIL (`sic_bucket` is None/AttributeError or moat not None).

- [ ] **Step 3: Implement the evaluator + leg lists**

Replace the body of `src/shortlist/scoring.py` from the imports through the
sub-score helpers with the following (keep `_norm`, `insider_score`, `_round`):

```python
from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Optional

from .models import ScoreCard, StockMetrics
from .sectors import gate_applicable, leg_applicable, resolve_bucket


def _norm(value: Optional[float], lo: float, hi: float) -> Optional[float]:
    if value is None:
        return None
    if hi == lo:
        return 50.0
    pct = (value - lo) / (hi - lo)
    return max(0.0, min(1.0, pct)) * 100.0


@dataclass
class _Leg:
    name: str               # canonical name (matches masked_legs + threshold key)
    value: Optional[float]
    tkey: str               # key into config['thresholds']


def _eval_subscore(name: str, bucket: str, legs: list[_Leg], t: dict,
                   config: dict) -> tuple[Optional[float], list[dict]]:
    """Return (sub-score or None, abstentions). Replaces silent-drop with an
    explicit applicable/present partition; bucket-gated floor."""
    abst: list[dict] = []
    applicable: list[_Leg] = []
    for lg in legs:
        if leg_applicable(bucket, lg.name, config):
            applicable.append(lg)
        else:
            abst.append({"field": lg.name, "reason": "inapplicable", "scope": "leg"})
    if not applicable:
        abst.append({"field": name, "reason": "inapplicable", "scope": "subscore"})
        return None, abst

    present = [lg for lg in applicable if lg.value is not None]
    for lg in applicable:
        if lg.value is None:
            abst.append({"field": lg.name, "reason": "missing", "scope": "leg"})

    v = config["validity"]
    if bucket == "unknown":
        ok = len(present) >= v["unknown_min_present_legs"]
    else:
        ok = bool(present) and (len(present) / len(applicable)) >= v["min_valid_leg_fraction"]
    if not ok:
        abst.append({"field": name, "reason": "missing", "scope": "subscore"})
        return None, abst

    return mean(_norm(lg.value, *t[lg.tkey]) for lg in present), abst


def _quality_legs(m): return [
    _Leg("roe", m.roe, "roe"),
    _Leg("net_margin", m.net_margin, "net_margin"),
    _Leg("interest_coverage", m.interest_coverage, "interest_coverage"),
    _Leg("debt_to_equity", m.debt_to_equity, "debt_to_equity"),
]
def _moat_legs(m): return [
    _Leg("gross_margin", m.gross_margin, "gross_margin"),
    _Leg("gross_margin_stability", m.gross_margin_stability, "gross_margin_stability"),
    _Leg("roic", m.roic_5y_avg if m.roic_5y_avg is not None else m.roic, "roic"),
]
def _growth_legs(m): return [
    _Leg("revenue_cagr", m.revenue_cagr, "revenue_cagr"),
    _Leg("fcf_cagr", m.fcf_cagr, "fcf_cagr"),
    _Leg("eps_cagr", m.eps_cagr, "eps_cagr"),
    _Leg("revenue_growth_persistence", m.revenue_growth_persistence, "revenue_growth_persistence"),
]
def _momentum_legs(m): return [
    _Leg("price_vs_200dma", m.price_vs_200dma, "price_vs_200dma"),
    _Leg("rel_strength_6m", m.rel_strength_6m, "rel_strength_6m"),
    _Leg("eps_revision", m.eps_revision, "eps_revision"),
]
def _value_legs(m): return [
    _Leg("upside_to_target", m.upside_to_target(), "upside_to_target"),
    _Leg("fcf_yield", m.fcf_yield, "fcf_yield"),
    _Leg("pe_vs_history", m.pe_vs_history(), "pe_vs_history"),
    _Leg("peg", m.peg, "peg"),
]
```

Keep `insider_score`, `check_flags`, `_round` as-is. `check_gates` is updated in 5b.
Then implement the new `score()` in 5b (the evaluator is exercised by it). For 5a,
add a temporary thin `score()` so tests run — but it is cleaner to do 5a and 5b in
one commit. **Do 5b now before running**, then run the 5a tests against the full
`score()`.

### Task 5b: composite confidence/scored, gate masking, abstentions

- [ ] **Step 1: Replace `check_gates` and `score`**

```python
def check_gates(m: StockMetrics, g: dict, bucket: str, config: dict) -> list[str]:
    tripped: list[str] = []
    if m.fcf_positive is False and gate_applicable(bucket, "negative_fcf", config):
        tripped.append("negative_fcf")
    if m.market_cap is not None and m.market_cap < g["min_market_cap"] \
            and gate_applicable(bucket, "below_min_mktcap", config):
        tripped.append("below_min_mktcap")
    if m.debt_to_equity is not None and m.debt_to_equity > g["max_debt_to_equity"] \
            and gate_applicable(bucket, "over_leveraged", config):
        tripped.append("over_leveraged")
    if m.insider_sentiment is not None and m.insider_sentiment < g["min_insider_sentiment"] \
            and gate_applicable(bucket, "heavy_insider_selling", config):
        tripped.append("heavy_insider_selling")
    return tripped


def score(m: StockMetrics, config: dict) -> ScoreCard:
    t = config["thresholds"]
    w = config["weights"]
    bucket = resolve_bucket(m.sic, config)

    abst: list[dict] = []

    def sub(name, legs):
        s, a = _eval_subscore(name, bucket, legs, t, config)
        abst.extend(a)
        return s

    q = sub("quality", _quality_legs(m))
    mo = sub("moat", _moat_legs(m))
    gr = sub("growth", _growth_legs(m))
    mom = sub("momentum", _momentum_legs(m))
    val = sub("value", _value_legs(m))
    pres = [x for x in (mom, val) if x is not None]
    opp = max(pres) if pres else None
    ins = insider_score(m, t)  # sector-neutral; never masked

    # A component is INAPPLICABLE iff its sub-score abstained at subscore scope for
    # the 'inapplicable' reason. opportunity is applicable if EITHER momentum or
    # value is applicable (momentum legs are never masked -> opportunity always
    # applicable for v1 buckets).
    inapplicable = {a["field"] for a in abst
                    if a["scope"] == "subscore" and a["reason"] == "inapplicable"}

    def applic(*subs):
        return any(s not in inapplicable for s in subs)

    components = [
        ("quality", q, w["quality"], ("quality",)),
        ("moat", mo, w["moat"], ("moat",)),
        ("growth", gr, w["growth"], ("growth",)),
        ("opportunity", opp, w["opportunity"], ("momentum", "value")),
        ("insider", ins, w["insider"], ("insider",)),
    ]

    # Composite: unchanged math over present components, weight redistributed.
    parts = [(s, weight) for _, s, weight, _ in components if s is not None]
    num = sum(s * weight for s, weight in parts)
    den = sum(weight for _, weight in parts)
    composite = round(num / den, 1) if den else 0.0

    # Confidence over APPLICABLE components; bucket-gated scored.
    appl_w = sum(weight for nm, _, weight, subs in components if applic(*subs))
    pres_w = sum(weight for nm, s, weight, subs in components
                 if applic(*subs) and s is not None)
    confidence = round(pres_w / appl_w, 3) if appl_w else 0.0
    scored = True if bucket == "unknown" else confidence >= config["validity"]["min_scored_weight"]

    return ScoreCard(
        ticker=m.ticker, composite=composite,
        quality=_round(q), moat=_round(mo), growth=_round(gr), momentum=_round(mom),
        value=_round(val), opportunity=_round(opp), insider=_round(ins),
        gates=check_gates(m, config["gates"], bucket, config),
        flags=check_flags(m, config.get("flags") or {}),
        metrics=m,
        sic_bucket=bucket, confidence=confidence, scored=scored, abstentions=abst,
    )
```

- [ ] **Step 2: Run the 5a tests**

Run: `uv run pytest tests/test_scoring_abstention.py -q`
Expected: PASS (4 tests).

- [ ] **Step 3: Run the FULL existing suite — the no-op guard**

Run: `uv run pytest -q`
Expected: PASS. If any pre-existing `test_scoring.py` test fails, it indicates a
real semantic drift on the `unknown` path — **investigate, do not edit the test to
pass**. (The legacy fixtures have `sic=None` → unknown → must be bit-identical.)
Note: `check_gates` signature changed — fix any direct callers (`grep -rn
"check_gates" src tests`).

- [ ] **Step 4: Commit**

```bash
git add src/shortlist/scoring.py tests/test_scoring_abstention.py
git commit -m "feat: bucket-gated leg/sub-score abstention + confidence/scored + gate masking"
```

---

## Task 6: gate-masking & confidence behavioral tests (financials)

**Files:**
- Test: `tests/test_scoring_abstention.py` (extend)

- [ ] **Step 1: Add tests**

```python
def test_over_leveraged_gate_masked_for_financial():
    card = score(_fin(debt_to_equity=8.0), CFG)   # 8.0 > max 5.0
    assert "over_leveraged" not in card.gates      # masked
    # same metrics as unknown -> gate fires
    from shortlist.models import StockMetrics
    un = score(_fin(sic=None, debt_to_equity=8.0), CFG)
    assert "over_leveraged" in un.gates

def test_financial_scored_with_reduced_confidence():
    card = score(_fin(), CFG)
    assert card.scored is True          # quality/growth/opportunity/insider present
    assert card.confidence <= 1.0
    assert card.composite > 0.0         # number still emitted (audit)

def test_data_starved_financial_not_scored():
    # only insider present among applicable components -> below floor
    from shortlist.models import StockMetrics
    m = StockMetrics(ticker="THIN", sic="6211", market_cap=10e9,
                     insider_sentiment=0.1)
    card = score(m, CFG)
    assert card.scored is False
    assert card.passed is False
```

- [ ] **Step 2: Run, verify pass**

Run: `uv run pytest tests/test_scoring_abstention.py -q`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_scoring_abstention.py
git commit -m "test: gate masking, reduced-confidence scored, data-starved not_scored"
```

---

## Task 7: screener EDGAR — set & tag `sic`

**Files:**
- Modify: `src/shortlist/providers/edgar.py` (`fetch`, ~line 39-46)
- Test: `tests/test_edgar_sic.py`

- [ ] **Step 1: Write the failing test** (no live SEC call — patch `Company`)

```python
# tests/test_edgar_sic.py
from unittest.mock import patch, MagicMock
from shortlist.providers.edgar import EdgarProvider

def test_edgar_provider_sets_and_tags_sic(monkeypatch):
    monkeypatch.setenv("SEC_IDENTITY", "test test@example.com")
    fake = MagicMock(); fake.sic = 6211
    # aggregate_form4 path returns no insider; we only assert sic plumbing
    with patch("shortlist.providers.edgar.Company", return_value=fake), \
         patch("shortlist.providers.edgar.aggregate_form4", return_value=None):
        m = EdgarProvider().fetch("SCHW")
    assert m.sic == "6211"
    assert m.sources.get("sic") == "edgar"
```

(First confirm the symbol names imported in `edgar.py`: `grep -n "import\|aggregate_form4\|Company\|_tag" src/shortlist/providers/edgar.py`, and adapt the patch targets/return handling to the real `fetch` shape — keep the assertion on `m.sic`/`m.sources["sic"]`.)

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_edgar_sic.py -q`
Expected: FAIL (`m.sic is None`).

- [ ] **Step 3: Implement**

In `src/shortlist/providers/edgar.py`, add the import and set/tag SIC. After
`company = Company(ticker)` (line ~39):

```python
        from ..sectors import extract_sic
        m.sic = extract_sic(company)
```

Change the tag line (edgar.py:46) from:

```python
        return self._tag(m, "insider_net_6m")
```
to:
```python
        return self._tag(m, "insider_net_6m", "sic")
```

(Verify `_tag` accepts varargs field names — check `base.py`. If `_tag` tags a
single field, call it once per field or extend it; do not guess — read `base.py`.)

- [ ] **Step 4: Run, verify pass**

Run: `uv run pytest tests/test_edgar_sic.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/providers/edgar.py tests/test_edgar_sic.py
git commit -m "feat: EdgarProvider sets+tags SIC (screener sector detection)"
```

---

## Task 8: harness EDGAR — emit partial `Profile(sic=…)`; bridge copies

**Files:**
- Modify: `src/shortlist/data/sources.py` (`EdgarSource` sync build)
- Modify: `src/shortlist/data/bridge.py` (copy `m.sic = p.sic`)
- Test: `tests/test_harness_sic.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_harness_sic.py
from shortlist.data.models import Profile, TickerSnapshot, merge_snapshots
from shortlist.data.bridge import snapshot_to_metrics

def test_merge_keeps_edgar_sic_when_fmp_profile_absent():
    # FMP gated -> no profile; EDGAR supplies a partial profile carrying only sic.
    edgar = TickerSnapshot(ticker="SCHW"); edgar.profile = Profile(sic="6211")
    finnhub = TickerSnapshot(ticker="SCHW"); finnhub.profile = Profile(name="Schwab", market_cap=152e9)
    merged = merge_snapshots([finnhub, edgar])   # finnhub higher priority for name/mktcap
    assert merged.profile.sic == "6211"
    assert merged.profile.name == "Schwab"

def test_bridge_copies_sic_to_metrics():
    snap = TickerSnapshot(ticker="SCHW"); snap.profile = Profile(name="Schwab", sic="6211")
    m = snapshot_to_metrics(snap)
    assert m.sic == "6211"
```

(Confirm `merge_snapshots`/`_merge_flat` treats `profile` field-by-field so a
partial profile's `sic` survives — read `data/models.py:_merge_flat`. The first
test directly asserts that invariant.)

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_harness_sic.py -q`
Expected: FAIL (bridge does not copy sic; maybe merge drops it).

- [ ] **Step 3: Implement**

(a) In `src/shortlist/data/bridge.py`, in the `if p:` profile block (after
`m.sector = p.sector`):

```python
        m.sic = p.sic
```

(b) In `src/shortlist/data/sources.py`, in `EdgarSource`'s synchronous build (where
it constructs the `Company` for Form 4 / financials), capture SIC and attach a
partial profile to the snapshot. Locate the `Company(...)` construction in
`EdgarSource` and add:

```python
        from ..sectors import extract_sic
        sic = extract_sic(company)          # company already built for form4/financials
        if sic:
            snap.profile = Profile(sic=sic) # partial: only sic; _merge_flat fills the rest
```

Place this so it runs even when insider/financials are empty (SIC must survive a
foreign-issuer/no-Form4 symbol). Wrap consistently with the existing per-section
try/except so a SIC failure degrades to `None` without touching insider/statements.
(Read the real method names — likely `_fetch_sync` building a `TickerSnapshot`; add
`from .models import Profile` if not already imported.)

- [ ] **Step 4: Run, verify pass**

Run: `uv run pytest tests/test_harness_sic.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/data/sources.py src/shortlist/data/bridge.py tests/test_harness_sic.py
git commit -m "feat: harness EdgarSource emits partial Profile(sic); bridge copies to metrics"
```

---

## Task 9: coverage / abstention reconciliation

**Files:**
- Modify: `src/shortlist/coverage.py` (`build_coverage`)
- Test: `tests/test_coverage_abstention.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_coverage_abstention.py
from shortlist.coverage import build_coverage
from shortlist.models import ScoreCard, StockMetrics

def _card(**kw):
    base = dict(ticker="SCHW", composite=40.0, quality=50.0, moat=None, growth=50.0,
                momentum=50.0, value=None, opportunity=50.0, insider=50.0,
                sic_bucket="financials",
                abstentions=[{"field": "moat", "reason": "inapplicable", "scope": "subscore"},
                             {"field": "fcf_yield", "reason": "inapplicable", "scope": "leg"}],
                metrics=StockMetrics(ticker="SCHW", sic="6211"))
    base.update(kw); return ScoreCard(**base)

def test_inapplicable_subscore_not_listed_as_coverage_gap():
    cov = build_coverage({"fmp": "gated_402", "edgar": "ok"}, {"edgar"}, _card())
    # 'moat' is None by masking, NOT a coverage gap -> excluded from unavailable
    assert "moat" not in cov.unavailable
    # 'value' is None by masking too (fcf masked + others missing); should not be a
    # 'gated' story. (value abstained inapplicable at subscore scope if present.)
```

(Adjust the abstention fixture so it matches what `score()` actually emits for a
masked-`value`; the invariant under test: a sub-score whose abstention reason is
`inapplicable` at `subscore` scope is NOT in `coverage.unavailable`.)

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_coverage_abstention.py -q`
Expected: FAIL (`moat` present in `unavailable`).

- [ ] **Step 3: Implement**

In `src/shortlist/coverage.py` `build_coverage`, compute the set of
inapplicable-masked sub-scores from the card and exclude them:

```python
    masked = {a["field"] for a in getattr(card, "abstentions", [])
              if a.get("scope") == "subscore" and a.get("reason") == "inapplicable"}
    unavailable = [f for f in _SUBSCORE_FIELDS
                   if getattr(card, f) is None and f not in masked]
```

(Leave the `upside_to_target` append and note logic unchanged.)

- [ ] **Step 4: Run, verify pass + full suite**

Run: `uv run pytest tests/test_coverage_abstention.py tests/test_coverage.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/coverage.py tests/test_coverage_abstention.py
git commit -m "fix: exclude masked-inapplicable sub-scores from coverage.unavailable"
```

---

## Task 10: CLI output — emit fields, sort by (scored, composite)

**Files:**
- Modify: `src/shortlist/screen.py` (`_card_dict`, two sort sites lines 48 & 70, CSV header/row)
- Test: `tests/test_card_dict_abstention.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_card_dict_abstention.py
from shortlist.screen import _card_dict, run            # adjust import if _card_dict private
from shortlist.models import ScoreCard, StockMetrics

def _c(**kw):
    base = dict(ticker="X", composite=60.0, quality=None, moat=None, growth=None,
                momentum=None, value=None, opportunity=None, insider=None,
                metrics=StockMetrics(ticker="X"))
    base.update(kw); return ScoreCard(**base)

def test_card_dict_includes_abstention_block_when_present():
    c = _c(sic_bucket="financials", confidence=0.8, scored=True,
           abstentions=[{"field": "moat", "reason": "inapplicable", "scope": "subscore"}])
    d = _card_dict(c)
    assert d["sic_bucket"] == "financials"
    assert d["confidence"] == 0.8
    assert d["scored"] is True
    assert d["abstentions"]

def test_card_dict_omits_abstentions_when_empty():
    d = _card_dict(_c())
    assert "abstentions" not in d
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_card_dict_abstention.py -q`
Expected: FAIL (`KeyError: 'sic_bucket'`).

- [ ] **Step 3: Implement**

In `_card_dict`, after the base `d = {...}` dict, add:

```python
    d["sic_bucket"] = c.sic_bucket
    d["confidence"] = c.confidence
    d["scored"] = c.scored
    if c.abstentions:
        d["abstentions"] = c.abstentions
```

Change both sort sites (screen.py:48 and screen.py:70) from:

```python
    cards.sort(key=lambda c: c.composite, reverse=True)
```
to:
```python
    cards.sort(key=lambda c: (c.scored, c.composite), reverse=True)
```

Extend the CSV header (add `"scored"`, `"sic_bucket"`) and the row writer to emit
`d["scored"]`, `d["sic_bucket"]`.

- [ ] **Step 4: Run, verify pass + full suite**

Run: `uv run pytest -q`
Expected: PASS. Fix any `test_screen_engine.py` expectations on row/JSON shape.

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/screen.py tests/test_card_dict_abstention.py
git commit -m "feat: emit sic_bucket/confidence/scored/abstentions; demote not_scored in ranking"
```

---

## Task 11: mock SIC fixtures + end-to-end golden & parity tests

**Files:**
- Modify: `src/shortlist/providers/mock.py` (SCHW + add a REIT or set realistic SCHW legs)
- Modify: `src/shortlist/data/mockdata.py` (SCHW snapshot profile sic)
- Test: `tests/test_sector_parity.py`

- [ ] **Step 1: Add SIC to mock fixtures**

In `src/shortlist/providers/mock.py`, SCHW dict — add `sic="6211"` and set legs so
the gate/masking story is demonstrable:

```python
    "SCHW": dict(
        name="Charles Schwab", sector="Financials", sic="6211", price=87, market_cap=152e9,
        pe_ttm=17.0, pe_median_5y=20.0, fcf_yield=0.02, target_median=115,
        roe=0.17, roic=None, roic_5y_avg=None, gross_margin=None, net_margin=0.36,
        debt_to_equity=8.0, interest_coverage=1.5, fcf_positive=True,
        revenue_cagr=0.08, eps_cagr=0.06, revenue_growth_persistence=0.50,
        gross_margin_stability=None, price_vs_200dma=-0.05, rel_strength_6m=-0.08,
        eps_revision=0.06, rating_buy=17, rating_hold=2, rating_sell=1,
        insider_net_6m=-1.2e6, insider_sentiment=-0.05,
    ),
```

In `src/shortlist/data/mockdata.py`, SCHW `Profile(...)` — add `sic="6211"`.

- [ ] **Step 2: Write the parity + golden tests**

```python
# tests/test_sector_parity.py
from shortlist.config import load_config
from shortlist.providers.mock import MockProvider          # adjust to real class/name
from shortlist.merge import merge
from shortlist.scoring import score
from shortlist.data.mockdata import MockSource              # adjust to real name
from shortlist.data.bridge import snapshot_to_metrics

CFG = load_config()

def test_schw_golden_screener():
    m = merge([MockProvider().fetch("SCHW")])
    card = score(m, CFG)
    assert card.sic_bucket == "financials"
    assert card.moat is None                       # masked -> abstains
    assert "over_leveraged" not in card.gates      # gate masked despite D/E 8.0
    assert card.composite > 0.0                    # number still emitted

def test_two_stack_parity_for_schw():
    # Same ticker, both engines, identical sector decision & gate outcome.
    m_screener = merge([MockProvider().fetch("SCHW")])
    card_s = score(m_screener, CFG)
    snap = MockSource().fetch_sync("SCHW")          # adjust to the real mock-source API
    card_h = score(snapshot_to_metrics(snap), CFG)
    assert card_s.sic_bucket == card_h.sic_bucket == "financials"
    assert ("over_leveraged" in card_s.gates) == ("over_leveraged" in card_h.gates)
    assert (card_s.moat is None) == (card_h.moat is None)
    assert card_s.scored == card_h.scored

def test_edgar_absent_both_unknown():
    # Strip SIC on both -> both resolve unknown -> symmetric, no masking.
    m = merge([MockProvider().fetch("SCHW")]); m.sic = None
    card = score(m, CFG)
    assert card.sic_bucket == "unknown"
    assert card.moat is not None or card.moat is None  # whatever legs present, NOT masked
    # the point: over_leveraged NOT masked when unknown
    assert "over_leveraged" in card.gates              # D/E 8.0 trips when unknown
```

(Adjust the mock provider/source class & method names to the real ones —
`grep -n "class .*Provider\|def fetch" src/shortlist/providers/mock.py` and
`grep -n "class .*Source\|def fetch" src/shortlist/data/mockdata.py`. The assertions
are the contract; the wiring adapts to the real fixtures.)

- [ ] **Step 3: Run, verify pass + full suite**

Run: `uv run pytest -q`
Expected: PASS. If any pre-existing test asserted the **old** SCHW mock composite,
update it — SCHW's score legitimately changes (that is the feature). Operating-co
tests must NOT need changes (unknown no-op).

- [ ] **Step 4: Commit**

```bash
git add src/shortlist/providers/mock.py src/shortlist/data/mockdata.py tests/test_sector_parity.py
git commit -m "test: SCHW golden + two-stack parity + EDGAR-absent symmetry (keyless)"
```

---

## Task 12: docs

**Files:**
- Modify: `CLAUDE.md` (new "Sector-aware applicability & abstention" section)
- Modify: `README.md` and/or `HARNESS.md` (brief note + `--json` fields)

- [ ] **Step 1: Update `CLAUDE.md`**

Add a section documenting: SIC→bucket detection (EDGAR-only, both stacks, contingent
on EDGAR in chain + `SEC_IDENTITY`); masked legs/gates per bucket; `unknown` = no-op;
the `passed = not gates and scored` change; the `sic_bucket`/`confidence`/`scored`/
`abstentions` JSON fields; and that calibration of surviving bands is **deferred**
(mode 2). Cross-reference the spec. Note `net_margin` is intentionally unmasked.

- [ ] **Step 2: Update `README.md` / `HARNESS.md`**

Document the new `--json` fields and the not-scored ranking demotion for consumers
(scout, `/run`).

- [ ] **Step 3: Run the full suite once more**

Run: `uv run pytest -q`
Expected: PASS (all green).

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md README.md HARNESS.md
git commit -m "docs: sector-aware applicability & abstention"
```

---

## Final verification (before finishing the branch)

- [ ] `uv run pytest -q` — all green, no skips beyond the 3 pre-existing.
- [ ] `uv run shortlist --demo --json` — SCHW shows `sic_bucket: financials`, `moat:
  null`, no `over_leveraged`, an `abstentions` block; operating cos unchanged.
- [ ] `grep -rn "m.sector" src/shortlist/scoring.py src/shortlist/sectors.py` — empty
  (applicability never reads free-text sector).
- [ ] Spec coverage re-check against `2026-06-02-sector-aware-abstention-design.md`
  §4–§7; every item maps to a task above.
- [ ] Invoke `superpowers:verification-before-completion` before claiming done.
```

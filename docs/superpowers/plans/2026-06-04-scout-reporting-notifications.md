# Scout Reporting + Notification Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `shortlist-scout`'s daily run a real reporting + delivery layer — an inline chart image, a styled HTML deep-dive, and hardened Telegram delivery — without a heavyweight charting dependency.

**Architecture:** A thin renderer-agnostic view-model owns the 3-tier (ScoreCard / StockMetrics / QualitativeAssessment) join and None semantics once. A section registry renders HTML and text from it; the PNG glance is a separate, deliberately limited Pillow view. A `Notifier` transport seam (httpx-injected) handles `sendPhoto`/`sendDocument`/`sendMessage` with chunking + retry; a `deliver()` policy preserves exit-code semantics.

**Tech Stack:** Python 3.12, Pillow (raster glance, lazy-imported scout extra), pure HTML/CSS (zero-dep), httpx (Telegram), pytest. matplotlib/numpy are **not** used.

**Spec:** `docs/superpowers/specs/2026-06-04-scout-reporting-notifications-design.md`

> **This plan incorporates a 3-reviewer pass.** Back-compat hazards are now explicit
> steps (not asides): the `_research_phase` tuple widening enumerates **all six** return
> sites; `test_fixes.py`/`test_research_budget.py`/`test_orchestrator_integration.py` get
> concrete edits; demo never imports Pillow; the GLANCE brief routes through one section
> (no `briefs`-on-view-model wart).

---

## File structure

Create the `report/` package (replaces the single `report.py` module; the facade re-exports `render_message` so `daily.py` and existing tests keep working):

| File | Responsibility |
|------|----------------|
| `src/shortlist/scout/report/__init__.py` | Facade: `build_report(...) -> ReportArtifacts`; re-export `render_message`. |
| `src/shortlist/scout/report/theme.py` | Palette + sub-score order/labels + `score_to_rgb`. Dep-free. |
| `src/shortlist/scout/report/viewmodel.py` | `ReportVM` + builder; owns the join + None/derivation logic. Dep-free. |
| `src/shortlist/scout/report/html.py` | `HtmlBuilder` (tags + single `esc()`), CSS theme, document assembly. Dep-free. |
| `src/shortlist/scout/report/sections.py` | `Section` protocol + `SECTIONS` registry; HTML + text per section. |
| `src/shortlist/scout/report/png.py` | `render_glance(vm) -> bytes` via Pillow. Only module importing Pillow. |
| `src/shortlist/scout/notify.py` | (modify) add `TelegramNotifier`, chunking, retry, `deliver()`. Keep `send_telegram`. |
| `src/shortlist/scout/daily.py` | (modify) `_research_phase` returns assessments; `run()` builds + delivers + persists. |
| `pyproject.toml` | (modify) add `scout` extra with Pillow. |
| `config.yaml` | (modify) add `scout.report` block. |

> **Migration note:** `src/shortlist/scout/report.py` becomes `src/shortlist/scout/report/__init__.py`. It is deleted in Task 5; until then leave the old file in place so the suite stays green. Do NOT import from `report.py` inside the package.

---

## Task 1: Theme (palette + colormap)

**Files:**
- Create: `src/shortlist/scout/report/__init__.py` (empty marker for now)
- Create: `src/shortlist/scout/report/theme.py`
- Test: `tests/scout/test_report_theme.py`

- [ ] **Step 1: Create the empty package marker**

Create `src/shortlist/scout/report/__init__.py` with a single line:

```python
# shortlist.scout.report — reporting layer (view-model, sections, renderers).
```

> Note: `src/shortlist/scout/report.py` still exists and still wins the import. That's fine until Task 5, where it's deleted and its contents move here. Do NOT import from `report.py` inside the package.

- [ ] **Step 2: Write the failing test**

```python
# tests/scout/test_report_theme.py
from shortlist.scout.report.theme import score_to_rgb, SUBS, SUB_LABELS, GRAY_BAD


def test_colormap_endpoints_and_midpoint():
    assert score_to_rgb(0)[0] > score_to_rgb(0)[1]      # red: R dominates
    assert score_to_rgb(100)[1] > score_to_rgb(100)[0]  # green: G dominates
    mid = score_to_rgb(50)
    assert mid[0] > 150 and mid[1] > 150                 # yellow: high R and G
    assert mid[2] < mid[0] and mid[2] < mid[1]           # ...and low B (not gray/white)


def test_colormap_none_is_gray_and_clamps():
    assert score_to_rgb(None) == GRAY_BAD
    assert score_to_rgb(-20) == score_to_rgb(0)          # clamp low
    assert score_to_rgb(140) == score_to_rgb(100)        # clamp high


def test_subscore_order_and_labels_aligned():
    assert SUBS == ["quality", "moat", "growth", "value", "momentum", "insider", "risk"]
    assert [SUB_LABELS[s] for s in SUBS] == ["Qual", "Moat", "Grow", "Value", "Mom", "Insdr", "Risk"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/scout/test_report_theme.py -v`
Expected: FAIL with `ModuleNotFoundError: shortlist.scout.report.theme`

- [ ] **Step 4: Write the implementation**

```python
# src/shortlist/scout/report/theme.py
"""Single source of truth for report colors + sub-score order. Dep-free (no numpy)."""
from __future__ import annotations

SUBS = ["quality", "moat", "growth", "value", "momentum", "insider", "risk"]
SUB_LABELS = {"quality": "Qual", "moat": "Moat", "growth": "Grow", "value": "Value",
              "momentum": "Mom", "insider": "Insdr", "risk": "Risk"}

BG = (23, 33, 43)        # #17212b
FG = (233, 237, 239)     # #e9edef
GRID = (43, 57, 71)      # #2b3947
GRAY_BAD = (51, 64, 77)  # #33404d — None / masked cell

# RdYlGn anchors at 0 / 50 / 100.
_STOPS = [(0.0, (215, 48, 39)), (0.5, (255, 235, 130)), (1.0, (26, 152, 80))]


def score_to_rgb(v: float | None) -> tuple[int, int, int]:
    """Map a 0..100 score to an (r,g,b) tuple. None -> neutral gray. Clamps out-of-range."""
    if v is None:
        return GRAY_BAD
    t = max(0.0, min(1.0, v / 100.0))
    for (t0, c0), (t1, c1) in zip(_STOPS, _STOPS[1:]):
        if t <= t1:
            f = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return tuple(round(a + (b - a) * f) for a, b in zip(c0, c1))  # type: ignore[return-value]
    return _STOPS[-1][1]


def rgb_hex(c: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % c


def text_on(c: tuple[int, int, int]) -> tuple[int, int, int]:
    """Pick dark or light text for legibility on fill `c` (luminance test)."""
    lum = 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
    return (17, 24, 31) if lum > 140 else (233, 237, 239)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/scout/test_report_theme.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add src/shortlist/scout/report/__init__.py src/shortlist/scout/report/theme.py tests/scout/test_report_theme.py
git commit -m "feat(scout-report): theme palette + dep-free RdYlGn colormap"
```

---

## Task 2: View-model

**Files:**
- Create: `src/shortlist/scout/report/viewmodel.py`
- Test: `tests/scout/test_report_viewmodel.py`

> **Design:** `build_view_model` takes only `assessments: dict[str, dict]` (the parsed
> research JSON records). No `briefs` and no `config` parameter (both were dead weight —
> the GLANCE brief is routed through a synthesized assessment in the facade, Task 5).
> `target_upside` reuses the audited `StockMetrics.upside_to_target()` method (DRY;
> note: it is a method, call with `()`, and it already guards `price` falsy/None → None).

- [ ] **Step 1: Write the failing test**

```python
# tests/scout/test_report_viewmodel.py
from datetime import date
from shortlist.models import ScoreCard, StockMetrics
from shortlist.scout.models import RunManifest, SignalStatus
from shortlist.scout.report.viewmodel import build_view_model


def _card(ticker, comp, **kw):
    base = dict(ticker=ticker, composite=comp, quality=70, moat=60, growth=50,
                momentum=80, value=40, opportunity=80, insider=55)
    base.update(kw)
    return ScoreCard(**base)


def _manifest():
    return RunManifest(session=date(2026, 6, 4),
                       signals=[SignalStatus("edgar_form4", True, "2 clusters")],
                       raw=10, after_dedup=8, after_prefilter=5, screened=2,
                       dropped_for_budget=1, researched=["AAPL"], notes=["hi"])


def test_leaders_sorted_by_scored_then_composite():
    cards = [_card("LOW", 40.0), _card("HIGH", 90.0),
             _card("NS1", 99.0, scored=False), _card("NS2", 50.0, scored=False)]
    vm = build_view_model(cards, _manifest(), assessments={})
    # scored desc by composite, then not-scored group desc by composite
    assert [l.ticker for l in vm.leaders] == ["HIGH", "LOW", "NS1", "NS2"]


def test_target_upside_uses_metrics_property():
    m = StockMetrics(ticker="AAPL", price=100.0, target_median=137.0)
    vm = build_view_model([_card("AAPL", 80.0, metrics=m)], _manifest(), assessments={})
    assert abs(vm.leaders[0].metrics.target_upside - 0.37) < 1e-6


def test_target_upside_none_for_missing_or_zero_price():
    for p in (None, 0.0):
        m = StockMetrics(ticker="AAPL", price=p, target_median=137.0)
        vm = build_view_model([_card("AAPL", 80.0, metrics=m)], _manifest(), assessments={})
        assert vm.leaders[0].metrics.target_upside is None


def test_assessment_present_only_for_researched():
    rec = {"business_model_summary": "Chips.", "synthesis": "Cheap-ish AI leader.",
           "thesis": {"bull_case": "AI demand", "bear_case": "Cyclical",
                      "takeaway": "Cheap-ish AI leader.",
                      "what_would_change_my_mind": ["margin compression"]},
           "risks": [{"claim": "China export limits"}],
           "red_flags": [], "management_capital_allocation": "Buybacks"}
    cards = [_card("AAPL", 80.0), _card("MSFT", 70.0)]
    vm = build_view_model(cards, _manifest(), assessments={"AAPL": rec})
    a = {l.ticker: l for l in vm.leaders}
    assert a["AAPL"].assessment.bull_case == "AI demand"
    assert a["AAPL"].assessment.risks == ["China export limits"]
    assert a["AAPL"].assessment.takeaway == "Cheap-ish AI leader."
    assert a["MSFT"].assessment is None


def test_funnel_and_subscores_carried():
    vm = build_view_model([_card("AAPL", 80.0)], _manifest(), assessments={})
    assert vm.funnel.screened == 2
    assert vm.leaders[0].subscores["quality"] == 70
    assert vm.leaders[0].subscores["risk"] is None  # not set on _card -> None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scout/test_report_viewmodel.py -v`
Expected: FAIL with `ModuleNotFoundError: shortlist.scout.report.viewmodel`

- [ ] **Step 3: Write the implementation**

```python
# src/shortlist/scout/report/viewmodel.py
"""Renderer-agnostic snapshot of one scout run. Pure data; no I/O, no optional deps."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from shortlist.models import ScoreCard
from ..models import RunManifest
from .theme import SUBS


@dataclass
class MetricsVM:
    price: float | None = None
    market_cap: float | None = None
    pe_ttm: float | None = None
    pe_median_5y: float | None = None
    fcf_yield: float | None = None
    peg: float | None = None
    roe: float | None = None
    roic: float | None = None
    gross_margin: float | None = None
    net_margin: float | None = None
    debt_to_equity: float | None = None
    revenue_cagr: float | None = None
    eps_cagr: float | None = None
    price_vs_200dma: float | None = None
    rel_strength_6m: float | None = None
    realized_vol: float | None = None
    max_drawdown: float | None = None
    rating_buy: int | None = None
    rating_hold: int | None = None
    rating_sell: int | None = None
    target_upside: float | None = None   # from StockMetrics.upside_to_target
    insider_net_6m: float | None = None
    insider_distinct_buyers: int | None = None


@dataclass
class AssessmentVM:
    business_model: str = ""
    takeaway: str = ""                    # one-line TL;DR (synthesis / thesis.takeaway)
    bull_case: str = ""
    bear_case: str = ""
    change_my_mind: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
    capital_allocation: str = ""


@dataclass
class LeaderVM:
    ticker: str
    name: str | None
    composite: float
    subscores: dict[str, float | None]
    masked: set[str]
    gates: list[str]
    flags: list[str]
    confidence: float | None
    thin: bool
    scored: bool
    coverage_note: str | None
    metrics: MetricsVM
    assessment: AssessmentVM | None


@dataclass
class SignalStatusVM:
    name: str
    ran: bool
    detail: str


@dataclass
class FunnelVM:
    raw: int
    after_dedup: int
    after_prefilter: int
    screened: int
    dropped_for_budget: int


@dataclass
class ReportVM:
    session: date
    leaders: list[LeaderVM]
    signals: list[SignalStatusVM]
    funnel: FunnelVM
    notes: list[str]


def _claim(x) -> str:
    return x.get("claim", "") if isinstance(x, dict) else str(x)


def _assessment_vm(rec: dict) -> AssessmentVM:
    th = rec.get("thesis") or {}
    return AssessmentVM(
        business_model=rec.get("business_model_summary", "") or "",
        takeaway=(rec.get("synthesis") or th.get("takeaway", "") or ""),
        bull_case=th.get("bull_case", "") or "",
        bear_case=th.get("bear_case", "") or "",
        change_my_mind=[str(x) for x in (th.get("what_would_change_my_mind") or [])],
        risks=[_claim(x) for x in (rec.get("risks") or [])],
        red_flags=[_claim(x) for x in (rec.get("red_flags") or [])],
        capital_allocation=rec.get("management_capital_allocation", "") or "",
    )


def _metrics_vm(m) -> MetricsVM:
    if m is None:
        return MetricsVM()
    return MetricsVM(
        price=m.price, market_cap=m.market_cap, pe_ttm=m.pe_ttm,
        pe_median_5y=m.pe_median_5y, fcf_yield=m.fcf_yield, peg=m.peg, roe=m.roe,
        roic=m.roic, gross_margin=m.gross_margin, net_margin=m.net_margin,
        debt_to_equity=m.debt_to_equity, revenue_cagr=m.revenue_cagr, eps_cagr=m.eps_cagr,
        price_vs_200dma=m.price_vs_200dma, rel_strength_6m=m.rel_strength_6m,
        realized_vol=m.realized_vol, max_drawdown=m.max_drawdown,
        rating_buy=m.rating_buy, rating_hold=m.rating_hold, rating_sell=m.rating_sell,
        target_upside=m.upside_to_target(), insider_net_6m=m.insider_net_6m,
        insider_distinct_buyers=m.insider_distinct_buyers)


def _leader_vm(c: ScoreCard, assessments: dict[str, dict]) -> LeaderVM:
    subs = {s: getattr(c, s, None) for s in SUBS}
    bucket = getattr(c, "sic_bucket", None)
    # Display heuristic: a None sub-score on a sectored card reads as "n/a for sector".
    masked = {s for s, v in subs.items() if v is None and bucket not in (None, "unknown")}
    note = c.coverage.note if (c.coverage is not None and c.coverage.note) else None
    rec = assessments.get(c.ticker)
    return LeaderVM(
        ticker=c.ticker,
        name=getattr(c.metrics, "name", None) if c.metrics else None,
        composite=c.composite, subscores=subs, masked=masked,
        gates=list(c.gates), flags=list(getattr(c, "flags", [])),
        confidence=getattr(c, "confidence", None), thin=getattr(c, "thin", False),
        scored=getattr(c, "scored", True), coverage_note=note,
        metrics=_metrics_vm(c.metrics),
        assessment=_assessment_vm(rec) if rec else None)


def build_view_model(cards, manifest: RunManifest, *,
                     assessments: dict[str, dict]) -> ReportVM:
    ordered = sorted(cards, key=lambda c: (getattr(c, "scored", True), c.composite),
                     reverse=True)
    return ReportVM(
        session=manifest.session,
        leaders=[_leader_vm(c, assessments) for c in ordered],
        signals=[SignalStatusVM(s.name, s.ran, s.detail) for s in manifest.signals],
        funnel=FunnelVM(manifest.raw, manifest.after_dedup, manifest.after_prefilter,
                        manifest.screened, manifest.dropped_for_budget),
        notes=list(manifest.notes))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/scout/test_report_viewmodel.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/scout/report/viewmodel.py tests/scout/test_report_viewmodel.py
git commit -m "feat(scout-report): renderer-agnostic view-model (3-tier join, upside reuse, sort)"
```

---

## Task 3: HTML builder (tags + escaping)

**Files:**
- Create: `src/shortlist/scout/report/html.py`
- Test: `tests/scout/test_report_html.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/scout/test_report_html.py
from shortlist.scout.report.html import HtmlBuilder, document


def test_esc_escapes_all_dangerous_chars():
    h = HtmlBuilder()
    out = h.esc('<script>"x" & y</script>')
    assert "<script>" not in out and "&lt;script&gt;" in out
    assert "&amp;" in out and "&quot;" in out


def test_tag_escapes_text_content_and_attrs():
    h = HtmlBuilder()
    assert h.tag("td", "A & B") == "<td>A &amp; B</td>"
    assert h.tag("td", "x", style="color:red") == '<td style="color:red">x</td>'
    assert "&quot;" in h.tag("td", "x", title='a"b')   # attr value escaped


def test_document_is_self_contained_html():
    out = document("Scout — 2026-06-04", png_b64=None, body="<p>hi</p>")
    assert out.startswith("<!DOCTYPE html>")
    assert "<style>" in out and "Scout — 2026-06-04" in out and "<p>hi</p>" in out


def test_document_embeds_png_when_present():
    out = document("T", png_b64="AAAA", body="")
    assert 'src="data:image/png;base64,AAAA"' in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scout/test_report_html.py -v`
Expected: FAIL with `ModuleNotFoundError: shortlist.scout.report.html`

- [ ] **Step 3: Write the implementation**

```python
# src/shortlist/scout/report/html.py
"""Zero-dep HTML assembly. Every interpolated value goes through HtmlBuilder.esc()."""
from __future__ import annotations

import html as _html

from .theme import rgb_hex, BG, FG, GRID


class HtmlBuilder:
    """Tiny tag helper with a single escaping choke-point. No templating engine."""

    def esc(self, s) -> str:
        return _html.escape("" if s is None else str(s), quote=True)

    def tag(self, name: str, text: str = "", **attrs) -> str:
        a = "".join(f' {k.replace("_", "-")}="{self.esc(v)}"' for k, v in attrs.items())
        return f"<{name}{a}>{self.esc(text)}</{name}>"

    def raw(self, name: str, inner_html: str, **attrs) -> str:
        """Wrap already-safe inner HTML (built from other esc'd pieces)."""
        a = "".join(f' {k.replace("_", "-")}="{self.esc(v)}"' for k, v in attrs.items())
        return f"<{name}{a}>{inner_html}</{name}>"


_CSS = f"""
body {{ background:{rgb_hex(BG)}; color:{rgb_hex(FG)};
        font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin:0; padding:20px; }}
h1 {{ font-size:20px; }} h2 {{ font-size:16px; margin-top:28px; }}
img.glance {{ max-width:100%; border-radius:6px; }}
table {{ border-collapse:collapse; margin:8px 0; font-size:13px; }}
td, th {{ padding:4px 8px; text-align:right; }}
td.k, th.k {{ text-align:left; color:#9fb0bd; }}
.card {{ border:1px solid {rgb_hex(GRID)}; border-radius:8px; padding:12px 16px; margin:12px 0; }}
.bull {{ color:#7fc99a; }} .bear {{ color:#e08f8f; }} .flag {{ color:#e0b86a; }}
.muted {{ color:#7b8a97; font-size:12px; }}
"""


def document(title: str, png_b64: str | None, body: str) -> str:
    b = HtmlBuilder()
    glance = (f'<img class="glance" src="data:image/png;base64,{png_b64}" '
              f'alt="dashboard">' if png_b64 else "")
    return (f"<!DOCTYPE html>\n<html><head><meta charset='utf-8'>"
            f"<title>{b.esc(title)}</title><style>{_CSS}</style></head>"
            f"<body><h1>{b.esc(title)}</h1>{glance}{body}</body></html>")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/scout/test_report_html.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/scout/report/html.py tests/scout/test_report_html.py
git commit -m "feat(scout-report): zero-dep HTML builder with single esc() choke-point"
```

---

## Task 4: Sections (HTML + text)

**Files:**
- Create: `src/shortlist/scout/report/sections.py`
- Test: `tests/scout/test_report_sections.py`

> **Design:** the `_Research` section owns ALL Claude content in both HTML and text. The
> GLANCE text line shows `takeaway` (falling back to `bull_case`). This is what lets the
> back-compat `briefs` substring flow through one section (Task 5) without a `vm.briefs`
> field. Escaping: `_Research.render_html` interpolates only `esc()`'d prose into literal
> tags; tickers/company names flow through `tag`/`esc`.

- [ ] **Step 1: Write the failing test**

```python
# tests/scout/test_report_sections.py
from datetime import date
from shortlist.scout.report.viewmodel import (
    ReportVM, LeaderVM, MetricsVM, AssessmentVM, FunnelVM, SignalStatusVM)
from shortlist.scout.report.sections import render_html_body, render_text, Detail


def _leader(ticker, comp, assessment=None, gates=None, subs=None):
    return LeaderVM(ticker=ticker, name=None, composite=comp,
                    subscores=subs or {"quality": 70, "risk": None}, masked=set(),
                    gates=gates or [], flags=[], confidence=0.8, thin=False, scored=True,
                    coverage_note=None, metrics=MetricsVM(pe_ttm=30.0, target_upside=0.37),
                    assessment=assessment)


def _vm(leaders):
    return ReportVM(session=date(2026, 6, 4), leaders=leaders,
                    signals=[SignalStatusVM("edgar_form4", True, "2 clusters")],
                    funnel=FunnelVM(10, 8, 5, len(leaders), 1), notes=[])


def test_html_body_lists_every_leader_and_funnel():
    body = render_html_body(_vm([_leader("AAPL", 80), _leader("MSFT", 70)]))
    assert "AAPL" in body and "MSFT" in body
    assert "screened" in body and "edgar_form4" in body


def test_research_section_only_when_assessment_present():
    a = AssessmentVM(bull_case="AI demand", bear_case="Cyclical", red_flags=["going concern"])
    with_res = render_html_body(_vm([_leader("AAPL", 80, assessment=a)]))
    assert "AI demand" in with_res and "going concern" in with_res
    no_res = render_html_body(_vm([_leader("AAPL", 80)]))
    assert "AI demand" not in no_res


def test_html_escapes_injected_text_in_prose_and_ticker():
    a = AssessmentVM(bull_case="<script>alert(1)</script>")
    body = render_html_body(_vm([_leader("<b>AAPL</b>", 80, assessment=a)]))
    assert "<script>alert(1)</script>" not in body and "&lt;script&gt;" in body
    assert "<b>AAPL</b>" not in body and "&lt;b&gt;AAPL" in body


def test_text_glance_has_substring_contract():
    txt = render_text(_vm([_leader("AAPL", 80, gates=["negative_fcf"])]), Detail.GLANCE)
    assert "AAPL" in txt and "80" in txt
    assert "negative_fcf" in txt
    assert "screened" in txt and "edgar_form4" in txt


def test_text_glance_shows_research_takeaway():
    a = AssessmentVM(takeaway="Strong moat, fair price.")
    txt = render_text(_vm([_leader("AAPL", 80, assessment=a)]), Detail.GLANCE)
    assert "Strong moat" in txt


def test_all_none_subscores_render_without_crash():
    nones = {s: None for s in ["quality", "moat", "growth", "value", "momentum", "insider", "risk"]}
    body = render_html_body(_vm([_leader("BNK", 0.0, subs=nones)]))
    txt = render_text(_vm([_leader("BNK", 0.0, subs=nones)]), Detail.FULL)
    assert "BNK" in body and "BNK" in txt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scout/test_report_sections.py -v`
Expected: FAIL with `ModuleNotFoundError: shortlist.scout.report.sections`

- [ ] **Step 3: Write the implementation**

```python
# src/shortlist/scout/report/sections.py
"""Report sections: each renders itself to HTML and to text. New section = one class + registry line.

The PNG glance is deliberately NOT a section (raster layout does not compose); it reads
the view-model directly in png.py. HTML and text are the same sections at different Detail.
The _Research section owns ALL Claude content in both formats.
"""
from __future__ import annotations

import enum
from typing import Protocol

from .html import HtmlBuilder
from .theme import SUBS, SUB_LABELS, rgb_hex, text_on, score_to_rgb
from .viewmodel import ReportVM


class Detail(enum.Enum):
    GLANCE = "glance"   # terse, for the chunked Telegram text fallback
    FULL = "full"       # complete, for the on-disk .txt


class Section(Protocol):
    id: str
    title: str
    def applies(self, vm: ReportVM) -> bool: ...
    def render_html(self, vm: ReportVM, h: HtmlBuilder) -> str: ...
    def render_text(self, vm: ReportVM, detail: Detail) -> list[str]: ...


def _fmt(v, pct=False, money=False):
    if v is None:
        return "·"
    if money:
        return f"${v/1e9:.1f}B" if abs(v) >= 1e9 else f"${v/1e6:.0f}M"
    return f"{v*100:+.0f}%" if pct else f"{v:.1f}"


# ---- leaderboard ----
class _Leaderboard:
    id, title = "leaderboard", "Shortlist"

    def applies(self, vm): return bool(vm.leaders)

    def render_html(self, vm, h):
        rows = []
        for l in vm.leaders:
            cc = score_to_rgb(l.composite)
            cells = [h.tag("td", l.ticker, _class="k"),
                     h.raw("td", h.esc(f"{l.composite:.0f}"),
                           style=f"background:{rgb_hex(cc)};color:{rgb_hex(text_on(cc))}")]
            for s in SUBS:
                v = l.subscores.get(s)
                c = score_to_rgb(v)
                cells.append(h.raw("td", h.esc("·" if v is None else f"{v:.0f}"),
                                   style=f"background:{rgb_hex(c)};color:{rgb_hex(text_on(c))}"))
            cells.append(h.tag("td", ",".join(l.gates) if l.gates else "", _class="k"))
            rows.append(h.raw("tr", "".join(cells)))
        head = "".join(h.tag("th", x, _class="k") for x in
                       ["", "Comp"] + [SUB_LABELS[s] for s in SUBS] + ["Gates"])
        return h.raw("table", h.raw("tr", head) + "".join(rows))

    def render_text(self, vm, detail):
        out = []
        for i, l in enumerate(vm.leaders, 1):
            gate = f"  ⚠️ {', '.join(l.gates)}" if l.gates else ""
            mark = "" if l.scored else "  (not scored)"
            thin = "  (thin)" if l.thin else ""
            out.append(f"{i}. {l.ticker}  {l.composite:.1f}{gate}{mark}{thin}")
            subs = " ".join(
                f"{SUB_LABELS[s]}{'·' if l.subscores.get(s) is None else f'{l.subscores[s]:.0f}'}"
                for s in SUBS)
            out.append(f"   {subs}")
            if l.coverage_note:
                out.append(f"   ⊘ {l.coverage_note}")
        return out


# ---- per-leader fundamentals (HTML carries the full table; FULL text mirrors it) ----
_FUND_ROWS = [("Price", "price", {}), ("Mkt cap", "market_cap", {"money": True}),
              ("PE (ttm)", "pe_ttm", {}), ("PE 5y med", "pe_median_5y", {}),
              ("FCF yield", "fcf_yield", {"pct": True}), ("PEG", "peg", {}),
              ("ROE", "roe", {"pct": True}), ("ROIC", "roic", {"pct": True}),
              ("Gross mgn", "gross_margin", {"pct": True}), ("Net mgn", "net_margin", {"pct": True}),
              ("Debt/Eq", "debt_to_equity", {}), ("Rev CAGR", "revenue_cagr", {"pct": True}),
              ("EPS CAGR", "eps_cagr", {"pct": True}), ("vs 200dma", "price_vs_200dma", {"pct": True}),
              ("Rel str 6m", "rel_strength_6m", {"pct": True}), ("Volatility", "realized_vol", {"pct": True}),
              ("Max DD", "max_drawdown", {"pct": True}), ("Target upside", "target_upside", {"pct": True}),
              ("Insider 6m", "insider_net_6m", {"money": True})]


class _Fundamentals:
    id, title = "fundamentals", "Fundamentals"

    def applies(self, vm): return bool(vm.leaders)

    def render_html(self, vm, h):
        cards = []
        for l in vm.leaders:
            rows = [h.raw("tr", h.tag("td", label, _class="k") +
                          h.tag("td", _fmt(getattr(l.metrics, attr), **opt)))
                    for label, attr, opt in _FUND_ROWS]
            analysts = (f"{l.metrics.rating_buy or 0}B / {l.metrics.rating_hold or 0}H / "
                        f"{l.metrics.rating_sell or 0}S")
            rows.append(h.raw("tr", h.tag("td", "Analysts", _class="k") + h.tag("td", analysts)))
            cards.append(h.raw("div",
                               h.tag("h2", f"{l.ticker} — {l.composite:.0f}") +
                               h.raw("table", "".join(rows)), _class="card"))
        return "".join(cards)

    def render_text(self, vm, detail):
        if detail is Detail.GLANCE:
            return []
        out = []
        for l in vm.leaders:
            out.append(f"-- {l.ticker} metrics --")
            out += [f"   {label}: {_fmt(getattr(l.metrics, attr), **opt)}"
                    for label, attr, opt in _FUND_ROWS]
        return out


# ---- Claude research (owns ALL qualitative content) ----
class _Research:
    id, title = "research", "Research"

    def applies(self, vm): return any(l.assessment for l in vm.leaders)

    def render_html(self, vm, h):
        cards = []
        for l in vm.leaders:
            a = l.assessment
            if not a:
                continue
            parts = [h.tag("h2", f"{l.ticker} — analysis")]
            if a.business_model:
                parts.append(h.tag("p", a.business_model))
            if a.bull_case:
                parts.append(h.raw("p", "<b>Bull:</b> " + h.esc(a.bull_case), _class="bull"))
            if a.bear_case:
                parts.append(h.raw("p", "<b>Bear:</b> " + h.esc(a.bear_case), _class="bear"))
            for label, items, cls in [("Red flags", a.red_flags, "flag"),
                                      ("Risks", a.risks, "muted"),
                                      ("What would change my mind", a.change_my_mind, "muted")]:
                if items:
                    lis = "".join(h.tag("li", x) for x in items)
                    parts.append(h.raw("div", h.tag("b", label) + h.raw("ul", lis), _class=cls))
            if a.capital_allocation:
                parts.append(h.raw("p", "<b>Capital allocation:</b> " + h.esc(a.capital_allocation)))
            cards.append(h.raw("div", "".join(parts), _class="card"))
        return "".join(cards)

    def render_text(self, vm, detail):
        out = []
        for l in vm.leaders:
            a = l.assessment
            if not a:
                continue
            line = a.takeaway or a.bull_case
            out.append(f"📝 {l.ticker}: {line[:160]}" if line else f"📝 {l.ticker}")
            if detail is Detail.FULL and a.red_flags:
                out.append(f"   🚩 {'; '.join(a.red_flags)}")
        return out


# ---- footer: signals + funnel + notes ----
class _Footer:
    id, title = "footer", "Coverage"

    def applies(self, vm): return True

    def _sig(self, vm):
        return " · ".join(f"{s.name} {'✓' if s.ran else '✗'} ({s.detail})" for s in vm.signals)

    def _funnel(self, vm):
        f = vm.funnel
        return (f"{f.raw} raw → {f.after_dedup} deduped → {f.after_prefilter} after prefilter "
                f"→ {f.screened} screened ({f.dropped_for_budget} dropped: budget)")

    def render_html(self, vm, h):
        notes = "".join(h.tag("div", n, _class="muted") for n in vm.notes)
        return h.raw("div", h.tag("div", f"Signals: {self._sig(vm)}", _class="muted") +
                     h.tag("div", f"Funnel: {self._funnel(vm)}", _class="muted") + notes)

    def render_text(self, vm, detail):
        return ["", f"Signals: {self._sig(vm)}", f"Funnel: {self._funnel(vm)}"] + \
               [f"Note: {n}" for n in vm.notes]


SECTIONS: list[Section] = [_Leaderboard(), _Fundamentals(), _Research(), _Footer()]


def render_html_body(vm: ReportVM) -> str:
    h = HtmlBuilder()
    return "".join(s.render_html(vm, h) for s in SECTIONS if s.applies(vm))


def render_text(vm: ReportVM, detail: Detail) -> str:
    lines = [f"📊 Scout shortlist — session {vm.session.isoformat()}", ""]
    for s in SECTIONS:
        if s.applies(vm):
            lines += s.render_text(vm, detail)
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/scout/test_report_sections.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/scout/report/sections.py tests/scout/test_report_sections.py
git commit -m "feat(scout-report): section registry (HTML + text); research section owns Claude content"
```

---

## Task 5: Facade + migrate `render_message`

Delete the old `report.py` module; move `render_message` into the package facade,
reimplemented over sections. The GLANCE brief routes through a **synthesized assessment**
(`{"synthesis": brief}`) so the `_Research` section renders it — no `vm.briefs` field.
Existing `tests/scout/test_report.py` uses **substring** assertions; they must stay green.

**Files:**
- Delete: `src/shortlist/scout/report.py`
- Modify: `src/shortlist/scout/report/__init__.py`
- Test: `tests/scout/test_report_facade.py` (new) + existing `tests/scout/test_report.py` must stay green

- [ ] **Step 1: Write the failing test for the new facade**

```python
# tests/scout/test_report_facade.py
from datetime import date
from shortlist.models import ScoreCard, StockMetrics
from shortlist.scout.models import RunManifest, SignalStatus
import shortlist.scout.report as R
from shortlist.scout.report import build_report, ReportArtifacts


def _card(t, c, **kw):
    base = dict(ticker=t, composite=c, quality=70, moat=60, growth=50, momentum=80,
                value=40, opportunity=80, insider=55)
    base.update(kw)
    return ScoreCard(**base)


def _manifest():
    return RunManifest(session=date(2026, 6, 4),
                       signals=[SignalStatus("edgar_form4", True, "2 clusters")],
                       raw=5, after_dedup=4, after_prefilter=3, screened=1,
                       dropped_for_budget=0, researched=[])


def test_build_report_returns_html_and_text(monkeypatch):
    monkeypatch.setattr(R, "_render_png", lambda vm: None)   # force png-less path
    art = build_report([_card("AAPL", 80.0, metrics=StockMetrics(ticker="AAPL", price=100.0))],
                       _manifest(), assessments={})
    assert isinstance(art, ReportArtifacts)
    assert art.png is None
    assert art.html.startswith("<!DOCTYPE html>") and "AAPL" in art.html
    assert "AAPL" in art.text and "screened" in art.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scout/test_report_facade.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_report'` (the module `report.py` still shadows the package and has no `build_report`).

- [ ] **Step 3: Delete the old module and write the facade**

```bash
git rm src/shortlist/scout/report.py
```

```python
# src/shortlist/scout/report/__init__.py
"""Reporting layer facade. build_report() produces all artifacts; render_message() kept
for back-compat (demo + existing callers/tests)."""
from __future__ import annotations

import base64
from dataclasses import dataclass

from .sections import render_html_body, render_text, Detail
from .html import document
from .viewmodel import build_view_model

__all__ = ["build_report", "ReportArtifacts", "render_message"]


@dataclass
class ReportArtifacts:
    png: bytes | None
    html: str
    text: str       # FULL detail (on-disk .txt + journal fallback body)


def _render_png(vm):
    """Lazy bridge to the Pillow renderer; None if Pillow/renderer unavailable.
    This is the ONLY place Pillow is reached on the build path; demo never calls it."""
    try:
        from .png import render_glance
    except Exception:        # noqa: BLE001 — Pillow not installed
        return None
    try:
        return render_glance(vm)
    except Exception:        # noqa: BLE001 — never let chart break delivery
        return None


def build_report(cards, manifest, *, assessments: dict[str, dict]) -> ReportArtifacts:
    vm = build_view_model(cards, manifest, assessments=assessments)
    png = _render_png(vm)
    b64 = base64.b64encode(png).decode() if png else None
    title = f"Scout daily dashboard — {vm.session.isoformat()}"
    html = document(title, b64, render_html_body(vm))
    text = render_text(vm, Detail.FULL)
    return ReportArtifacts(png=png, html=html, text=text)


def render_message(cards, manifest, briefs: dict[str, str] | None = None) -> str:
    """Back-compat text renderer (Telegram GLANCE fallback + demo stdout). Briefs are
    rendered via the _Research section by synthesizing a minimal assessment each."""
    synth = {t: {"synthesis": b} for t, b in (briefs or {}).items()}
    vm = build_view_model(cards, manifest, assessments=synth)
    return render_text(vm, Detail.GLANCE)
```

- [ ] **Step 4: Run the new + existing report tests**

Run: `uv run pytest tests/scout/test_report_facade.py tests/scout/test_report.py -v`
Expected: PASS. The `test_report.py` substring contracts are satisfied by the sections:
ticker + `{composite:.1f}` (`"78"`), gate names, signal name + detail, `"15 screened"`
(footer funnel), `"(thin)"`, `"⊘"` + coverage note, and `"Strong moat"` via the synthesized
research takeaway. If any substring is missing, adjust the section wording (Task 4) — do NOT
weaken the test.

- [ ] **Step 5: Run the full scout suite to catch demo/manifest fallout**

Run: `uv run pytest tests/scout/ -q`
Expected: `test_report.py`, `test_digest_fallback.py`, `test_report_*` pass. `test_daily*` /
`test_fixes` / `test_orchestrator*` / `test_research_budget` may still reference the old
orchestrator and are fixed in Task 8 — note any failures but do not fix them here.

- [ ] **Step 6: Commit**

```bash
git add -A src/shortlist/scout/report tests/scout/test_report_facade.py
git commit -m "refactor(scout-report): facade + render_message over sections (briefs via synthesized assessment)"
```

---

## Task 6: PNG glance (Pillow)

**Files:**
- Create: `src/shortlist/scout/report/png.py`
- Test: `tests/scout/test_report_png.py`
- Modify: `pyproject.toml` (add `scout` extra)

> **Fonts:** the spec's "bundle a TTF" recommendation is relaxed to **system DejaVu with a
> `load_default` fallback** (target host is the Ubuntu VPS where DejaVu is present; tests
> assert PNG validity, not pixels, so glyph determinism is not required). The spec is
> amended to match. No binary font is vendored into the repo.

- [ ] **Step 1: Add the Pillow extra and sync**

In `pyproject.toml`, under `[project.optional-dependencies]`, add:

```toml
scout = ["pillow>=10.0"]
```

Run: `uv sync --extra scout --extra edgar`
Expected: Pillow installed into the venv.

- [ ] **Step 2: Write the failing test**

```python
# tests/scout/test_report_png.py
import io
import pytest
from datetime import date
from shortlist.scout.report.viewmodel import (
    ReportVM, LeaderVM, MetricsVM, FunnelVM, SignalStatusVM)

pytest.importorskip("PIL")
from PIL import Image
from shortlist.scout.report.png import render_glance

_ALL = ["quality", "moat", "growth", "value", "momentum", "insider", "risk"]


def _leader(t, c, subs=None):
    return LeaderVM(ticker=t, name=None, composite=c,
                    subscores=subs or {"quality": 90, "moat": None, "growth": 60, "value": 40,
                                       "momentum": 5, "insider": 50, "risk": 70},
                    masked=set(), gates=[], flags=[], confidence=0.8, thin=False,
                    scored=True, coverage_note=None, metrics=MetricsVM(), assessment=None)


def _vm(leaders):
    return ReportVM(session=date(2026, 6, 4), leaders=leaders,
                    signals=[SignalStatusVM("edgar_form4", True, "x")],
                    funnel=FunnelVM(len(leaders), len(leaders), len(leaders), len(leaders), 0),
                    notes=[])


def test_render_returns_valid_png_bytes():
    out = render_glance(_vm([_leader(f"T{i}", 80 - i) for i in range(6)]))
    assert isinstance(out, bytes) and out[:8] == b"\x89PNG\r\n\x1a\n"
    assert Image.open(io.BytesIO(out)).format == "PNG"


def test_height_scales_with_row_count():
    h3 = Image.open(io.BytesIO(render_glance(_vm([_leader(f"T{i}", 70) for i in range(3)])))).height
    h12 = Image.open(io.BytesIO(render_glance(_vm([_leader(f"T{i}", 70) for i in range(12)])))).height
    assert h12 > h3


def test_empty_renders_a_valid_card_not_a_crash():
    out = render_glance(_vm([]))
    assert Image.open(io.BytesIO(out)).format == "PNG"


def test_all_none_subscores_render(tmp_path):
    nones = {s: None for s in _ALL}
    out = render_glance(_vm([_leader("BNK", 0.0, subs=nones)]))
    assert Image.open(io.BytesIO(out)).format == "PNG"   # masked bank -> all gray, no crash
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/scout/test_report_png.py -v`
Expected: FAIL with `ModuleNotFoundError: shortlist.scout.report.png`

- [ ] **Step 4: Write the implementation**

```python
# src/shortlist/scout/report/png.py
"""Pillow raster of the curated glance (composite bars + sub-score heatmap). The ONLY
module that imports Pillow. Scales to any N; empty N renders an honest card."""
from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

from .theme import SUBS, SUB_LABELS, BG, FG, score_to_rgb, text_on
from .viewmodel import ReportVM

_W = 760           # target width (px); we supersample 2x then downscale
_ROW = 34          # px per row (per panel)
_CHROME = 150      # fixed px overhead (title + axis + padding)
_SS = 2            # supersample factor

_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def _font(size, bold=False):
    path = _FONT_PATHS[1] if bold else _FONT_PATHS[0]
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        try:
            return ImageFont.load_default(size=size)   # Pillow >= 10
        except TypeError:
            return ImageFont.load_default()


def _center(d, box, text, font, fill):
    l, t, r, b = d.textbbox((0, 0), text, font=font)
    x = box[0] + (box[2] - box[0] - (r - l)) / 2 - l
    y = box[1] + (box[3] - box[1] - (b - t)) / 2 - t
    d.text((x, y), text, font=font, fill=fill)


def render_glance(vm: ReportVM) -> bytes:
    n = len(vm.leaders)
    s = _SS
    if n == 0:
        img = Image.new("RGB", (_W * s, 120 * s), BG)
        d = ImageDraw.Draw(img)
        _center(d, (0, 0, _W * s, 120 * s),
                f"No candidates passed screening — {vm.session.isoformat()}",
                _font(15 * s, bold=True), FG)
        return _finish(img)

    height = _CHROME + 2 * _ROW * n
    img = Image.new("RGB", (_W * s, height * s), BG)
    d = ImageDraw.Draw(img)
    f_title = _font(15 * s, bold=True)
    f_lbl = _font(11 * s, bold=True)
    f_cell = _font(10 * s, bold=True)

    pad = 14 * s
    d.text((pad, 10 * s), f"Scout daily dashboard — {vm.session.isoformat()}",
           font=f_title, fill=FG)

    left = 70 * s                 # gutter for ticker labels
    plot_w = _W * s - left - pad

    # --- composite bars panel ---
    y0 = 44 * s
    d.text((pad, y0 - 18 * s), "Composite", font=f_lbl, fill=FG)
    for i, l in enumerate(vm.leaders):
        ry = y0 + i * _ROW * s
        d.text((pad, ry + 6 * s), l.ticker, font=f_lbl, fill=FG)
        bw = int(plot_w * max(0.0, min(100.0, l.composite)) / 100.0)
        col = score_to_rgb(l.composite)
        d.rectangle([left, ry + 3 * s, left + bw, ry + (_ROW - 6) * s], fill=col)
        d.text((left + bw + 6 * s, ry + 6 * s), f"{l.composite:.0f}", font=f_cell, fill=FG)

    # --- sub-score heatmap panel ---
    hy = y0 + n * _ROW * s + 24 * s
    d.text((pad, hy - 18 * s), "Sub-scores", font=f_lbl, fill=FG)
    cols = len(SUBS)
    cw = plot_w / cols
    for j, sub in enumerate(SUBS):
        cx = left + j * cw
        _center(d, (cx, hy - 16 * s, cx + cw, hy), SUB_LABELS[sub], f_cell, FG)
    for i, l in enumerate(vm.leaders):
        ry = hy + i * _ROW * s
        d.text((pad, ry + 8 * s), l.ticker, font=f_lbl, fill=FG)
        for j, sub in enumerate(SUBS):
            v = l.subscores.get(sub)
            cx = left + j * cw
            box = (cx + 1, ry + 1, cx + cw - 1, ry + _ROW * s - 1)
            col = score_to_rgb(v)
            d.rectangle(list(box), fill=col)
            _center(d, box, "·" if v is None else f"{v:.0f}", f_cell, text_on(col))

    return _finish(img)


def _finish(img: Image.Image) -> bytes:
    w, h = img.size
    img = img.resize((w // _SS, h // _SS), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/scout/test_report_png.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add src/shortlist/scout/report/png.py tests/scout/test_report_png.py pyproject.toml
git commit -m "feat(scout-report): Pillow glance renderer (bars + heatmap, scales to any N)"
```

---

## Task 7: Notifier transport + delivery policy

**Files:**
- Modify: `src/shortlist/scout/notify.py`
- Test: `tests/scout/test_notifier.py` (new); keep `tests/scout/test_notify.py` green

- [ ] **Step 1: Write the failing test**

```python
# tests/scout/test_notifier.py
import httpx
from shortlist.scout.notify import TelegramNotifier, deliver, DeliveryResult


def _client(seen, status=200):
    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(status, json={"ok": True})
    return httpx.Client(transport=httpx.MockTransport(handler))


def _body_client(bodies):
    def handler(request):
        bodies.append(request.read())
        return httpx.Response(200, json={"ok": True})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_configured_reflects_credentials():
    assert TelegramNotifier("T", "42").configured() is True
    assert TelegramNotifier(None, None).configured() is False


def test_send_message_chunks_and_preserves_content():
    bodies = []
    n = TelegramNotifier("T", "42", client=_body_client(bodies))
    assert n.send_message("x" * 9000) is True
    assert len(bodies) == 3                      # 4096 + 4096 + 808
    joined = "".join(b.decode() for b in bodies)
    assert joined.count("x") == 9000             # no characters lost across chunks


def test_send_message_empty_sends_nothing_but_succeeds():
    seen = []
    n = TelegramNotifier("T", "42", client=_client(seen))
    assert n.send_message("") is True and seen == []


def test_send_photo_and_document_hit_correct_endpoints():
    seen = []
    n = TelegramNotifier("T", "42", client=_client(seen))
    assert n.send_photo(b"\x89PNG", "cap") is True
    assert n.send_document(b"<html>", "r.html", "cap") is True
    assert any("/sendPhoto" in u for u in seen) and any("/sendDocument" in u for u in seen)


def test_caption_truncated_to_1024():
    bodies = []
    n = TelegramNotifier("T", "42", client=_body_client(bodies))
    n.send_photo(b"x", "y" * 5000)
    assert b"y" * 1025 not in bodies[0]           # caption capped


class _Fake:
    def __init__(self, configured=True, photo=True, doc=True, msg=True):
        self._c, self.photo, self.doc, self.msg = configured, photo, doc, msg
        self.calls = []
    def configured(self): return self._c
    def send_photo(self, png, cap): self.calls.append("photo"); return self.photo
    def send_document(self, data, fn, cap): self.calls.append("doc"); return self.doc
    def send_message(self, text): self.calls.append("msg"); return self.msg


def test_deliver_sequences_photo_then_document():
    f = _Fake()
    res = deliver(f, png=b"x", html="<h>", text="t", caption="c", session="2026-06-04")
    assert f.calls == ["photo", "doc"] and res.configured and res.all_ok


def test_deliver_doc_failure_falls_back_to_message():
    f = _Fake(doc=False)
    res = deliver(f, png=b"x", html="<h>", text="t", caption="c", session="x")
    assert "msg" in f.calls and not res.all_ok and "document" in " ".join(res.failures)


def test_deliver_photo_failure_still_sends_doc_and_message():
    f = _Fake(photo=False)
    res = deliver(f, png=b"x", html="<h>", text="t", caption="c", session="x")
    assert f.calls == ["photo", "doc", "msg"] and "photo" in res.failures


def test_deliver_unconfigured_does_nothing():
    f = _Fake(configured=False)
    res = deliver(f, png=None, html="<h>", text="t", caption="c", session="x")
    assert f.calls == [] and not res.configured and not res.all_ok
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scout/test_notifier.py -v`
Expected: FAIL with `ImportError: cannot import name 'TelegramNotifier'`

- [ ] **Step 3: Write the implementation (append to notify.py, keep `send_telegram`)**

```python
# append to src/shortlist/scout/notify.py
import time
from dataclasses import dataclass, field


_API = "https://api.telegram.org/bot{token}/{method}"
_MSG_CAP = 4096
_CAPTION_CAP = 1024


def _chunks(text: str, size: int):
    for i in range(0, len(text), size):
        yield text[i:i + size]


class TelegramNotifier:
    """One-shot Telegram transport. configured() is the exit-code discriminator.
    Each send returns bool and redacts its own exceptions (the URL embeds the token)."""

    def __init__(self, token: str | None = None, chat_id: str | None = None,
                 client: httpx.Client | None = None, max_retries: int = 2) -> None:
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        self._client = client
        self.max_retries = max_retries

    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def _post(self, method: str, **kwargs) -> bool:
        if not self.configured():
            return False
        c = self._client or httpx.Client(timeout=30.0)
        url = _API.format(token=self.token, method=method)
        try:
            for attempt in range(self.max_retries + 1):
                resp = c.post(url, **kwargs)
                if resp.status_code == 429 and attempt < self.max_retries:
                    # Retry-After-aware backoff, mirroring FMPProvider._get's 429 idiom.
                    delay = float(resp.headers.get("Retry-After", 2 ** attempt))
                    time.sleep(min(delay, 30.0))
                    continue
                return resp.status_code == 200
            return False
        except Exception as e:  # noqa: BLE001
            print(f"telegram {method} failed: {redact_secrets(str(e))}")
            return False
        finally:
            if self._client is None:
                c.close()

    def send_message(self, text: str) -> bool:
        ok = True
        for chunk in _chunks(text, _MSG_CAP):
            ok = self._post("sendMessage", json={"chat_id": self.chat_id, "text": chunk}) and ok
        return ok

    def send_photo(self, png: bytes, caption: str = "") -> bool:
        return self._post("sendPhoto",
                          data={"chat_id": self.chat_id, "caption": caption[:_CAPTION_CAP]},
                          files={"photo": ("dashboard.png", png, "image/png")})

    def send_document(self, data: bytes, filename: str, caption: str = "") -> bool:
        return self._post("sendDocument",
                          data={"chat_id": self.chat_id, "caption": caption[:_CAPTION_CAP]},
                          files={"document": (filename, data, "text/html")})


@dataclass
class DeliveryResult:
    configured: bool
    all_ok: bool
    failures: list[str] = field(default_factory=list)


def deliver(notifier, *, png: bytes | None, html: str, text: str, caption: str,
            session: str) -> DeliveryResult:
    """Policy: photo (if any) then document; fall back to a text message on any failure."""
    if not notifier.configured():
        return DeliveryResult(configured=False, all_ok=False)
    failures: list[str] = []
    if png is not None and not notifier.send_photo(png, caption):
        failures.append("photo")
    if not notifier.send_document(html.encode("utf-8"), f"scout-{session}.html", caption):
        failures.append("document")
    if failures and not notifier.send_message(text):
        failures.append("message")
    return DeliveryResult(configured=True, all_ok=not failures, failures=failures)
```

- [ ] **Step 4: Run new + existing transport tests**

Run: `uv run pytest tests/scout/test_notifier.py tests/scout/test_notify.py -v`
Expected: PASS (both files).

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/scout/notify.py tests/scout/test_notifier.py
git commit -m "feat(scout-notify): TelegramNotifier (photo/document/chunked msg, Retry-After) + deliver()"
```

---

## Task 8: Wire the orchestrator (+ fix back-compat tests)

This is the highest-risk task. It rewires `run()`, widens `_research_phase` to a 4-tuple
(**all six return sites**), changes the artifact layout to `scout/<date>/`, and updates
**three existing test files** that depend on the old shape. Do the test edits in the same
task so the suite is green at commit.

**Files:**
- Modify: `src/shortlist/scout/daily.py`
- Modify: `config.yaml`
- Modify: `tests/scout/test_research_budget.py`, `tests/scout/test_fixes.py`, `tests/scout/test_orchestrator_integration.py`
- Test: `tests/scout/test_orchestrator_reporting.py` (new)

- [ ] **Step 1: Add the config block**

In `config.yaml` under `scout:` (after `artifact_dir: scout`), add:

```yaml
  report:
    chart: true            # render + send the PNG glance
    attach_html: true      # send the HTML deep-dive as a document
    caption_top_n: 3       # names listed in the photo caption
```

- [ ] **Step 2: Write the failing integration test**

```python
# tests/scout/test_orchestrator_reporting.py
import json
from datetime import date
from pathlib import Path
import yaml
import shortlist.scout.daily as daily

_CONFIG = yaml.safe_load((Path(__file__).resolve().parents[2] / "config.yaml").read_text())


def _cfg(tmp_path):
    cfg = dict(_CONFIG)                       # real thresholds/scoring/gates from config.yaml
    cfg["scout"] = dict(cfg.get("scout", {}))
    cfg["scout"].update(artifact_dir="scout", state_path="state/s.json")
    return cfg


def test_demo_run_prints_text_and_no_pillow(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc = daily.run(_cfg(tmp_path), demo=True, today=date(2026, 6, 4))
    assert rc == 0
    assert "Scout shortlist" in capsys.readouterr().out


def test_assessment_record_loader_reads_json(tmp_path):
    rec = {"business_model_summary": "Chips.", "thesis": {"bull_case": "AI"}}
    p = tmp_path / "AAPL" / "abc.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps(rec))
    md = str(p).replace(".json", ".md")
    assert daily._assessment_record_from_file(md)["thesis"]["bull_case"] == "AI"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/scout/test_orchestrator_reporting.py -v`
Expected: FAIL — `_assessment_record_from_file` does not exist (`AttributeError`).

- [ ] **Step 4: Add the loader and widen `_research_phase` to a 4-tuple (all six returns)**

In `daily.py`, add next to `_one_line_brief_from_file`:

```python
def _assessment_record_from_file(brief_path) -> dict | None:
    """Read the full QualitativeAssessment record (JSON) report.write() saved next to the .md."""
    try:
        json_path = Path(str(brief_path).replace(".md", ".json"))
        return json.loads(json_path.read_text())
    except Exception:  # noqa: BLE001
        return None
```

In `_research_phase`, change the signature to return a 4-tuple and update **every** return
statement. There are **six** return sites — update all of them:

1. Kill-switch return → `return {}, {}, [], "research skipped: kill-switch"`
2. Layer-import-failure return → `return {}, {}, [], "research skipped: layer unavailable"`
3. Not-available return → `return {}, {}, [], "research skipped: claude CLI / edgartools not available"`
4. Timeout return → `return {}, {}, [], f"research skipped: phase budget {budget_s}s exceeded"`
5. Exception return → `return {}, {}, [], f"research failed: {redact_secrets(str(e))}"`
6. The final success return — replace the briefs-building tail with:

```python
    briefs: dict[str, str] = {}
    assessments: dict[str, dict] = {}
    researched: list[str] = []
    for r in results:
        if r.skipped:
            continue
        researched.append(r.ticker)
        brief_text = r.synthesis if r.synthesis else _one_line_brief_from_file(r.brief_path)
        briefs[r.ticker] = brief_text[:200]
        rec = _assessment_record_from_file(r.brief_path)
        if rec:
            assessments[r.ticker] = rec
    return briefs, assessments, researched, None
```

Also update the signature line to:

```python
def _research_phase(cards, config, scout_cfg, *, _is_available=None, _enrich=None
                    ) -> tuple[dict, dict, list, str | None]:
```

- [ ] **Step 5: Update `test_research_budget.py` (4 call sites) to the 4-tuple**

In `tests/scout/test_research_budget.py`, every call `briefs, researched, note = _research_phase(...)`
becomes `briefs, assessments, researched, note = _research_phase(...)`. There are 4 such
unpackings (one per test). Leave the existing assertions on `briefs`/`researched`/`note`
unchanged; no new assertions required.

Run: `uv run pytest tests/scout/test_research_budget.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Rewire `run()` — demo prints text (no Pillow); non-demo builds + delivers**

In `daily.py run()`, replace the research-call + message/deliver/persist block (currently
~lines 150–187) with:

```python
    # 3. Auto-research (guardrailed) — skipped in demo
    briefs: dict[str, str] = {}
    assessments: dict[str, dict] = {}
    researched: list[str] = []
    notes: list[str] = []
    if not demo:
        briefs, assessments, researched, note = _research_phase(cards, config, scout_cfg)
        if note:
            notes.append(note)

    manifest = RunManifest(
        session=session, signals=statuses, raw=raw, after_dedup=after_dedup,
        after_prefilter=after_prefilter, screened=len(cards), dropped_for_budget=dropped,
        researched=researched, notes=notes)

    # 4a. Demo: print the GLANCE text and stop — never touches Pillow / network.
    if demo:
        from .report import render_message
        print(render_message(cards, manifest, briefs))
        return 0

    # 4b. Live: build artifacts, deliver, persist.
    from .report import build_report
    from .notify import TelegramNotifier, deliver
    rep_cfg = scout_cfg.get("report", {})
    artifacts = build_report(cards, manifest, assessments=assessments)
    caption = _caption(manifest, cards, rep_cfg.get("caption_top_n", 3))

    notifier = TelegramNotifier()
    result = deliver(notifier,
                     png=artifacts.png if rep_cfg.get("chart", True) else None,
                     html=artifacts.html, text=artifacts.text, caption=caption,
                     session=session.isoformat())
    if not result.configured:
        print(artifacts.text)  # journal fallback
    if result.configured and not result.all_ok:
        manifest.notes.append("telegram delivery failed (configured)")
    _persist(scout_cfg, manifest, artifacts)
    state.mark_run_completed(session)
    state.record_screened([c.ticker for c in cards], session)
    if result.configured and not result.all_ok:
        return 2
    return 0
```

> The `from .notify import TelegramNotifier, deliver` is function-local so tests can
> monkeypatch `shortlist.scout.notify.TelegramNotifier` and have `run()` pick up the fake.

Add the caption + persist helpers (replace `_write_manifest`):

```python
def _caption(manifest, cards, top_n: int) -> str:
    ordered = sorted(cards, key=lambda c: (getattr(c, "scored", True), c.composite),
                     reverse=True)
    top = " · ".join(f"{c.ticker} {c.composite:.0f}" for c in ordered[:top_n])
    return (f"Scout — {manifest.session.isoformat()}\nTop: {top}\n"
            f"{manifest.screened} screened from {manifest.raw} raw")[:1024]


def _persist(scout_cfg, manifest, artifacts) -> None:
    out_dir = Path(scout_cfg.get("artifact_dir", "scout")) / manifest.session.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(json.dumps(manifest.to_dict(), indent=2))
    (out_dir / "report.txt").write_text(artifacts.text)
    (out_dir / "report.html").write_text(artifacts.html)
    if artifacts.png is not None:
        (out_dir / "dashboard.png").write_bytes(artifacts.png)
```

Remove the old `from .report import render_message` top-of-`run` import (if present), the old
`send_telegram` call, and the `_write_manifest` function. `render_message` is still importable
from the facade for the demo branch above and any other caller.

- [ ] **Step 7: Update `test_fixes.py` — Telegram patch + artifact globs**

`run()` no longer calls `send_telegram`; it builds `TelegramNotifier()` and `deliver()`.
And artifacts now live under `scout/<date>/`. Make these concrete edits:

a) **Replace the `send_telegram` monkeypatches with a fake notifier.** Add this helper near
the top of `tests/scout/test_fixes.py`:

```python
class _FakeNotifier:
    def __init__(self, configured=True, ok=True):
        self._c, self._ok = configured, ok
    def configured(self): return self._c
    def send_photo(self, *a): return self._ok
    def send_document(self, *a): return self._ok
    def send_message(self, *a): return self._ok
```

Then in each test that currently does
`monkeypatch.setattr(notify_mod, "send_telegram", lambda msg: <bool>)`:
- For the **unconfigured** test: `monkeypatch.setattr(notify_mod, "TelegramNotifier", lambda: _FakeNotifier(configured=False))`.
- For the **configured-but-fails** test: `monkeypatch.setattr(notify_mod, "TelegramNotifier", lambda: _FakeNotifier(configured=True, ok=False))` and **remove** the `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` env setup (configured-ness now comes from the fake, not env — this also removes the real-network risk).

b) **Fix the artifact-path globs.** Every `list((tmp_path / "scout").glob("*.json"))[0]` →
`list((tmp_path / "scout").glob("*/manifest.json"))[0]`, and every `glob("*.txt")[0]` →
`glob("*/report.txt")[0]`. (5 sites: the manifest-content checks and the rendered-report check.)

Run: `uv run pytest tests/scout/test_fixes.py -v`
Expected: PASS. The `configured-but-fails` test still asserts `rc == 2` and the
"telegram delivery failed (configured)" manifest note; the unconfigured test still asserts `rc == 0`.

- [ ] **Step 8: Update `test_orchestrator_integration.py` — dead `send_telegram` patch**

In `tests/scout/test_orchestrator_integration.py`, replace
`monkeypatch.setattr(notify_mod, "send_telegram", ...)` with
`monkeypatch.setattr(notify_mod, "TelegramNotifier", lambda: _FakeNotifier(configured=False))`
(define the same `_FakeNotifier` locally or import it). This keeps the run unconfigured so it
prints + returns 0 without a network call, matching the test's intent.

Run: `uv run pytest tests/scout/test_orchestrator_integration.py -v`
Expected: PASS.

- [ ] **Step 9: Run the new orchestrator test, the full scout suite, then everything**

Run: `uv run pytest tests/scout/test_orchestrator_reporting.py -v`
Expected: PASS (2 tests).

Run: `uv run pytest tests/scout/ -q`
Expected: PASS (entire scout suite green).

Run: `uv run pytest -q`
Expected: PASS (no regressions elsewhere).

- [ ] **Step 10: Commit**

```bash
git add src/shortlist/scout/daily.py config.yaml tests/scout/test_orchestrator_reporting.py \
        tests/scout/test_research_budget.py tests/scout/test_fixes.py \
        tests/scout/test_orchestrator_integration.py
git commit -m "feat(scout): wire reporting artifacts + TelegramNotifier delivery; migrate back-compat tests"
```

---

## Task 9: Smoke, Pillow-absent check, docs

**Files:**
- Modify: `CLAUDE.md`, `docs/NOTIFICATIONS.md`, `docs/AUTONOMOUS_SCOUT.md`

- [ ] **Step 1: Demo smoke**

Run: `uv run shortlist-scout --demo`
Expected: prints a text report to stdout, exit 0, no traceback.

- [ ] **Step 2: Prove the lazy-import contract holds (spec risk #2)**

Run (normal venv, Pillow installed — the point is that importing the report package must
NOT load PIL; Pillow is only reached lazily inside `_render_png`):
```bash
uv run python -c "import sys; import shortlist.scout.report; import shortlist.scout.daily; \
assert 'PIL' not in sys.modules, 'Pillow imported on the report/daily path!'; \
print('OK: report+daily import with no PIL loaded')"
```
Expected: prints OK. (Confirms `report/__init__.py` → sections/html/viewmodel/theme and the
demo path in `daily.py` pull in no PIL at import time.)

- [ ] **Step 3: Generate a real HTML+PNG artifact to eyeball**

Run:
```bash
uv run python -c "from datetime import date; from shortlist.models import ScoreCard, StockMetrics; \
from shortlist.scout.models import RunManifest, SignalStatus; from shortlist.scout.report import build_report; \
c=ScoreCard(ticker='NVDA', composite=79, quality=100, moat=100, growth=100, value=74, momentum=59, \
opportunity=74, insider=41, risk=55, metrics=StockMetrics(ticker='NVDA', price=100, target_median=137)); \
m=RunManifest(session=date(2026,6,4), signals=[SignalStatus('edgar_form4',True,'2')], raw=5, after_dedup=4, \
after_prefilter=3, screened=1, dropped_for_budget=0); a=build_report([c], m, assessments={}); \
open('scratch/smoke.html','w').write(a.html); open('scratch/smoke.png','wb').write(a.png or b''); \
print('wrote scratch/smoke.{html,png}; png bytes:', len(a.png or b''))"
```
Expected: writes the files; open `scratch/smoke.html` in a browser to verify the embedded
chart + tables render.

- [ ] **Step 4: Update docs**

- `CLAUDE.md`: in the Scout bullet, note (a) the new `report/` package (view-model →
  section-registry → HTML/text/PNG renderers; add a section = one class + one `SECTIONS`
  entry), (b) the hard rule **"Pillow is lazy-imported only in `report/png.py`; never import
  it from viewmodel/sections/html/theme"**, (c) the `scout` extra (`uv sync --extra scout`),
  (d) artifacts now under `scout/<date>/` (dashboard.png, report.html, report.txt, manifest.json).
- `docs/NOTIFICATIONS.md`: mark §3 hardening (chunking, retry/`Retry-After`, `Notifier`
  seam, photo/document delivery) as **implemented**; link this plan + the spec.
- `docs/AUTONOMOUS_SCOUT.md`: change the delivery description from "text Telegram report"
  to "chart (sendPhoto) + HTML deep-dive (sendDocument), artifacts under `scout/<date>/`."

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md docs/NOTIFICATIONS.md docs/AUTONOMOUS_SCOUT.md
git commit -m "docs(scout): reporting + notification layer shipped; lazy-Pillow rule + delivery semantics"
```

---

## Self-review notes (for the implementer)

- **Spec coverage:** §3 architecture → Tasks 2/4/6; §4 module layout → all tasks; §5 view-model
  → Task 2; §6 HTML → Tasks 3/4/5; §7 PNG → Task 6 (font rule relaxed to system+fallback,
  spec amended); §8 Notifier+deliver → Task 7; §9 orchestration → Task 8; §10 persistence →
  Task 8 `_persist`; §11 config → Task 8; §12 deps → Task 6; §13 degradation → facade
  `_render_png` (Task 5) + `deliver` unconfigured (Task 7) + empty-N PNG (Task 6) + demo
  no-Pillow (Task 8/9); §14 testing → every task; §15 build order = task order; §16 risks →
  text substring-compat (Task 5 Step 4) + briefs-via-synthesized-assessment (one section),
  Pillow-not-hard-dep (facade try/except + Task 9 Step 2 check), HTML injection (Task 3/4
  escaping tests incl. ticker).
- **Back-compat anchors:** `render_message` (facade) + `send_telegram` (notify.py) stay
  exported; `state.mark_run_completed` BEFORE `record_screened`; exit 0 unconfigured / exit 2
  configured-but-failed (Task 8 Step 6). Three existing test files migrated in-task (Task 8
  Steps 5/7/8): `test_research_budget` (4-tuple), `test_fixes` (fake notifier + `*/manifest.json`
  globs), `test_orchestrator_integration` (fake notifier).
- **Type consistency:** `build_view_model(cards, manifest, *, assessments)` (no briefs/config);
  `build_report(cards, manifest, *, assessments) -> ReportArtifacts(png,html,text)`;
  `deliver(...) -> DeliveryResult(configured, all_ok, failures)`; `_research_phase` returns
  `(briefs, assessments, researched, note)` with all six return sites updated; `AssessmentVM`
  has `takeaway`; `MetricsVM.target_upside` sourced from `StockMetrics.upside_to_target`.
```

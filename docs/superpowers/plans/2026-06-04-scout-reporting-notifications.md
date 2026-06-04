# Scout Reporting + Notification Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `shortlist-scout`'s daily run a real reporting + delivery layer — an inline chart image, a styled HTML deep-dive, and hardened Telegram delivery — without a heavyweight charting dependency.

**Architecture:** A thin renderer-agnostic view-model owns the 3-tier (ScoreCard / StockMetrics / QualitativeAssessment) join and None semantics once. A section registry renders HTML and text from it; the PNG glance is a separate, deliberately limited Pillow view. A `Notifier` transport seam (httpx-injected) handles `sendPhoto`/`sendDocument`/`sendMessage` with chunking + retry; a `deliver()` policy preserves exit-code semantics.

**Tech Stack:** Python 3.12, Pillow (raster glance, lazy-imported scout extra), pure HTML/CSS (zero-dep), httpx (Telegram), pytest. matplotlib/numpy are **not** used.

**Spec:** `docs/superpowers/specs/2026-06-04-scout-reporting-notifications-design.md`

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

> **Migration note:** `src/shortlist/scout/report.py` becomes `src/shortlist/scout/report/__init__.py`. Move the existing `render_message`/`_n` into the package facade in Task 5; until then leave the old file in place so the suite stays green.

---

## Task 1: Theme (palette + colormap)

**Files:**
- Create: `src/shortlist/scout/report/__init__.py` (empty for now — makes it a package)
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

- [ ] **Step 1: Write the failing test**

```python
# tests/scout/test_report_viewmodel.py
from datetime import date
from shortlist.models import ScoreCard, StockMetrics, Coverage
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
    cards = [_card("LOW", 40.0), _card("HIGH", 90.0), _card("NS", 99.0, scored=False)]
    vm = build_view_model(cards, _manifest(), briefs={}, assessments={}, config={})
    assert [l.ticker for l in vm.leaders] == ["HIGH", "LOW", "NS"]  # scored desc, then composite


def test_target_upside_derived_from_metrics():
    m = StockMetrics(ticker="AAPL", price=100.0, target_median=137.0)
    vm = build_view_model([_card("AAPL", 80.0, metrics=m)], _manifest(),
                          briefs={}, assessments={}, config={})
    assert abs(vm.leaders[0].metrics.target_upside - 0.37) < 1e-6


def test_target_upside_none_when_no_price():
    m = StockMetrics(ticker="AAPL", price=None, target_median=137.0)
    vm = build_view_model([_card("AAPL", 80.0, metrics=m)], _manifest(),
                          briefs={}, assessments={}, config={})
    assert vm.leaders[0].metrics.target_upside is None


def test_assessment_present_only_for_researched():
    rec = {"business_model_summary": "Chips.",
           "thesis": {"bull_case": "AI demand", "bear_case": "Cyclical",
                      "what_would_change_my_mind": ["margin compression"]},
           "risks": [{"claim": "China export limits"}],
           "red_flags": [], "management_capital_allocation": "Buybacks"}
    cards = [_card("AAPL", 80.0), _card("MSFT", 70.0)]
    vm = build_view_model(cards, _manifest(), briefs={},
                          assessments={"AAPL": rec}, config={})
    a = {l.ticker: l for l in vm.leaders}
    assert a["AAPL"].assessment.bull_case == "AI demand"
    assert a["AAPL"].assessment.risks == ["China export limits"]
    assert a["MSFT"].assessment is None


def test_funnel_and_subscores_carried():
    vm = build_view_model([_card("AAPL", 80.0)], _manifest(),
                          briefs={}, assessments={}, config={})
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
    target_upside: float | None = None   # derived: target_median/price - 1
    insider_net_6m: float | None = None
    insider_distinct_buyers: int | None = None


@dataclass
class AssessmentVM:
    business_model: str = ""
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
    upside = None
    if m.target_median is not None and m.price not in (None, 0):
        upside = m.target_median / m.price - 1.0
    return MetricsVM(
        price=m.price, market_cap=m.market_cap, pe_ttm=m.pe_ttm,
        pe_median_5y=m.pe_median_5y, fcf_yield=m.fcf_yield, peg=m.peg, roe=m.roe,
        roic=m.roic, gross_margin=m.gross_margin, net_margin=m.net_margin,
        debt_to_equity=m.debt_to_equity, revenue_cagr=m.revenue_cagr, eps_cagr=m.eps_cagr,
        price_vs_200dma=m.price_vs_200dma, rel_strength_6m=m.rel_strength_6m,
        realized_vol=m.realized_vol, max_drawdown=m.max_drawdown,
        rating_buy=m.rating_buy, rating_hold=m.rating_hold, rating_sell=m.rating_sell,
        target_upside=upside, insider_net_6m=m.insider_net_6m,
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


def build_view_model(cards, manifest: RunManifest, *, briefs: dict[str, str],
                     assessments: dict[str, dict], config: dict) -> ReportVM:
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

> `briefs` is accepted for signature symmetry with the facade; the HTML/text use the
> richer `assessment`, so briefs is currently unused here. Keep the parameter.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/scout/test_report_viewmodel.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/scout/report/viewmodel.py tests/scout/test_report_viewmodel.py
git commit -m "feat(scout-report): renderer-agnostic view-model (3-tier join, target-upside, sort)"
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


def test_tag_escapes_text_content():
    h = HtmlBuilder()
    assert h.tag("td", "A & B") == "<td>A &amp; B</td>"
    assert h.tag("td", "x", style="color:red") == '<td style="color:red">x</td>'


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

- [ ] **Step 1: Write the failing test**

```python
# tests/scout/test_report_sections.py
from datetime import date
from shortlist.scout.report.viewmodel import (
    ReportVM, LeaderVM, MetricsVM, AssessmentVM, FunnelVM, SignalStatusVM)
from shortlist.scout.report.sections import SECTIONS, render_html_body, render_text, Detail


def _leader(ticker, comp, assessment=None, gates=None):
    return LeaderVM(ticker=ticker, name=None, composite=comp,
                    subscores={"quality": 70, "risk": None}, masked=set(),
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


def test_html_escapes_injected_text():
    a = AssessmentVM(bull_case="<script>alert(1)</script>")
    body = render_html_body(_vm([_leader("AAPL", 80, assessment=a)]))
    assert "<script>alert(1)</script>" not in body and "&lt;script&gt;" in body


def test_text_glance_has_substring_contract():
    txt = render_text(_vm([_leader("AAPL", 80, gates=["negative_fcf"])]), Detail.GLANCE)
    assert "AAPL" in txt and "80" in txt
    assert "negative_fcf" in txt
    assert "screened" in txt and "edgar_form4" in txt
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
"""
from __future__ import annotations

import enum
from typing import Protocol

from .html import HtmlBuilder
from .theme import SUBS, SUB_LABELS, rgb_hex, text_on, score_to_rgb
from .viewmodel import ReportVM, LeaderVM


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
            cells = [h.tag("td", l.ticker, _class="k")]
            cells.append(h.raw("td", h.esc(f"{l.composite:.0f}"),
                               style=f"background:{rgb_hex(score_to_rgb(l.composite))};"
                                     f"color:{rgb_hex(text_on(score_to_rgb(l.composite)))}"))
            for s in SUBS:
                v = l.subscores.get(s)
                c = score_to_rgb(v)
                cells.append(h.raw("td", h.esc("·" if v is None else f"{v:.0f}"),
                                   style=f"background:{rgb_hex(c)};color:{rgb_hex(text_on(c))}"))
            gate = h.tag("td", ",".join(l.gates) if l.gates else "", _class="k")
            rows.append(h.raw("tr", "".join(cells) + gate))
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
            subs = " ".join(f"{SUB_LABELS[s]}{'·' if l.subscores.get(s) is None else f'{l.subscores[s]:.0f}'}"
                            for s in SUBS)
            out.append(f"   {subs}")
            if l.coverage_note:
                out.append(f"   ⊘ {l.coverage_note}")
        return out


# ---- per-leader fundamentals (HTML only carries the full table; text stays terse) ----
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
            analysts = f"{l.metrics.rating_buy or 0}B / {l.metrics.rating_hold or 0}H / {l.metrics.rating_sell or 0}S"
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


# ---- Claude research ----
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
            out.append(f"📝 {l.ticker}: {a.bull_case[:160]}" if a.bull_case else f"📝 {l.ticker}")
            if detail is Detail.FULL and a.red_flags:
                out.append(f"   🚩 {'; '.join(a.red_flags)}")
        return out


# ---- footer: signals + funnel + notes ----
class _Footer:
    id, title = "footer", "Coverage"

    def applies(self, vm): return True

    def render_html(self, vm, h):
        sig = " · ".join(f"{s.name} {'✓' if s.ran else '✗'} ({s.detail})" for s in vm.signals)
        f = vm.funnel
        funnel = (f"{f.raw} raw → {f.after_dedup} deduped → {f.after_prefilter} "
                  f"after prefilter → {f.screened} screened ({f.dropped_for_budget} dropped: budget)")
        notes = "".join(h.tag("div", n, _class="muted") for n in vm.notes)
        return (h.raw("div", h.tag("div", f"Signals: {sig}", _class="muted") +
                      h.tag("div", f"Funnel: {funnel}", _class="muted") + notes))

    def render_text(self, vm, detail):
        f = vm.funnel
        out = ["", "Signals: " + " · ".join(
            f"{s.name} {'✓' if s.ran else '✗'} ({s.detail})" for s in vm.signals)]
        out.append(f"Funnel: {f.raw} raw → {f.after_dedup} deduped → {f.after_prefilter} "
                   f"after prefilter → {f.screened} screened ({f.dropped_for_budget} dropped: budget)")
        out += [f"Note: {n}" for n in vm.notes]
        return out


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
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/scout/report/sections.py tests/scout/test_report_sections.py
git commit -m "feat(scout-report): section registry rendering HTML + text from the view-model"
```

---

## Task 5: Facade + migrate `render_message`

This deletes the old `report.py` module and moves `render_message` into the package facade,
reimplemented over sections. Existing `tests/scout/test_report.py` uses **substring**
assertions — they must keep passing.

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
    # Force the png renderer to be unavailable so the facade degrades to png=None.
    import shortlist.scout.report as R
    monkeypatch.setattr(R, "_render_png", lambda vm: None)
    art = build_report([_card("AAPL", 80.0, metrics=StockMetrics(ticker="AAPL", price=100.0))],
                       _manifest(), briefs={}, assessments={}, config={})
    assert isinstance(art, ReportArtifacts)
    assert art.png is None
    assert art.html.startswith("<!DOCTYPE html>") and "AAPL" in art.html
    assert "AAPL" in art.text and "screened" in art.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scout/test_report_facade.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_report'`

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
    """Lazy bridge to the Pillow renderer; None if Pillow/renderer unavailable."""
    try:
        from .png import render_glance
    except Exception:        # noqa: BLE001 — Pillow not installed
        return None
    try:
        return render_glance(vm)
    except Exception:        # noqa: BLE001 — never let chart break delivery
        return None


def build_report(cards, manifest, *, briefs, assessments, config) -> ReportArtifacts:
    vm = build_view_model(cards, manifest, briefs=briefs, assessments=assessments, config=config)
    png = _render_png(vm)
    b64 = base64.b64encode(png).decode() if png else None
    title = f"Scout daily dashboard — {vm.session.isoformat()}"
    html = document(title, b64, render_html_body(vm))
    text = render_text(vm, Detail.FULL)
    return ReportArtifacts(png=png, html=html, text=text)


def render_message(cards, manifest, briefs: dict[str, str] | None = None) -> str:
    """Back-compat text renderer (Telegram GLANCE fallback + demo stdout)."""
    vm = build_view_model(cards, manifest, briefs=briefs or {}, assessments={}, config={})
    return render_text(vm, Detail.GLANCE)
```

- [ ] **Step 4: Run the new + existing report tests**

Run: `uv run pytest tests/scout/test_report_facade.py tests/scout/test_report.py -v`
Expected: PASS. If a substring assertion in `test_report.py` fails, adjust the section
text wording (Task 4 `_Leaderboard`/`_Footer`) so the substring is present — do NOT weaken
the test. The contract substrings are: ticker, composite int, gate name, signal name +
detail, `"N screened"`, the brief text, `"(thin)"`, `"⊘"` + coverage note.

> The brief substring (`"Strong moat"`) in `test_report.py::test_message_lists_ranked_names_and_signal_coverage`
> comes through `briefs`. Since the GLANCE text now renders from sections (which read
> `assessment`, not `briefs`), add a minimal briefs fallback: in `_Research.render_text`,
> when `l.assessment is None`, the facade has no brief. To preserve that one test, have
> `render_message` inject briefs as single-line research: pass briefs into the view-model
> and render them. Implement by extending `_Leaderboard.render_text` to append
> `   📝 {brief}` when `vm` carries a brief for the ticker. Store briefs on the VM:

Add to `ReportVM` (Task 2 `viewmodel.py`) a field `briefs: dict[str, str] = field(default_factory=dict)`
and set it in `build_view_model` from the `briefs` arg. Then in `_Leaderboard.render_text`,
after the sub-score line:

```python
            if vm.briefs.get(l.ticker):
                out.append(f"   📝 {vm.briefs[l.ticker]}")
```

- [ ] **Step 5: Run the full scout suite to catch demo/manifest fallout**

Run: `uv run pytest tests/scout/ -q`
Expected: PASS. If `test_daily_demo.py` or `test_orchestrator_integration.py` assert exact
text, reconcile by adjusting section wording (keep their substrings present).

- [ ] **Step 6: Commit**

```bash
git add -A src/shortlist/scout/report tests/scout/test_report_facade.py
git commit -m "refactor(scout-report): facade + render_message over sections (substring-compatible)"
```

---

## Task 6: PNG glance (Pillow)

**Files:**
- Create: `src/shortlist/scout/report/png.py`
- Test: `tests/scout/test_report_png.py`
- Modify: `pyproject.toml` (add `scout` extra)

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

PIL = pytest.importorskip("PIL")
from PIL import Image
from shortlist.scout.report.png import render_glance


def _leader(t, c):
    return LeaderVM(ticker=t, name=None, composite=c,
                    subscores={"quality": 90, "moat": None, "growth": 60, "value": 40,
                               "momentum": 5, "insider": 50, "risk": 70},
                    masked=set(), gates=[], flags=[], confidence=0.8, thin=False,
                    scored=True, coverage_note=None, metrics=MetricsVM(), assessment=None)


def _vm(n):
    return ReportVM(session=date(2026, 6, 4),
                    leaders=[_leader(f"T{i}", 80 - i) for i in range(n)],
                    signals=[SignalStatusVM("edgar_form4", True, "x")],
                    funnel=FunnelVM(n, n, n, n, 0), notes=[])


def test_render_returns_valid_png_bytes():
    out = render_glance(_vm(6))
    assert isinstance(out, bytes) and out[:8] == b"\x89PNG\r\n\x1a\n"
    assert Image.open(io.BytesIO(out)).format == "PNG"


def test_height_scales_with_row_count():
    h3 = Image.open(io.BytesIO(render_glance(_vm(3)))).height
    h12 = Image.open(io.BytesIO(render_glance(_vm(12)))).height
    assert h12 > h3


def test_empty_renders_a_valid_card_not_a_crash():
    out = render_glance(_vm(0))
    assert Image.open(io.BytesIO(out)).format == "PNG"
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

from .theme import SUBS, SUB_LABELS, BG, FG, GRID, score_to_rgb, text_on
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
    comps = [l.composite for l in vm.leaders]

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
Expected: PASS (3 tests)

- [ ] **Step 6: Verify the facade now embeds the PNG**

Run: `uv run pytest tests/scout/test_report_facade.py -v`
Expected: still PASS (the monkeypatched test forces png=None; nothing breaks).

- [ ] **Step 7: Commit**

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


def _client(seen):
    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"ok": True})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_configured_reflects_credentials():
    assert TelegramNotifier("T", "42").configured() is True
    assert TelegramNotifier(None, None).configured() is False


def test_send_message_chunks_over_4096():
    seen = []
    n = TelegramNotifier("T", "42", client=_client(seen))
    assert n.send_message("x" * 9000) is True
    assert len(seen) >= 3 and all("/sendMessage" in u for u in seen)


def test_send_photo_and_document_hit_correct_endpoints():
    seen = []
    n = TelegramNotifier("T", "42", client=_client(seen))
    assert n.send_photo(b"\x89PNG", "cap") is True
    assert n.send_document(b"<html>", "r.html", "cap") is True
    assert any("/sendPhoto" in u for u in seen) and any("/sendDocument" in u for u in seen)


def test_deliver_sequences_photo_then_document():
    calls = []

    class Fake:
        def configured(self): return True
        def send_photo(self, png, cap): calls.append("photo"); return True
        def send_document(self, data, fn, cap): calls.append("doc"); return True
        def send_message(self, text): calls.append("msg"); return True

    res = deliver(Fake(), png=b"x", html="<h>", text="t", caption="c", session="2026-06-04")
    assert calls == ["photo", "doc"]
    assert res.configured and res.all_ok


def test_deliver_falls_back_to_message_on_failure():
    calls = []

    class Fake:
        def configured(self): return True
        def send_photo(self, png, cap): calls.append("photo"); return True
        def send_document(self, data, fn, cap): calls.append("doc"); return False
        def send_message(self, text): calls.append("msg"); return True

    res = deliver(Fake(), png=b"x", html="<h>", text="t", caption="c", session="2026-06-04")
    assert "msg" in calls and not res.all_ok and "document" in " ".join(res.failures)


def test_deliver_unconfigured_does_nothing():
    class Fake:
        def configured(self): return False
        def send_photo(self, *a): raise AssertionError("should not send")
        def send_document(self, *a): raise AssertionError("should not send")
        def send_message(self, *a): raise AssertionError("should not send")

    res = deliver(Fake(), png=None, html="<h>", text="t", caption="c", session="x")
    assert not res.configured and not res.all_ok
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scout/test_notifier.py -v`
Expected: FAIL with `ImportError: cannot import name 'TelegramNotifier'`

- [ ] **Step 3: Write the implementation (append to notify.py, keep `send_telegram`)**

```python
# append to src/shortlist/scout/notify.py
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
                    # Retry-After-aware backoff; tests use a 200-handler so this is rare.
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
git commit -m "feat(scout-notify): TelegramNotifier (photo/document/chunked message) + deliver() policy"
```

---

## Task 8: Wire the orchestrator

**Files:**
- Modify: `src/shortlist/scout/daily.py`
- Modify: `config.yaml`
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
import shortlist.scout.daily as daily


def test_demo_run_still_prints_text(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = {"scout": {"artifact_dir": "scout", "state_path": "state/s.json",
                     "deep_screen_sources": ["mock"]}}
    rc = daily.run(cfg, demo=True, today=date(2026, 6, 4))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Scout shortlist" in out                  # text report still printed in demo


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
Expected: FAIL — `_assessment_record_from_file` does not exist (and demo path may differ).

- [ ] **Step 4: Add the assessment-record loader and extend `_research_phase`**

In `daily.py`, add next to `_one_line_brief_from_file`:

```python
def _assessment_record_from_file(brief_path) -> dict | None:
    """Read the full QualitativeAssessment record (JSON) that report.write() saved."""
    try:
        json_path = Path(str(brief_path).replace(".md", ".json"))
        return json.loads(json_path.read_text())
    except Exception:  # noqa: BLE001
        return None
```

Change `_research_phase` to also return `assessments`. Update its signature/return:

```python
def _research_phase(cards, config, scout_cfg, *, _is_available=None, _enrich=None
                    ) -> tuple[dict, dict, list, str | None]:
    # ... unchanged guard/kill-switch/budget body ...
    # at the end, replace the briefs loop with:
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

Update the three early-return tuples in `_research_phase` to 4-tuples
(`return {}, {}, [], "<reason>"`).

- [ ] **Step 5: Rewire `run()` to build artifacts + deliver + persist**

In `daily.py run()`, replace the research-call + message/deliver/persist block
(currently lines ~150–187) with:

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

    from .report import build_report
    rep_cfg = scout_cfg.get("report", {})
    artifacts = build_report(cards, manifest, briefs=briefs, assessments=assessments,
                             config=config)
    caption = _caption(manifest, cards, rep_cfg.get("caption_top_n", 3))

    # 4. Deliver + persist
    if demo:
        print(artifacts.text)
        return 0

    from .notify import TelegramNotifier, deliver
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

Remove the now-unused `from .report import render_message` import line and the old
`_write_manifest` function. Keep `render_message` available (facade) for any other caller.

- [ ] **Step 6: Run the new test + full scout suite**

Run: `uv run pytest tests/scout/test_orchestrator_reporting.py -v`
Expected: PASS (2 tests).

Run: `uv run pytest tests/scout/ -q`
Expected: PASS. If `test_fixes.py` / `test_orchestrator_integration.py` assert the old flat
`scout/<date>.json` path, update them to `scout/<date>/manifest.json` (the artifact layout
changed by design — see spec §10).

- [ ] **Step 7: Run the entire suite**

Run: `uv run pytest -q`
Expected: PASS (no regressions outside scout).

- [ ] **Step 8: Commit**

```bash
git add src/shortlist/scout/daily.py config.yaml tests/scout/test_orchestrator_reporting.py
git commit -m "feat(scout): wire reporting artifacts + TelegramNotifier delivery into the daily run"
```

---

## Task 9: Smoke + docs

**Files:**
- Modify: `HARNESS.md` or `docs/AUTONOMOUS_SCOUT.md` (scout delivery section)
- Modify: `docs/NOTIFICATIONS.md` (mark §3 hardening as implemented)

- [ ] **Step 1: Demo smoke**

Run: `uv run shortlist-scout --demo`
Expected: prints a text report to stdout, exit 0, no traceback.

- [ ] **Step 2: Live-shape smoke (offline-safe, no Telegram creds)**

Run: `uv run python -c "from datetime import date; import shortlist.scout.daily as d; import yaml; \
cfg=yaml.safe_load(open('config.yaml')); cfg['scout']['deep_screen_sources']=['mock']; \
print('rc', d.run(cfg, demo=True, today=date(2026,6,4)))"`
Expected: `rc 0`.

- [ ] **Step 3: Generate a real HTML+PNG artifact to eyeball**

Run: `uv run python -c "from datetime import date; from shortlist.models import ScoreCard, StockMetrics; \
from shortlist.scout.models import RunManifest, SignalStatus; from shortlist.scout.report import build_report; \
c=ScoreCard(ticker='NVDA', composite=79, quality=100, moat=100, growth=100, value=74, momentum=59, \
opportunity=74, insider=41, risk=55, metrics=StockMetrics(ticker='NVDA', price=100, target_median=137)); \
m=RunManifest(session=date(2026,6,4), signals=[SignalStatus('edgar_form4',True,'2')], raw=5, after_dedup=4, \
after_prefilter=3, screened=1, dropped_for_budget=0); a=build_report([c], m, briefs={}, assessments={}, config={}); \
open('scratch/smoke.html','w').write(a.html); open('scratch/smoke.png','wb').write(a.png or b''); print('wrote scratch/smoke.{html,png}')"`
Expected: writes the files; open `scratch/smoke.html` in a browser to verify the embedded chart + tables render.

- [ ] **Step 4: Update docs**

In `docs/NOTIFICATIONS.md`, change the §3 hardening plan heading to note it is implemented
(chunking, retry, `Notifier` seam, photo/document delivery), and point to this plan +
the spec. In `docs/AUTONOMOUS_SCOUT.md`, update the scout delivery description from
"text Telegram report" to "chart (sendPhoto) + HTML deep-dive (sendDocument), artifacts
under `scout/<date>/`."

- [ ] **Step 5: Commit**

```bash
git add docs/NOTIFICATIONS.md docs/AUTONOMOUS_SCOUT.md
git commit -m "docs(scout): reporting + notification layer shipped; delivery semantics updated"
```

---

## Self-review notes (for the implementer)

- **Spec coverage:** §3 architecture → Tasks 2/4/6; §4 module layout → all tasks; §5 view-model
  → Task 2; §6 HTML → Tasks 3/4/5; §7 PNG → Task 6; §8 Notifier+deliver → Task 7; §9
  orchestration → Task 8; §10 persistence → Task 8 `_persist`; §11 config → Task 8; §12 deps
  → Task 6; §13 degradation → facade `_render_png` (Task 5) + `deliver` unconfigured (Task 7)
  + empty-N PNG (Task 6) + demo (Task 8); §14 testing → every task; §15 build order = task
  order; §16 risks → text substring-compat (Task 5 Step 4/5), Pillow-not-hard-dep (facade
  try/except + `importorskip` in Task 6), HTML injection (Task 3/4 escaping tests).
- **Back-compat anchors:** keep `render_message` (facade) and `send_telegram` (notify.py)
  exported; preserve `state.mark_run_completed` BEFORE `record_screened`; exit 0 unconfigured /
  exit 2 configured-but-failed (Task 8 Step 5 mirrors the original FIX 3/FIX 4 ordering).
- **Type consistency:** `build_report(... ) -> ReportArtifacts(png,html,text)`; `deliver(...) ->
  DeliveryResult(configured,all_ok,failures)`; `_research_phase` now returns a 4-tuple
  `(briefs, assessments, researched, note)` — every return site updated in Task 8 Step 4.
```

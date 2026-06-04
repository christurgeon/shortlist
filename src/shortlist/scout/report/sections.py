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

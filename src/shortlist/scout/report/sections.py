"""Report sections: each renders itself to HTML and to text. New section = one class + registry line.

The PNG glance is deliberately NOT a section (raster layout does not compose); it reads
the view-model directly in png.py. HTML and text are the same sections at different Detail.
The _Research section owns ALL Claude content in both formats.
"""
from __future__ import annotations

import enum
from typing import Protocol

from .html import HtmlBuilder
from .theme import SUB_LABELS, SUBS, rgb_hex, score_to_rgb, stance_emoji, stance_to_rgb, text_on
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


def _fmt(v, pct=False, money=False, **_):   # tolerate display-only opts (e.g. `neutral`)
    if v is None:
        return "·"
    if money:
        return f"${v/1e9:.1f}B" if abs(v) >= 1e9 else f"${v/1e6:.0f}M"
    return f"{v*100:+.0f}%" if pct else f"{v:.1f}"


# ---- leaderboard ----
class _Leaderboard:
    id, title = "leaderboard", "Shortlist"

    def applies(self, vm): return bool(vm.leaders)

    @staticmethod
    def _chip(v, extra=""):
        c = score_to_rgb(v)
        style = f"background:{rgb_hex(c)};color:{rgb_hex(text_on(c))}"
        txt = "·" if v is None else f"{v:.0f}"
        return f'<span class="chip{extra}" style="{style}">{txt}</span>'

    @staticmethod
    def _tags(items, cls):
        return "".join(f'<span class="tag {cls}">{HtmlBuilder().esc(x)}</span>' for x in items)

    def render_html(self, vm, h):
        rows = []
        for i, ld in enumerate(vm.leaders, 1):
            tik = h.raw("td", h.raw("span", h.esc(ld.ticker), _class="t") +
                        (h.raw("span", h.esc(ld.name), _class="n") if ld.name else ""),
                        _class="tik")
            cells = [h.tag("td", str(i), _class="rk"), tik,
                     h.raw("td", self._chip(ld.composite, " comp"))]
            for s in SUBS:
                cells.append(h.raw("td", self._chip(ld.subscores.get(s))))
            cells.append(h.raw("td", self._tags(ld.gates, "tag-gate"), _class="tags"))
            cells.append(h.raw("td", self._tags(ld.flags, "tag-flag"), _class="tags"))
            rows.append(h.raw("tr", "".join(cells)))
        cols = (["#", "Ticker", "Comp"] + [SUB_LABELS[s] for s in SUBS] + ["Gates", "Flags"])
        head = h.raw("tr", "".join(
            h.tag("th", x, _class=("tik" if c == 1 else "")) for c, x in enumerate(cols)))
        table = h.raw("table", h.raw("thead", head) + h.raw("tbody", "".join(rows)),
                      _class="board")
        # board-wrap hosts a static right-edge fade (no-JS scroll affordance on mobile)
        return h.raw("div", h.raw("div", table, _class="scroll-x"), _class="board-wrap")

    def render_text(self, vm, detail):
        out = []
        for i, ld in enumerate(vm.leaders, 1):
            gate = f"  ⚠️ {', '.join(ld.gates)}" if ld.gates else ""
            flag = f"  🏷️ {', '.join(ld.flags)}" if ld.flags else ""
            mark = "" if ld.scored else "  (not scored)"
            thin = "  (thin)" if ld.thin else ""
            out.append(f"{i}. {ld.ticker}  {ld.composite:.1f}{gate}{flag}{mark}{thin}")
            subs = " ".join(
                f"{SUB_LABELS[s]}{'·' if ld.subscores.get(s) is None else f'{ld.subscores[s]:.0f}'}"
                for s in SUBS)
            out.append(f"   {subs}")
            if ld.coverage_note:
                out.append(f"   ⊘ {ld.coverage_note}")
        return out


# ---- per-leader fundamentals (HTML carries the full table; FULL text mirrors it) ----
_FUND_ROWS = [("Price", "price", {}), ("Mkt cap", "market_cap", {"money": True}),
              ("PE (ttm)", "pe_ttm", {}), ("PE 5y med", "pe_median_5y", {}),
              ("FCF yield", "fcf_yield", {"pct": True}), ("PEG", "peg", {}),
              ("ROE", "roe", {"pct": True}), ("ROIC", "roic", {"pct": True}),
              ("Gross mgn", "gross_margin", {"pct": True}), ("Net mgn", "net_margin", {"pct": True}),
              ("Debt/Eq", "debt_to_equity", {}), ("Rev CAGR", "revenue_cagr", {"pct": True}),
              ("EPS CAGR", "eps_cagr", {"pct": True}), ("vs 200dma", "price_vs_200dma", {"pct": True}),
              ("Rel str 6m", "rel_strength_6m", {"pct": True}),
              ("Volatility", "realized_vol", {"pct": True, "neutral": True}),
              ("Max DD", "max_drawdown", {"pct": True}), ("Target upside", "target_upside", {"pct": True}),
              ("Insider 6m", "insider_net_6m", {"money": True})]


class _Fundamentals:
    id, title = "fundamentals", "Fundamentals"

    def applies(self, vm): return bool(vm.leaders)

    @staticmethod
    def _metric(h, label, value, signed, raw=None):
        cls = "v"
        if value == "·":
            cls = "v na"
        elif signed and any(ch in "123456789" for ch in value):
            # Decide good/bad from the raw numeric sign — money metrics (e.g.
            # insider_net_6m) format as "$..M" with no leading +/-, so the sign isn't
            # in the string. The any-nonzero-digit guard skips a value that rounds to
            # zero ("+0%"/"-0%"/"$-0M") so a tiny negative doesn't read as bearish.
            s = raw if raw is not None else (-1.0 if value[:1] == "-" else 1.0)
            cls = "v neg" if s < 0 else "v pos"
        return h.raw("div", h.tag("span", label, _class="k") +
                     h.raw("span", h.esc(value), _class=cls), _class="metric")

    def render_html(self, vm, h):
        cards = []
        for ld in vm.leaders:
            # Sign-color only metrics where +/- genuinely means better/worse. `neutral`
            # opt-out keeps always-positive magnitudes (e.g. volatility) from reading
            # as "good" just because they carry a + sign.
            cells = [self._metric(h, label, _fmt(getattr(ld.metrics, attr), **opt),
                                  bool(opt.get("pct") or opt.get("money"))
                                  and not opt.get("neutral"),
                                  raw=getattr(ld.metrics, attr))
                     for label, attr, opt in _FUND_ROWS]
            analysts = (f"{ld.metrics.rating_buy or 0}B / {ld.metrics.rating_hold or 0}H / "
                        f"{ld.metrics.rating_sell or 0}S")
            cells.append(self._metric(h, "Analysts", analysts, False))
            heading = (h.raw("span", h.esc(ld.ticker), _class="tk") +
                       (h.raw("span", h.esc(ld.name), _class="nm") if ld.name else "") +
                       h.raw("span", h.esc(f"{ld.composite:.0f}"), _class="sc"))
            cards.append(h.raw("div",
                               h.raw("h2", heading) +
                               h.raw("div", "".join(cells), _class="metrics"), _class="card"))
        return "".join(cards)

    def render_text(self, vm, detail):
        if detail is Detail.GLANCE:
            return []
        out = []
        for ld in vm.leaders:
            out.append(f"-- {ld.ticker} metrics --")
            out += [f"   {label}: {_fmt(getattr(ld.metrics, attr), **opt)}"
                    for label, attr, opt in _FUND_ROWS]
        return out


# ---- Claude research (owns ALL qualitative content) ----
class _Research:
    id, title = "research", "Research"

    def applies(self, vm): return any(ld.assessment for ld in vm.leaders)

    def render_html(self, vm, h):
        cards = []
        for ld in vm.leaders:
            a = ld.assessment
            if not a:
                continue
            heading = h.raw("span", h.esc(ld.ticker), _class="tk") + " analysis"
            parts = [h.raw("h2", heading)]
            if a.call_stance:
                col = stance_to_rgb(a.call_stance)
                pill = (f'<span class="pill" style="background:{rgb_hex(col)};'
                        f'color:{rgb_hex(text_on(col))}">'
                        f'{h.esc(a.call_label)} · {h.esc(a.call_conviction.title())}</span>')
                line = pill + ' <span class="muted">screen only — not advice</span>'
                if a.call_watch:
                    line += ' <span class="muted">· but watch: ' + h.esc(a.call_watch) + "</span>"
                parts.append(h.raw("p", line, _class="call"))
                if a.call_rationale:
                    parts.append(h.raw("p", "<b>Why:</b> " + h.esc(a.call_rationale)))
                if a.call_decided_without:
                    dw = "; ".join(a.call_decided_without)
                    parts.append(h.raw("p", "<b>Decided without:</b> " + h.esc(dw),
                                       _class="muted"))
            if a.takeaway:
                parts.append(h.tag("p", a.takeaway, _class="takeaway"))
            if a.business_model:
                parts.append(h.tag("p", a.business_model))
            if a.moat:
                parts.append(h.raw("p", "<b>Moat:</b> " + h.esc(a.moat)))
            if a.bull_case:
                parts.append(h.raw("div", "<b>Bull:</b> " + h.esc(a.bull_case),
                                   _class="callout bull"))
            if a.bear_case:
                parts.append(h.raw("div", "<b>Bear:</b> " + h.esc(a.bear_case),
                                   _class="callout bear"))
            if a.reconciliation:
                lis = "".join(h.tag("li", f"{sig}: {tension}")
                              for sig, tension in a.reconciliation)
                parts.append(h.raw("div", h.tag("b", "Reconciliation vs. score") +
                                   h.raw("ul", lis), _class="block"))
            for label, items, cls in [("Red flags", a.red_flags, "flag"),
                                      ("Risks", a.risks, ""),
                                      ("What would change my mind", a.change_my_mind, "")]:
                if items:
                    lis = "".join(h.tag("li", x) for x in items)
                    parts.append(h.raw("div", h.tag("b", label) + h.raw("ul", lis),
                                       _class=f"block {cls}".strip()))
            if a.capital_allocation:
                parts.append(h.raw("p", "<b>Capital allocation:</b> " + h.esc(a.capital_allocation)))
            cards.append(h.raw("div", "".join(parts), _class="card"))
        return "".join(cards)

    def render_text(self, vm, detail):
        out = []
        for ld in vm.leaders:
            a = ld.assessment
            if not a:
                continue
            line = None
            if a.call_stance:
                head = (f"{stance_emoji(a.call_stance)} {ld.ticker}: {a.call_label} · "
                        f"{a.call_conviction.title()} — screen only, not advice")
                if a.call_watch:
                    head += f" · but watch: {a.call_watch}"
                out.append(head)
            else:
                line = a.takeaway or a.bull_case
                out.append(f"📝 {ld.ticker}: {line[:160]}" if line else f"📝 {ld.ticker}")
            if detail is Detail.FULL:
                if a.takeaway and a.takeaway != line:
                    out.append(f"   {a.takeaway}")
                for sig, tension in a.reconciliation:
                    out.append(f"   ⚖️ {sig}: {tension}")
                if a.red_flags:
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

    def _funnel_html(self, vm, h):
        f = vm.funnel
        steps = [(f.raw, "raw"), (f.after_dedup, "deduped"),
                 (f.after_prefilter, "prefilter"), (f.screened, "screened")]
        arw = '<span class="arw">›</span>'
        body = arw.join(f'<b>{n}</b> {h.esc(label)}' for n, label in steps)
        drop = f' <span class="drop">({f.dropped_for_budget} dropped: budget)</span>'
        return h.raw("div", body + drop, _class="funnel")

    def render_html(self, vm, h):
        inner = ""
        if vm.signals:   # autonomous run; interactive sets signals=[] -> coverage hidden
            chips = "".join(
                h.raw("span", f'{h.esc(s.name)} {"✓" if s.ran else "✗"} '
                      f'<span class="muted">({h.esc(s.detail)})</span>',
                      _class=f"sig {'ok' if s.ran else 'no'}")
                for s in vm.signals)
            inner += h.raw("div", chips, _class="sigs") + self._funnel_html(vm, h)
        inner += "".join(h.tag("div", n, _class="note") for n in vm.notes)
        return h.raw("div", inner, _class="cov")

    def render_text(self, vm, detail):
        out = [""]
        if vm.signals:
            out += [f"Signals: {self._sig(vm)}", f"Funnel: {self._funnel(vm)}"]
        out += [f"Note: {n}" for n in vm.notes]
        return out


# ---- macro / regime header ----
class _MacroHeader:
    id, title = "macro", "Regime"

    def applies(self, vm): return vm.macro is not None

    def _line(self, mc):
        bits = [f"Regime: {mc.regime}"]
        if mc.hy_oas is not None:   bits.append(f"HY OAS {mc.hy_oas:.1f}%")
        if mc.t10y2y is not None:   bits.append(f"2s10s {mc.t10y2y:+.2f}")
        if mc.vix is not None:      bits.append(f"VIX {mc.vix:.0f}")
        if mc.dgs10 is not None:    bits.append(f"10y {mc.dgs10:.1f}%")
        if mc.fedfunds is not None: bits.append(f"FFR {mc.fedfunds:.1f}%")
        return " · ".join(bits)

    def render_html(self, vm, h):
        # h.tag escapes the text internally (see html.py) — the idiom _Footer uses
        # for plain-text content.
        return h.tag("div", self._line(vm.macro), _class="macro")

    def render_text(self, vm, detail):
        return ["", self._line(vm.macro)]


# ---- owned-holdings exposure + monitoring (bot /portfolio) ----
class _Portfolio:
    id, title = "portfolio", "Portfolio"

    def applies(self, vm):
        p = getattr(vm, "portfolio", None)
        return p is not None and hasattr(p, "alerts")

    @staticmethod
    def _chips(pos):
        if pos.no_data:
            return ["no data"]
        c = list(pos.card.gates) + list(pos.card.flags)
        if not pos.card.scored:
            c.append("not scored")
        return c

    def render_html(self, vm, h):
        p = vm.portfolio
        parts = []
        if p.alerts:
            items = "".join(
                h.raw("li", h.esc(f"{pos.ticker} — {', '.join(self._chips(pos)) or 'flagged'}"))
                for pos in p.alerts)
            parts.append(h.raw("div", h.raw("b", "Alerts") + h.raw("ul", items), _class="pf-alerts"))
        rows = []
        for pos in p.positions:
            w = "·" if pos.weight is None else f"{pos.weight*100:.0f}%"
            comp = "·" if pos.card is None else f"{pos.card.composite:.0f}"
            tags = ", ".join(self._chips(pos)) if (pos.no_data or
                   (pos.card and (pos.card.gates or pos.card.flags or not pos.card.scored))) else "·"
            cells = (h.tag("td", pos.ticker) + h.tag("td", w) + h.tag("td", comp) + h.tag("td", tags))
            rows.append(h.raw("tr", cells))
        head = h.raw("tr", "".join(h.tag("th", c) for c in ("Ticker", "Weight", "Comp", "Gates/Flags")))
        parts.append(h.raw("table", h.raw("thead", head) + h.raw("tbody", "".join(rows)), _class="pf"))
        if p.sector_weights:
            sec = " · ".join(f"{b} {w*100:.0f}%" for b, w in p.sector_weights)
            parts.append(h.tag("div", "Sectors: " + sec, _class="pf-sectors"))
        if p.total_value is not None:
            wc = f" · wtd comp {p.weighted_composite:.0f}" if p.weighted_composite is not None else ""
            parts.append(h.tag("div", f"Book ${p.total_value/1e3:.0f}k{wc}", _class="pf-tot"))
        return "".join(parts)

    def render_text(self, vm, detail):
        p = vm.portfolio
        out = [""]
        if p.alerts:
            out.append("⚠️ Alerts:")
            out += [f"  {pos.ticker} — {', '.join(self._chips(pos)) or 'flagged'}" for pos in p.alerts]
        for pos in p.positions:
            w = "·" if pos.weight is None else f"{pos.weight*100:.0f}%"
            comp = "·" if pos.card is None else f"{pos.card.composite:.0f}"
            out.append(f"  {pos.ticker}  {w}  comp {comp}")
        if p.sector_weights:
            out.append("  Sectors: " + " · ".join(f"{b} {w*100:.0f}%" for b, w in p.sector_weights))
        if p.total_value is not None:
            wc = f" · wtd comp {p.weighted_composite:.0f}" if p.weighted_composite is not None else ""
            out.append(f"  Book ${p.total_value/1e3:.0f}k{wc}")
        return out


SECTIONS: list[Section] = [_MacroHeader(), _Leaderboard(), _Fundamentals(), _Research(),
                            _Portfolio(), _Footer()]


def render_html_body(vm: ReportVM) -> str:
    h = HtmlBuilder()
    out = []
    for s in SECTIONS:
        if s.applies(vm):
            label = h.raw("div", h.esc(s.title), _class="sec-label")
            out.append(h.raw("section", label + s.render_html(vm, h), _class="sec"))
    return "".join(out)


def render_text(vm: ReportVM, detail: Detail) -> str:
    lines = [f"📊 Scout shortlist — session {vm.session.isoformat()}", ""]
    for s in SECTIONS:
        if s.applies(vm):
            lines += s.render_text(vm, detail)
    return "\n".join(lines)

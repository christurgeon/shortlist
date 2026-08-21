"""Report sections: each renders itself to HTML and to text. New section = one class + registry line.

The PNG glance is deliberately NOT a section (raster layout does not compose); it reads
the view-model directly in png.py. HTML and text are the same sections at different Detail.
The _Research section owns ALL Claude content in both formats.
"""
from __future__ import annotations

import enum
from typing import Protocol

from .html import HtmlBuilder
from .theme import (
    FLAG_DESCRIPTIONS,
    GATE_DESCRIPTIONS,
    SUB_LABELS,
    SUBS,
    describe_code,
    rgb_hex,
    score_to_rgb,
    stance_emoji,
    stance_to_rgb,
    text_on,
)
from .viewmodel import MetricsVM, ReportVM


class Detail(enum.Enum):
    GLANCE = "glance"   # terse, for the chunked Telegram text fallback
    FULL = "full"       # complete, for the on-disk .txt


class Section(Protocol):
    id: str
    title: str
    def applies(self, vm: ReportVM) -> bool: ...
    def render_html(self, vm: ReportVM, h: HtmlBuilder) -> str: ...
    def render_text(self, vm: ReportVM, detail: Detail) -> list[str]: ...


_SI_MIN_DISPLAY = 0.05   # only surface short interest once it's material (FINRA covers
                         # most names at ~1%, which is noise); crowded_short fires at 10%.


def _short_interest_text(mvm: MetricsVM) -> "str | None":
    """Compact FINRA short-interest string, or None when absent / immaterial. Pairs with
    the crowded_short flag: '22.4% / 8.1d ↑' (days-to-cover + rising arrow each optional).
    Note: unsigned, 1-decimal % (deliberately unlike _fmt's signed integer %) — short
    interest is a magnitude, not a +/- signal, and precision matters near the gate."""
    sp = getattr(mvm, "short_pct_outstanding", None)
    if sp is None or sp < _SI_MIN_DISPLAY:
        return None
    parts = [f"{sp * 100:.1f}%"]
    dtc = getattr(mvm, "days_to_cover", None)
    if dtc is not None:
        parts.append(f"{dtc:.1f}d")
    txt = " / ".join(parts)
    if getattr(mvm, "short_interest_rising", None):
        txt += " ↑"
    return txt


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
    def _tags(h, items, cls):
        return "".join(f'<span class="tag {cls}">{h.esc(x)}</span>' for x in items)

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
            rows.append(h.raw("tr", "".join(cells)))
        cols = (["#", "Ticker", "Comp"] + [SUB_LABELS[s] for s in SUBS])
        head = h.raw("tr", "".join(
            h.tag("th", x, _class=("tik" if c == 1 else "")) for c, x in enumerate(cols)))
        table = h.raw("table", h.raw("thead", head) + h.raw("tbody", "".join(rows)),
                      _class="board")
        # board-wrap hosts a static right-edge fade (no-JS scroll affordance on mobile)
        board = h.raw("div", h.raw("div", table, _class="scroll-x"), _class="board-wrap")
        # Gates+flags live OUTSIDE the scrolling heatmap so they wrap at viewport
        # width instead of trailing off the right edge (where they read as cut off).
        return board + self._flags_strip(vm, h)

    def _flags_strip(self, vm, h):
        """One wrapping chip row per leader that carries a gate or flag; nothing for
        clean names. Sits below the heatmap, free of its horizontal scroll."""
        rows = []
        for ld in vm.leaders:
            if not ld.gates and not ld.flags:
                continue
            chips = (self._tags(h, ld.gates, "tag-gate") +
                     self._tags(h, ld.flags, "tag-flag"))
            rows.append(h.raw("div",
                              h.raw("span", h.esc(ld.ticker), _class="fs-tik") + chips,
                              _class="flags-row"))
        if not rows:
            return ""
        return h.raw("div", "".join(rows), _class="flags-strip")

    def render_text(self, vm, detail):
        # The leaderboard is deliberately full at both detail levels — the ranked
        # table is the report's spine, so GLANCE does not terse it (unlike
        # _Fundamentals). `detail` is therefore intentionally unused here.
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
              ("Insider 6m", "insider_net_6m", {"money": True}),
              # Net-debt/EBITDA (the over_leveraged gate's measure; floored >=0 in the VM).
              # Uncolored like the sibling debt_to_equity row (a plain ratio, not a +/- signal).
              ("Net debt/EBITDA", "net_debt_to_ebitda", {})]


def _piotroski_text(mvm: MetricsVM) -> "str | None":
    """'won/legs' Piotroski fraction (e.g. '5/6'), or None when absent."""
    pf = getattr(mvm, "piotroski_f", None)
    if pf is None:
        return None
    return f"{pf}/{getattr(mvm, 'piotroski_f_legs', None) or 6}"


def _earnings_text(mvm: MetricsVM) -> "str | None":
    """Compact earnings-execution string, or None when absent: beat consistency,
    avg surprise, and days to the next report — e.g. '4/4 beats · +3.7% · next 18d'.
    avg-surprise and next-report are each included only when present."""
    q = getattr(mvm, "earnings_quarters", None)
    if not q:
        return None
    parts = [f"{getattr(mvm, 'earnings_beats', None) or 0}/{q} beats"]
    avg = getattr(mvm, "earnings_avg_surprise_pct", None)
    if avg is not None:
        parts.append(f"{avg:+.1f}%")
    d = getattr(mvm, "earnings_days_to_next", None)
    if d is not None:
        parts.append(f"next {d}d")
    return " · ".join(parts)


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
            si = _short_interest_text(ld.metrics)   # conditional — only crowded names
            if si:
                cells.append(self._metric(h, "Short interest", si, False))
            pio = _piotroski_text(ld.metrics)   # conditional (None on lean/masked stacks)
            if pio:
                cells.append(self._metric(h, "Piotroski", pio, False))
            ern = _earnings_text(ld.metrics)    # conditional (None without earnings history)
            if ern:
                cells.append(self._metric(h, "Earnings", ern, False))
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
            si = _short_interest_text(ld.metrics)
            if si:
                out.append(f"   Short interest: {si}")
            pio = _piotroski_text(ld.metrics)
            if pio:
                out.append(f"   Piotroski: {pio}")
            ern = _earnings_text(ld.metrics)
            if ern:
                out.append(f"   Earnings: {ern}")
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
                if a.call_model_stance:
                    # A gate override is not the model's own view; say so by the pill.
                    line += ' <span class="muted">· gate override</span>'
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
                if a.call_model_stance:
                    head += " · gate override"
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
    id, title = "footer", "Notes"

    def applies(self, vm): return bool(vm.notes)

    def render_html(self, vm, h):
        return h.raw("div", "".join(h.tag("div", n, _class="note") for n in vm.notes),
                     _class="cov")

    def render_text(self, vm, detail):
        return [""] + [f"Note: {n}" for n in vm.notes]


# ---- macro / regime header ----
class _MacroHeader:
    id, title = "macro", "Regime"

    def applies(self, vm): return vm.macro is not None

    def _line(self, mc):
        bits = [f"Regime: {mc.regime}"]
        if mc.hy_oas is not None:
            bits.append(f"HY OAS {mc.hy_oas:.1f}%")
        if mc.t10y2y is not None:
            bits.append(f"2s10s {mc.t10y2y:+.2f}")
        if mc.vix is not None:
            bits.append(f"VIX {mc.vix:.0f}")
        if mc.dgs10 is not None:
            bits.append(f"10y {mc.dgs10:.1f}%")
        if mc.fedfunds is not None:
            bits.append(f"FFR {mc.fedfunds:.1f}%")
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
        p = vm.portfolio
        return p is not None and hasattr(p, "alerts")

    @staticmethod
    def _chips(pos):
        if pos.no_data or pos.card is None:
            return ["no data"]
        c = list(pos.card.gates) + list(pos.card.flags)
        if not pos.card.scored:
            c.append("not scored")
        return c

    # Display-string helpers shared by both renderers so the scaling math lives once.
    @staticmethod
    def _pos_weight(pos):
        return "·" if pos.weight is None else f"{pos.weight*100:.0f}%"

    @staticmethod
    def _pos_comp(pos):
        return "·" if pos.card is None else f"{pos.card.composite:.0f}"

    @staticmethod
    def _sector_line(sector_weights):
        return " · ".join(f"{b} {w*100:.0f}%" for b, w in sector_weights)

    @staticmethod
    def _book_line(p):
        wc = f" · wtd comp {p.weighted_composite:.0f}" if p.weighted_composite is not None else ""
        return f"Book ${p.total_value/1e3:.0f}k{wc}"

    def render_html(self, vm, h):
        p = vm.portfolio
        parts = []
        if p.alerts:
            items = "".join(
                h.tag("li", f"{pos.ticker} — {', '.join(self._chips(pos)) or 'flagged'}")
                for pos in p.alerts)
            parts.append(h.raw("div", h.raw("b", "Alerts") + h.raw("ul", items), _class="pf-alerts"))
        rows = []
        for pos in p.positions:
            w = self._pos_weight(pos)
            comp = self._pos_comp(pos)
            chips = self._chips(pos)
            tags = ", ".join(chips) if chips else "·"
            cells = (h.tag("td", pos.ticker) + h.tag("td", w) + h.tag("td", comp) + h.tag("td", tags))
            rows.append(h.raw("tr", cells))
        head = h.raw("tr", "".join(h.tag("th", c) for c in ("Ticker", "Weight", "Comp", "Gates/Flags")))
        parts.append(h.raw("table", h.raw("thead", head) + h.raw("tbody", "".join(rows)), _class="pf"))
        if p.sector_weights:
            parts.append(h.tag("div", "Sectors: " + self._sector_line(p.sector_weights), _class="pf-sectors"))
        if p.total_value is not None:
            parts.append(h.tag("div", self._book_line(p), _class="pf-tot"))
        # NOTE: pf/pf-alerts/pf-sectors/pf-tot are unstyled in v1 (html._CSS) — content
        # renders readably on base table/div rules; theming is deferred.
        return "".join(parts)

    def render_text(self, vm, detail):
        p = vm.portfolio
        out = [""]
        if p.alerts:
            out.append("⚠️ Alerts:")
            out += [f"  {pos.ticker} — {', '.join(self._chips(pos)) or 'flagged'}" for pos in p.alerts]
        for pos in p.positions:
            w = self._pos_weight(pos)
            comp = self._pos_comp(pos)
            out.append(f"  {pos.ticker}  {w}  comp {comp}")
        if p.sector_weights:
            out.append("  Sectors: " + self._sector_line(p.sector_weights))
        if p.total_value is not None:
            out.append("  " + self._book_line(p))
        return out


# ---- flag/gate glossary (conditional: only what appears in this report) ----
def _order_codes(encountered: list[str], reference: dict) -> list[str]:
    """Dedupe order-preservingly; known ids first in `reference`'s defined order,
    then any unknown/future ids in stable first-encounter order."""
    seen = list(dict.fromkeys(encountered))
    known = [c for c in reference if c in seen]
    unknown = [c for c in seen if c not in reference]
    return known + unknown


def _present_codes(vm) -> tuple[list[str], list[str]]:
    """(gate ids, flag ids) actually present across the leaderboard and any
    portfolio holdings. Reads `card.gates`/`card.flags` directly — NOT
    `_Portfolio._chips`, which injects synthetic non-codes ('not scored'/'no data')."""
    gates: list[str] = []
    flags: list[str] = []
    for ld in vm.leaders:
        gates += ld.gates
        flags += ld.flags
    p = vm.portfolio
    if p is not None:
        for pos in getattr(p, "positions", []):
            card = getattr(pos, "card", None)
            if card is not None:
                gates += list(card.gates)
                flags += list(getattr(card, "flags", []))
    return _order_codes(gates, GATE_DESCRIPTIONS), _order_codes(flags, FLAG_DESCRIPTIONS)


def _gloss_line(code: str) -> str:
    d = describe_code(code)
    return f"  {code} — {d}" if d else f"  {code}"


class _Glossary:
    id, title = "glossary", "Flags & gates in this report"

    def applies(self, vm):
        gates, flags = _present_codes(vm)
        return bool(gates or flags)

    @staticmethod
    def _items_html(h, codes, cls):
        out = []
        for c in codes:
            chip = h.raw("span", h.esc(c), _class=f"tag {cls}")
            out.append(h.raw("div", chip + h.tag("span", describe_code(c), _class="gloss-desc"),
                             _class="gloss-item"))
        return "".join(out)

    def _group_html(self, h, head, codes, cls):
        return h.raw("div", h.tag("div", head, _class="gloss-head") + self._items_html(h, codes, cls),
                     _class="gloss-group")

    def render_html(self, vm, h):
        gates, flags = _present_codes(vm)
        parts = []
        if gates:
            parts.append(self._group_html(h, "Gates (hard filters)", gates, "tag-gate"))
        if flags:
            parts.append(self._group_html(h, "Flags (advisory)", flags, "tag-flag"))
        return h.raw("div", "".join(parts), _class="glossary")

    def render_text(self, vm, detail):
        gates, flags = _present_codes(vm)
        out = []
        if gates:
            out.append("Gates (hard filters):")
            out += [_gloss_line(c) for c in gates]
        if flags:
            out.append("Flags (advisory):")
            out += [_gloss_line(c) for c in flags]
        return out


# tickers per /deep command line — matches the bot's bot.max_deep default (3)
_DEEP_PER_LINE = 3


def _pct(v) -> str:
    return "—" if v is None else f"{v * 100:+.0f}%"


# ---- /deep handoff block ----
class _DeepBlock:
    """Copy-paste /deep commands for the non-gated, scored leaders — the 'stocks worth
    passing into /deep' handoff. Conviction-ordered (the leader order), ≤3 per line."""
    id, title = "deep", "Pass to /deep"

    def applies(self, vm) -> bool:
        return bool(vm.deep_block)

    def _lines(self, vm) -> list[str]:
        t = list(vm.deep_block or [])   # None-tolerant: applies() gates render, but a
        # directly-constructed ReportVM (tests) may pass None rather than the [] default.
        return [", ".join(t[i:i + _DEEP_PER_LINE]) for i in range(0, len(t), _DEEP_PER_LINE)]

    def render_html(self, vm, h) -> str:
        cmds = "".join(h.tag("div", f"/deep {ln}", _class="deepcmd") for ln in self._lines(vm))
        note = h.tag("div", "screening triage, not investment advice", _class="muted")
        return h.raw("div", cmds + note, _class="deep")

    def render_text(self, vm, detail) -> list[str]:
        lines = self._lines(vm)
        if not lines:
            return []
        out = ["", "Pass to /deep (screening triage, not investment advice):"]
        out += [f"/deep {ln}" for ln in lines]
        return out


SECTIONS: list[Section] = [_MacroHeader(), _Leaderboard(), _Fundamentals(), _Research(),
                           _DeepBlock(), _Portfolio(), _Glossary(), _Footer()]


def render_html_body(vm: ReportVM) -> str:
    h = HtmlBuilder()
    out = []
    for s in SECTIONS:
        if s.applies(vm):
            label = h.raw("div", h.esc(s.title), _class="sec-label")
            out.append(h.raw("section", label + s.render_html(vm, h), _class="sec"))
    return "".join(out)


def render_text(vm: ReportVM, detail: Detail) -> str:
    lines = [f"📊 Shortlist — session {vm.session.isoformat()}", ""]
    for s in SECTIONS:
        if s.applies(vm):
            lines += s.render_text(vm, detail)   # every Section.render_text -> list[str]
    return "\n".join(lines)

"""Renderer-agnostic snapshot of one scout run. Pure data; no I/O, no optional deps."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from shortlist.models import ScoreCard, rank_key

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
    target_upside: float | None = None   # from StockMetrics.upside_to_target()
    insider_net_6m: float | None = None


@dataclass
class AssessmentVM:
    business_model: str = ""
    takeaway: str = ""                    # one-line TL;DR (synthesis / thesis.takeaway)
    moat: str = ""                        # moat.summary prose
    reconciliation: list[tuple[str, str]] = field(default_factory=list)  # (signal, tension)
    bull_case: str = ""
    bear_case: str = ""
    change_my_mind: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
    capital_allocation: str = ""
    call_stance: str = ""
    call_label: str = ""
    call_conviction: str = ""
    call_rationale: str = ""
    call_watch: str = ""
    call_decided_without: list[str] = field(default_factory=list)


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
    macro: "object | None" = None   # data.macro.MacroContext | None (run-level)
    portfolio: "object | None" = None   # shortlist.portfolio.PortfolioSummary | None


def _claim(x) -> str:
    return x.get("claim", "") if isinstance(x, dict) else str(x)


def _assessment_vm(rec: dict) -> AssessmentVM:
    from ...research.models import stance_label
    th = rec.get("thesis") or {}
    sc = rec.get("screening_call") if isinstance(rec.get("screening_call"), dict) else None
    cmm = [str(x) for x in (th.get("what_would_change_my_mind") or [])]
    watch = (cmm[0] if cmm else th.get("bear_case", "")) if sc else ""
    stance = sc.get("stance", "") if sc else ""
    if sc and sc.get("stance_clamped"):
        note = sc.get("clamp_note") or "a tripped gate"
        call_rationale = f"Auto-downgraded: {note}."
    else:
        call_rationale = (sc.get("rationale") or "") if sc else ""
    return AssessmentVM(
        business_model=rec.get("business_model_summary", "") or "",
        takeaway=(rec.get("synthesis") or th.get("takeaway", "") or ""),
        moat=(rec.get("moat") if isinstance(rec.get("moat"), dict) else {}).get("summary", "") or "",
        reconciliation=[(str(e.get("signal", "")), str(e.get("tension", "")))
                        for e in (rec.get("reconciliation") or [])
                        if isinstance(e, dict)],
        bull_case=th.get("bull_case", "") or "",
        bear_case=th.get("bear_case", "") or "",
        change_my_mind=[str(x) for x in (th.get("what_would_change_my_mind") or [])],
        risks=[_claim(x) for x in (rec.get("risks") or [])],
        red_flags=[_claim(x) for x in (rec.get("red_flags") or [])],
        capital_allocation=rec.get("management_capital_allocation", "") or "",
        call_stance=stance,
        call_label=stance_label(stance) if stance else "",
        call_conviction=((sc.get("conviction") or "") if sc else ""),
        call_rationale=call_rationale,
        call_watch=watch,
        call_decided_without=[str(x) for x in ((sc.get("decided_without") if sc else None) or [])],
    )


def call_one_liner(rec: dict) -> str | None:
    """Plain-text one-liner for the bot reply, or None if no call. Reuses _assessment_vm."""
    vm = _assessment_vm(rec)
    if not vm.call_stance:
        return None
    line = vm.call_label
    if vm.call_conviction:
        line += f" · conviction {vm.call_conviction.title()}"
    if vm.call_watch:
        line += f" — but watch: {vm.call_watch}"
    return line


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
        target_upside=m.upside_to_target(), insider_net_6m=m.insider_net_6m)


def _leader_vm(c: ScoreCard, assessments: dict[str, dict]) -> LeaderVM:
    subs = {s: getattr(c, s, None) for s in SUBS}
    masked = {a["field"] for a in getattr(c, "abstentions", [])
              if isinstance(a, dict) and a.get("scope") == "subscore"
              and a.get("reason") == "inapplicable"}
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
                     assessments: dict[str, dict], macro=None, portfolio=None) -> ReportVM:
    ordered = sorted(cards, key=rank_key, reverse=True)
    return ReportVM(
        session=manifest.session,
        leaders=[_leader_vm(c, assessments) for c in ordered],
        signals=[SignalStatusVM(s.name, s.ran, s.detail) for s in manifest.signals],
        funnel=FunnelVM(manifest.raw, manifest.after_dedup, manifest.after_prefilter,
                        manifest.screened, manifest.dropped_for_budget),
        notes=list(manifest.notes),
        macro=macro,
        portfolio=portfolio)

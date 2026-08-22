"""Renderer-agnostic snapshot of one report. Pure data; no I/O, no optional deps."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from shortlist.models import ScoreCard, rank_key

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
    # Fundamental quality + leverage (already in --json; surfaced in the report too)
    piotroski_f: int | None = None        # Core-6 quality, 0..6
    piotroski_f_legs: int | None = None   # legs evaluated (the "/N" denominator)
    net_debt_to_ebitda: float | None = None   # display-floored to >=0 (net cash -> 0.0x)
    # Short interest (FINRA; conditional display — pairs with the crowded_short flag)
    short_pct_outstanding: float | None = None
    days_to_cover: float | None = None
    short_interest_rising: bool | None = None
    # Earnings execution (Finnhub; conditional display — beat consistency + next report)
    earnings_beats: int | None = None
    earnings_quarters: int | None = None
    earnings_avg_surprise_pct: float | None = None
    earnings_days_to_next: int | None = None


@dataclass
class FindingVM:
    """One grounded claim. `status` is the reader-facing verification state, and the
    four values are NOT interchangeable:

    - `verified`   — the quote was located in one document shown to the model;
    - `unverified` — a quote was offered and could NOT be located (the fabrication
      signal; research/models.py keeps this population separate for that reason);
    - `inference`  — the model declared the claim as its own, quoting nothing. Legal
      in `moat.sources` and `management_findings` ONLY;
    - `unknown`    — the brief predates verification, so nothing was ever checked.
      Rendering this as `unverified` would assert a failure that never happened.
    """
    claim: str = ""
    evidence: str = ""
    source: str = ""       # provenance label of the document that verified it
    status: str = "unknown"


@dataclass
class ReconciliationVM:
    signal: str = ""
    tension: str = ""
    filing_says: str = ""
    source: str = ""
    status: str = "unknown"   # FindingVM statuses, plus "silent" (filing did not address it)


@dataclass
class AssessmentVM:
    business_model: str = ""
    takeaway: str = ""                    # one-line TL;DR (synthesis / thesis.takeaway)
    moat: str = ""                        # moat.summary prose
    reconciliation: list[ReconciliationVM] = field(default_factory=list)
    bull_case: str = ""
    bear_case: str = ""
    change_my_mind: list[str] = field(default_factory=list)
    risks: list[FindingVM] = field(default_factory=list)
    red_flags: list[FindingVM] = field(default_factory=list)
    added_risks: list[FindingVM] = field(default_factory=list)
    moat_sources: list[FindingVM] = field(default_factory=list)
    management_findings: list[FindingVM] = field(default_factory=list)
    capital_allocation: str = ""
    # Straight from the record, never recomputed here: research/assess.py owns the
    # classification and the two populations must not be mixed (see FindingVM).
    unverified_count: int = 0
    inference_count: int = 0
    call_stance: str = ""
    call_label: str = ""
    call_conviction: str = ""
    call_rationale: str = ""
    call_watch: str = ""
    call_decided_without: list[str] = field(default_factory=list)
    # The stance the gate replaced (None when nothing was clamped, and on briefs
    # written before the field existed). See research/models.py:ScreeningCall.
    call_model_stance: str = ""


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
class ReportVM:
    session: date
    leaders: list[LeaderVM]
    notes: list[str] = field(default_factory=list)
    macro: "object | None" = None   # data.macro.MacroContext | None
    portfolio: "object | None" = None   # shortlist.portfolio.PortfolioSummary | None
    deep_block: list[str] = field(default_factory=list)   # non-gated tickers for the /deep handoff


def _status(item: dict, *, inference_ok: bool) -> str:
    """A record written before `verified` existed carries no verification at all, so
    it is `unknown` rather than a failure. An empty quote is a declared inference in
    the two lists where research/assess.py counts it as one, and nowhere else."""
    if "verified" not in item:
        return "unknown"
    if item.get("verified"):
        return "verified"
    if inference_ok and not str(item.get("evidence") or "").strip():
        return "inference"
    return "unverified"


def _finding(x, *, inference_ok: bool) -> FindingVM:
    """Tolerates the bare-string form for the same reason research/models.py does:
    an advisory list must never sink an otherwise-valid brief."""
    if not isinstance(x, dict):
        return FindingVM(claim=str(x))
    return FindingVM(claim=str(x.get("claim", "")), evidence=str(x.get("evidence", "")),
                     source=str(x.get("source", "")),
                     status=_status(x, inference_ok=inference_ok))


def _findings(raw, *, inference_ok: bool = False) -> list[FindingVM]:
    return [_finding(x, inference_ok=inference_ok) for x in (raw or [])]


def _reconciliation(raw) -> list[ReconciliationVM]:
    out = []
    for e in (raw or []):
        if not isinstance(e, dict):
            continue
        silent = str(e.get("verdict") or "") == "silent"
        out.append(ReconciliationVM(
            signal=str(e.get("signal", "")), tension=str(e.get("tension", "")),
            filing_says=str(e.get("filing_says", "")), source=str(e.get("source", "")),
            status="silent" if silent else _status(e, inference_ok=False)))
    return out


def _assessment_vm(rec: dict) -> AssessmentVM:
    from ...research.models import stance_label
    th = rec.get("thesis") or {}
    sc = rec.get("screening_call") if isinstance(rec.get("screening_call"), dict) else None
    cmm = [str(x) for x in (th.get("what_would_change_my_mind") or [])]
    watch = (cmm[0] if cmm else th.get("bear_case", "")) if sc else ""
    stance = sc.get("stance", "") if sc else ""
    model_stance = (sc.get("model_stance") or "") if sc else ""
    if sc and sc.get("stance_clamped"):
        note = sc.get("clamp_note") or "a tripped gate"
        if model_stance:
            call_rationale = f"{note} overrode the model's {stance_label(model_stance)}."
        else:
            call_rationale = f"Auto-downgraded: {note}."
    else:
        call_rationale = (sc.get("rationale") or "") if sc else ""
    mo = rec.get("moat")
    moat = (mo if isinstance(mo, dict) else {}).get("summary", "") or ""
    return AssessmentVM(
        business_model=rec.get("business_model_summary", "") or "",
        takeaway=(rec.get("synthesis") or th.get("takeaway", "") or ""),
        moat=moat,
        reconciliation=_reconciliation(rec.get("reconciliation")),
        bull_case=th.get("bull_case", "") or "",
        bear_case=th.get("bear_case", "") or "",
        change_my_mind=[str(x) for x in (th.get("what_would_change_my_mind") or [])],
        risks=_findings(rec.get("risks")),
        red_flags=_findings(rec.get("red_flags")),
        added_risks=_findings(rec.get("added_risks")),
        moat_sources=_findings((mo if isinstance(mo, dict) else {}).get("sources"),
                               inference_ok=True),
        management_findings=_findings(rec.get("management_findings"), inference_ok=True),
        unverified_count=int(rec.get("unverified_count") or 0),
        inference_count=int(rec.get("inference_count") or 0),
        capital_allocation=rec.get("management_capital_allocation", "") or "",
        call_stance=stance,
        call_label=stance_label(stance) if stance else "",
        call_conviction=((sc.get("conviction") or "") if sc else ""),
        call_rationale=call_rationale,
        call_watch=watch,
        call_decided_without=[str(x) for x in ((sc.get("decided_without") if sc else None) or [])],
        call_model_stance=model_stance,
    )


def call_one_liner(rec: dict) -> str | None:
    """Plain-text one-liner for the bot reply, or None if no call. Reuses _assessment_vm."""
    from ...research.models import stance_label
    vm = _assessment_vm(rec)
    if not vm.call_stance:
        return None
    line = vm.call_label
    if vm.call_model_stance:
        # A clamped call reads as a rule overriding a judgment, not as the model's view.
        line += f" (gate override — model said {stance_label(vm.call_model_stance)})"
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
        target_upside=m.upside_to_target(), insider_net_6m=m.insider_net_6m,
        piotroski_f=m.piotroski_f, piotroski_f_legs=m.piotroski_f_legs,
        # net cash (negative) floors to 0.0x for display, matching the --json output.
        net_debt_to_ebitda=(max(0.0, m.net_debt_to_ebitda)
                            if m.net_debt_to_ebitda is not None else None),
        short_pct_outstanding=m.short_pct_outstanding, days_to_cover=m.days_to_cover,
        short_interest_rising=m.short_interest_rising,
        earnings_beats=m.earnings_beats, earnings_quarters=m.earnings_quarters,
        earnings_avg_surprise_pct=m.earnings_avg_surprise_pct,
        earnings_days_to_next=m.earnings_days_to_next)


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


def build_view_model(cards, session: date, *, assessments: dict[str, dict],
                     macro=None, portfolio=None, notes=None) -> ReportVM:
    ordered = sorted(cards, key=rank_key, reverse=True)
    leaders = [_leader_vm(c, assessments) for c in ordered]
    # /deep handoff: non-gated, scored leaders only (a gated/not-scored name can't pass),
    # in conviction (rank_key) order. Suppressed for /portfolio reports — suggesting you
    # /deep names you already hold is noise (the section is for discovery/screen digests).
    #
    # A name that ALREADY has an assessment in this very report is excluded, for the same
    # reason. Without that, a `/deep LULU` report ended its own brief by telling you to run
    # `/deep LULU` — the command that produced it. It also fixes `--research N`, which used
    # to re-suggest the top N names it had just spent ~$0.60 and ~3 minutes each
    # researching; now the handoff lists only the names still worth the spend.
    deep_block = ([ld.ticker for ld in leaders
                   if not ld.gates and ld.scored and ld.ticker not in (assessments or {})]
                  if portfolio is None else [])
    return ReportVM(
        session=session,
        leaders=leaders,
        notes=list(notes or []),
        macro=macro,
        portfolio=portfolio,
        deep_block=deep_block)

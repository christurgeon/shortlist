from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

TRAJECTORIES = ("widening", "stable", "eroding")

VERDICTS = ("confirms", "contradicts", "silent")

# Reconciliation `signal` taxonomy (see spec §3.2). Card sub-score axes + two
# synthetic/derived tokens, plus namespaced gate:/flag: tokens. The four event
# flags (activist_13d…) are presence-based and harness-only — NOT config-derived,
# so they must be hardcoded here, not assembled from config.
_AXIS_SIGNALS = ("quality", "moat", "growth", "momentum", "value", "insider",
                 "risk", "short_interest", "narrative_tone")
_GATE_SIGNALS = ("negative_fcf", "below_min_mktcap", "over_leveraged",
                 "heavy_insider_selling")
_FLAG_SIGNALS = ("crowded_short", "insider_cluster_buy", "planned_sale",
                 "value_trap", "activist_13d", "recent_8k", "passive_13g",
                 "planned_insider_sale_144")


def default_valid_signals() -> set[str]:
    """The set of reconciliation `signal` tokens accepted by the parser."""
    return (set(_AXIS_SIGNALS)
            | {f"gate:{g}" for g in _GATE_SIGNALS}
            | {f"flag:{f}" for f in _FLAG_SIGNALS})

# The JSON shape the model is instructed to emit (meta fields are added by us).
SCHEMA_HINT = """{
  "business_model_summary": "string",
  "moat": {"summary": "string", "sources": ["string"], "trajectory": "widening|stable|eroding"},
  "risks": [{"claim": "string", "evidence": "verbatim quote from the filing"}],
  "red_flags": [{"claim": "string", "evidence": "verbatim quote from the filing"}],
  "management_capital_allocation": "string",
  "reconciliation": [{"signal": "value|growth|moat|quality|momentum|insider|risk|short_interest|narrative_tone|gate:<name>|flag:<name>", "tension": "one sentence: the number vs the narrative", "filing_says": "verbatim quote, or \\"\\" if the filing is silent", "verdict": "confirms|contradicts|silent"}],
  "thesis": {"bull_case": "string (1-2 sentences)", "bear_case": "string (1-2 sentences)", "what_would_change_my_mind": ["string"], "takeaway": "string (1-2 sentences)"}
}"""

_REQUIRED = ("business_model_summary", "moat", "risks", "red_flags",
             "management_capital_allocation")


@dataclass
class FilingText:
    ticker: str
    accession: str
    filing_date: str
    business: str = ""
    mda: str = ""
    risk_factors: str = ""

    def combined(self) -> str:
        return "\n\n".join(s for s in (self.business, self.mda, self.risk_factors) if s)

    def has_content(self) -> bool:
        return bool(self.business or self.mda or self.risk_factors)


@dataclass
class Finding:
    claim: str
    evidence: str
    verified: bool = False


@dataclass
class Moat:
    summary: str = ""
    sources: list[str] = field(default_factory=list)
    trajectory: Optional[str] = None  # one of TRAJECTORIES, or None


@dataclass
class Conflict:
    signal: str
    tension: str
    filing_says: str = ""          # verbatim quote; "" iff verdict == "silent"
    verdict: str = "silent"        # one of VERDICTS
    verified: bool = False         # set by _verify_grounding (non-silent only)


@dataclass
class Thesis:
    bull_case: str = ""
    bear_case: str = ""
    what_would_change_my_mind: list[str] = field(default_factory=list)
    takeaway: str = ""             # the traveling TL;DR (replaces old `synthesis`)


@dataclass
class QualitativeAssessment:
    ticker: str
    as_of: str
    filing_accession: str
    filing_date: str
    model: str
    cost_usd: Optional[float] = None
    stop_reason: Optional[str] = None
    business_model_summary: str = ""
    moat: Moat = field(default_factory=Moat)
    risks: list[Finding] = field(default_factory=list)
    red_flags: list[Finding] = field(default_factory=list)
    management_capital_allocation: str = ""
    reconciliation: list[Conflict] = field(default_factory=list)
    thesis: Thesis = field(default_factory=Thesis)
    unverified_count: int = 0
    silent_count: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def synthesis(self) -> str:
        """Back-compat: the old flat synthesis is now the thesis takeaway.
        A read-only property — NOT a dataclass field, so dataclasses.asdict()
        does not serialize it (see report.write for the on-disk injection)."""
        return self.thesis.takeaway


def _findings(payload: dict, key: str) -> list[Finding]:
    out: list[Finding] = []
    for item in (payload.get(key) or []):
        if not isinstance(item, dict):
            raise ValueError(f"{key} items must be objects")
        out.append(Finding(claim=str(item.get("claim", "")),
                            evidence=str(item.get("evidence", ""))))
    return out


def _thesis(payload: dict, max_falsifiers: int = 3) -> "Thesis":
    """Build a Thesis from payload['thesis']. Presence is enforced HERE (must be a
    dict) — NOT via _REQUIRED — mirroring the moat dict-check. Sub-fields default."""
    raw = payload.get("thesis")
    if not isinstance(raw, dict):
        raise ValueError("thesis must be an object")
    cmm = [str(x) for x in (raw.get("what_would_change_my_mind") or [])][:max_falsifiers]
    return Thesis(
        bull_case=str(raw.get("bull_case", "")),
        bear_case=str(raw.get("bear_case", "")),
        what_would_change_my_mind=cmm,
        takeaway=str(raw.get("takeaway", "")),
    )


def _reconciliation(payload: dict, *, valid_signals: set[str],
                    max_conflicts: int = 3) -> list[Conflict]:
    """Build the reconciliation list, best-effort. Fully optional: a missing key →
    []. A malformed/unresolved-signal conflict is DROPPED (never raises, unlike
    _findings). Bad verdict is coerced to 'silent'. Truncated to max_conflicts."""
    out: list[Conflict] = []
    for item in (payload.get("reconciliation") or []):
        if not isinstance(item, dict):
            continue
        signal = str(item.get("signal", ""))
        if signal not in valid_signals:
            continue
        verdict = item.get("verdict")
        if verdict not in VERDICTS:
            verdict = "silent"
        out.append(Conflict(
            signal=signal,
            tension=str(item.get("tension", "")),
            filing_says=str(item.get("filing_says", "")),
            verdict=verdict,
        ))
        if len(out) >= max_conflicts:
            break
    return out


def assessment_from_payload(payload: dict, *, ticker: str, as_of: str, accession: str,
                            filing_date: str, model: str, cost_usd: Optional[float],
                            stop_reason: Optional[str],
                            valid_signals: Optional[set[str]] = None,
                            max_conflicts: int = 3,
                            max_falsifiers: int = 3) -> QualitativeAssessment:
    """Build a QualitativeAssessment from the model's parsed JSON.
    Raises ValueError if required keys are missing/mistyped or thesis is not a dict."""
    missing = [k for k in _REQUIRED if k not in payload]
    if missing:
        raise ValueError(f"missing keys: {missing}")
    moat_raw = payload["moat"]
    if not isinstance(moat_raw, dict):
        raise ValueError("moat must be an object")
    trajectory = moat_raw.get("trajectory")
    moat = Moat(
        summary=str(moat_raw.get("summary", "")),
        sources=[str(s) for s in (moat_raw.get("sources") or [])],
        trajectory=trajectory if trajectory in TRAJECTORIES else None,
    )
    vs = valid_signals if valid_signals is not None else default_valid_signals()
    return QualitativeAssessment(
        ticker=ticker, as_of=as_of, filing_accession=accession, filing_date=filing_date,
        model=model, cost_usd=cost_usd, stop_reason=stop_reason,
        business_model_summary=str(payload["business_model_summary"]),
        moat=moat,
        risks=_findings(payload, "risks"),
        red_flags=_findings(payload, "red_flags"),
        management_capital_allocation=str(payload["management_capital_allocation"]),
        reconciliation=_reconciliation(payload, valid_signals=vs, max_conflicts=max_conflicts),
        thesis=_thesis(payload, max_falsifiers=max_falsifiers),
    )

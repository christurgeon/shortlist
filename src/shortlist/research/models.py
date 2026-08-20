from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

TRAJECTORIES = ("widening", "stable", "eroding")

VERDICTS = ("confirms", "contradicts", "silent")

STANCES = ("STRONG_BUY", "BUY", "HOLD", "AVOID", "STRONG_AVOID")  # most-bullish-first
CONVICTIONS = ("HIGH", "MEDIUM", "LOW")

DEFAULT_STANCE_LABELS = {
    "STRONG_BUY": "Strong Buy", "BUY": "Buy", "HOLD": "Hold",
    "AVOID": "Avoid", "STRONG_AVOID": "Strong Avoid",
}
DEFAULT_DISCLAIMER = "screen only — not advice"


def _sc_cfg(config: dict | None) -> dict:
    return ((config or {}).get("research") or {}).get("screening_call") or {}


def stance_label(stance: str, config: dict | None = None) -> str:
    labels = _sc_cfg(config).get("labels") or {}
    # a config label of "" is treated as absent (falls back to the default)
    return labels.get(stance) or DEFAULT_STANCE_LABELS.get(stance, stance)


def call_disclaimer(config: dict | None = None) -> str:
    return _sc_cfg(config).get("disclaimer") or DEFAULT_DISCLAIMER


# Reconciliation `signal` taxonomy
# (docs/superpowers/specs/2026-06-03-research-reconciliation-thesis-design.md §3.2).
# Card sub-score axes + two
# synthetic/derived tokens, plus namespaced gate:/flag: tokens. The four event
# flags (activist_13d…) are presence-based and harness-only — NOT config-derived,
# so they must be hardcoded here, not assembled from config.
_AXIS_SIGNALS = ("quality", "moat", "growth", "momentum", "value", "insider",
                 "risk", "short_interest", "narrative_tone", "governance")
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
  "moat": {"summary": "string", "sources": [{"claim": "string", "evidence": "verbatim quote, or \\"\\" if this is your inference rather than something the filing states"}], "trajectory": "widening|stable|eroding"},
  "risks": [{"claim": "string", "evidence": "verbatim quote from the filing"}],
  "red_flags": [{"claim": "string", "evidence": "verbatim quote from the filing"}],
  "added_risks": [{"claim": "string (a risk newly disclosed vs the prior year)", "evidence": "verbatim quote from the NEWLY ADDED RISK FACTORS section"}],
  "management_capital_allocation": "string (your JUDGMENT of capital-allocation quality — do NOT enumerate figures here, they belong in management_findings)",
  "management_findings": [{"claim": "one specific, checkable capital-allocation fact", "evidence": "verbatim quote, or \\"\\" if this is your inference rather than something the filing states"}],
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
class EightKText:
    """One recent 8-K's extracted substance (research/eightk.py). Unlike the
    proxy/reverse-DCF context lines this IS filing text, so it enters the grounding
    haystack — but as its OWN segment, carrying `label` (e.g.
    "8-K 2026-07-30 (Item 2.02, EX-99.1)") so a reader is told which document a
    verified quote came from rather than assuming the 10-K."""
    accession: str
    filed: str
    items: str
    label: str
    text: str


@dataclass
class DebtNote:
    """One debt & liquidity statement note (research/notes.py). Like EightKText
    this IS filing text, so it enters the grounding haystack — as its OWN segment,
    carrying `label` (e.g. "10-K note: LONG-TERM OBLIGATIONS") so a reader is told
    which document a verified quote came from rather than assuming the 10-K.

    `text` is PURE FILING TEXT and nothing else may be mixed into it — it is a
    haystack segment, so anything added becomes quotable and can pass
    quote-verification as a filing fact. An earlier revision appended a
    " […truncated…]" marker here; normalized it was 13 chars against
    `assess._MIN_EVIDENCE_CHARS = 12`, so a model emitting the marker alone as its
    evidence got `verified=True` against a real note.

    `truncated` is therefore just a FLAG: `assess._build_user_prompt` renders it as a
    "(TRUNCATED …)" suffix on the prompt section header, which is scaffolding outside
    every segment. That is what lets the model distinguish a severed maturity ladder
    from a complete one, which SYSTEM_PROMPT's "name the missing input rather than
    estimating it" depends on."""
    form: str                      # "10-K" | "10-Q"
    accession: str
    title: str                     # as filed, e.g. "Borrowings and Credit Agreements"
    label: str
    text: str
    truncated: bool = False


@dataclass
class FilingBundle:
    """The documents fed to one research brief. `tenk` is the current 10-K (the
    primary, displayed filing). `tenq_mda` and `added_risks_text` are additive
    context (may be ""). `cache_key` is the FILING half of the on-disk key — the
    accessions, so a new 10-Q produces a fresh brief — not the whole key: the full
    on-disk key also folds in a prompt/config fingerprint and a context digest
    (`research/cachekey.py:brief_key`). The prior-year 10-K (the diff baseline) is
    deliberately NOT part of `cache_key` and is never exposed here whole."""
    tenk: FilingText
    primary_accession: str
    cache_key: str
    filing_date: str
    tenq_mda: str = ""
    added_risks_text: str = ""
    text_similarity: Optional[float] = None
    # ^ Lazy-Prices YoY cosine (Item 1A + MD&A) vs the prior-year 10-K, computed in
    # fetch_bundle from documents already fetched for the risk diff. PROMPT-ONLY:
    # it is a computed number, not filing text, so it must never enter haystack()
    # or a model could quote it through quote-verification as a filing fact.
    eightks: list = field(default_factory=list)
    # ^ list[EightKText] — recent 8-K substance (research/eightk.py). Empty unless
    # `research.eightk` is enabled AND a qualifying filing exists, which is what
    # keeps the prompt and the report byte-identical for every other name.
    tenq_accession: str = ""
    # ^ set whenever a 10-Q was LOCATED, independently of whether its MD&A parsed.
    # `tenq_accession and not tenq_mda` is the "we found the filing but could not read
    # Item 2" case (2.19% of 228 filings measured 2026-08-14), which must be reported
    # as a data gap rather than silently omitted — see
    # docs/audits/2026-08-14-tenq-mda-recovery-kill.md.
    tenq_added_risks: str = ""
    # ^ Part II Item 1A blocks absent from the 10-K's Item 1A — the quarter's risk
    # CHANGES (research/filings.py:_tenq_added_risks). Filing text, so unlike
    # `text_similarity` it DOES enter the haystack — as its own segment, so a
    # verified quote is attributed to the 10-Q and not to the 10-K.
    debt_notes: list = field(default_factory=list)
    # ^ list[DebtNote] — debt & liquidity statement notes (research/notes.py), the
    # maturity-ladder input SYSTEM_PROMPT's refinancing-coverage arithmetic needs.
    # Empty unless `research.notes` is enabled AND the filer files one, which is
    # what keeps the prompt byte-identical for every other name. 10-K notes first:
    # a 10-Q debt note is a legitimate subset (5 of 20 filers file none at all).

    def segments(self) -> list[tuple[str, str]]:
        """The grounding corpus split into (provenance label, text) pairs, in the
        order they reach the model. haystack() is the join of these, so adding a
        segment cannot change what verifies — only what it is attributed to.
        Empty texts are dropped so a label can never match the empty string."""
        parts = [("10-K", self.tenk.combined()),
                 ("10-Q MD&A", self.tenq_mda),
                 ("10-Q Part II Item 1A", self.tenq_added_risks),
                 ("newly disclosed risks", self.added_risks_text)]
        parts += [(e.label, e.text) for e in self.eightks]
        parts += [(n.label, n.text) for n in self.debt_notes]
        return [(label, text) for label, text in parts if text]

    def haystack(self) -> str:
        """All text shown to the model — the grounding corpus. Excludes the
        prior-year 10-K (never shown), includes the current 10-K + 10-Q MD&A +
        added-risk blocks + any recent 8-K substance."""
        return "\n\n".join(text for _, text in self.segments())


@dataclass
class Finding:
    claim: str
    evidence: str
    verified: bool = False
    source: str = ""               # provenance label of the segment that verified it ("" if none)


@dataclass
class Moat:
    summary: str = ""
    # {claim, evidence} pairs, NOT bare strings: 17 of 38 moat sources measured on
    # 2026-08-17 asserted a specific figure with no grounding anywhere in the brief.
    # An empty `evidence` is valid here (declared inference) — see _evidence_pairs.
    sources: list["Finding"] = field(default_factory=list)
    trajectory: Optional[str] = None  # one of TRAJECTORIES, or None


@dataclass
class Conflict:
    signal: str
    tension: str
    filing_says: str = ""          # verbatim quote; "" iff verdict == "silent"
    verdict: str = "silent"        # one of VERDICTS
    verified: bool = False         # set by _verify_grounding (non-silent only)
    source: str = ""               # provenance label of the segment that verified it ("" if none)


@dataclass
class Thesis:
    bull_case: str = ""
    bear_case: str = ""
    what_would_change_my_mind: list[str] = field(default_factory=list)
    takeaway: str = ""             # the traveling TL;DR (replaces old `synthesis`)


@dataclass
class ScreeningCall:
    stance: str = "HOLD"                  # one of STANCES (post-clamp)
    conviction: str = "LOW"               # one of CONVICTIONS (post-cap)
    rationale: str = ""                   # one sentence: the why
    # Python-owned, authoritative — never from the model:
    decided_without: list[str] = field(default_factory=list)
    not_applicable: list[str] = field(default_factory=list)
    conviction_capped: bool = False
    stance_clamped: bool = False
    clamp_note: str = ""                  # e.g. "tripped negative_fcf gate"
    as_of_price: Optional[float] = None   # snapshot for future hit-rate
    # ScoreCard.confidence at decision time. It drives two of the three conviction
    # guards, so without it a retrospective cannot tell whether a conviction was the
    # model's own or a rule's (see docs/audits/2026-08-04-deep-brief-assessment.md D4).
    confidence: Optional[float] = None


def _screening_call(payload: dict) -> Optional[ScreeningCall]:
    """Lenient, OPTIONAL parse of payload['call']. Missing/malformed -> None (never
    raises, unlike _thesis) so a brief is never dropped over its capstone. Stance/
    conviction are normalized (upper-cased, spaces->underscores) then validated;
    unknown values coerce to HOLD/LOW."""
    raw = payload.get("call")
    if not isinstance(raw, dict):
        return None
    stance = str(raw.get("stance") or "").strip().upper().replace(" ", "_")
    conviction = str(raw.get("conviction") or "").strip().upper()
    return ScreeningCall(
        stance=stance if stance in STANCES else "HOLD",
        conviction=conviction if conviction in CONVICTIONS else "LOW",
        rationale=str(raw.get("rationale") or ""),
    )


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
    management_findings: list[Finding] = field(default_factory=list)
    reconciliation: list[Conflict] = field(default_factory=list)
    thesis: Thesis = field(default_factory=Thesis)
    unverified_count: int = 0
    silent_count: int = 0
    # Claims that declared themselves unquoted (empty evidence) in the two lists
    # where that is legal. Deliberately NOT folded into unverified_count: that
    # number means "the model quoted something absent from the filing" — a
    # fabrication signal — and mixing populations would destroy it.
    inference_count: int = 0
    notes: list[str] = field(default_factory=list)
    added_risks: list[Finding] = field(default_factory=list)
    cache_key: str = ""        # assess() sets this to the narrow FILING key (falls back to
                               # filing_accession); research/__init__.py:_enrich_card then
                               # overwrites it with the WIDE on-disk key (filing + prompt/config
                               # fingerprint + context digest + day bucket) before report.write,
                               # so the two never diverge on disk
    text_similarity: Optional[float] = None   # Lazy-Prices YoY cosine; None == not computed
    screening_call: Optional[ScreeningCall] = None

    @property
    def synthesis(self) -> str:
        """Back-compat: the old flat synthesis is now the thesis takeaway.
        A read-only property — NOT a dataclass field, so dataclasses.asdict()
        does not serialize it (see report.write for the on-disk injection)."""
        return self.thesis.takeaway


def _finding_from(item: dict) -> Finding:
    """Build a Finding from a payload item's claim/evidence (missing -> empty)."""
    return Finding(claim=str(item.get("claim", "")),
                   evidence=str(item.get("evidence", "")))


def _evidence_pairs(raw, limit: Optional[int] = None) -> list[Finding]:
    """Tolerant parse of the two lists where an empty quote is legal (`moat.sources`,
    `management_findings`). Accepts BOTH shapes on purpose:

    - a bare string is the pre-2026-08-17 form — claim only, no quote;
    - a dict is the {claim, evidence} form.

    Anything else is skipped. Tolerance is not a style choice here: `_finding_from`
    raises AttributeError on a str, and `assess.py`'s parse-retry catches only
    ValueError/JSONDecodeError — so a model that drifts back to the legacy shape
    would escape the retry and drop the entire brief. `_added_risks` sets the
    precedent: an advisory list must never sink an otherwise-valid brief.
    """
    out: list[Finding] = []
    for item in (raw or []):
        if isinstance(item, str):
            out.append(Finding(claim=item, evidence=""))
        elif isinstance(item, dict):
            out.append(_finding_from(item))
        else:
            continue
        if limit is not None and len(out) >= limit:
            break
    return out


def _findings(payload: dict, key: str) -> list[Finding]:
    out: list[Finding] = []
    for item in (payload.get(key) or []):
        if not isinstance(item, dict):
            raise ValueError(f"{key} items must be objects")
        out.append(_finding_from(item))
    return out


def _added_risks(payload: dict, limit: int) -> list[Finding]:
    """Tolerant parse of the optional `added_risks` list (YoY new risks). Missing
    key -> []; malformed (non-dict) items are skipped, never raised — this advisory
    section must not sink an otherwise-valid brief. Truncated to `limit`."""
    out: list[Finding] = []
    for item in (payload.get("added_risks") or []):
        if not isinstance(item, dict):
            continue
        out.append(_finding_from(item))
        if len(out) >= limit:
            break
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
                            max_falsifiers: int = 3,
                            max_added_risks: int = 8,
                            max_moat_sources: int = 6,
                            max_management_findings: int = 6) -> QualitativeAssessment:
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
        sources=_evidence_pairs(moat_raw.get("sources"), max_moat_sources),
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
        management_findings=_evidence_pairs(payload.get("management_findings"),
                                            max_management_findings),
        reconciliation=_reconciliation(payload, valid_signals=vs, max_conflicts=max_conflicts),
        thesis=_thesis(payload, max_falsifiers=max_falsifiers),
        added_risks=_added_risks(payload, max_added_risks),
    )

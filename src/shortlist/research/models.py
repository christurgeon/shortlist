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
    # An empty label falls back to the default.
    return labels.get(stance) or DEFAULT_STANCE_LABELS.get(stance, stance)


def call_disclaimer(config: dict | None = None) -> str:
    return _sc_cfg(config).get("disclaimer") or DEFAULT_DISCLAIMER


# Reconciliation `signal` taxonomy: card sub-score axes + two
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
  "business_model_summary": "string (how the company makes money, in words — do NOT state figures here)",
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

    def stub_sections(self) -> list[str]:
        """Labels of the sections too short to be the item's real text."""
        return [label for name, label in _SECTION_LABELS
                if len(getattr(self, name)) < STUB_SECTION_CHARS]


# A section under this length is a stub (an MD&A cross-reference, an empty Item 1, a
# "Not applicable" Item 1A), and has_content() still passes it. The brief flags stubs
# instead of abstaining, since the rest of the brief is usually sound. A length floor
# cannot catch a span of the WRONG text.
STUB_SECTION_CHARS = 2000

_SECTION_LABELS = (("business", "Item 1 (Business)"),
                   ("mda", "Item 7 (MD&A)"),
                   ("risk_factors", "Item 1A (Risk factors)"))


def stub_sections_note(sections: list[str]) -> str:
    """The reader-facing wording, shared by the markdown brief and the bot report."""
    if not sections:
        return ""
    return (f"Not extracted from the 10-K: {', '.join(sections)} — the filing points "
            "elsewhere or omits it, so the model never saw that text. A claim it "
            "marks as unaddressed there may be in the filing.")


@dataclass
class EightKText:
    """One recent 8-K's extracted substance (research/eightk.py). Filing text, so it
    enters the grounding haystack as its OWN segment. `label` (e.g. "8-K 2026-07-30
    (Item 2.02, EX-99.1)") tells the reader which document a verified quote came from."""
    accession: str
    filed: str
    items: str
    label: str
    text: str


@dataclass
class DebtNote:
    """One debt & liquidity statement note (research/notes.py). Filing text, so it
    enters the grounding haystack as its OWN segment. `label` (e.g. "10-K note:
    LONG-TERM OBLIGATIONS") tells the reader which document a verified quote came from.

    `text` is PURE FILING TEXT: anything mixed in becomes quotable and could pass
    quote-verification as a filing fact. `truncated` is therefore only a FLAG;
    `assess._build_user_prompt` renders it as a "(TRUNCATED …)" suffix on the prompt
    header, outside every segment, so the model can tell a severed maturity ladder from a
    complete one."""
    form: str                      # "10-K" | "10-Q"
    accession: str
    title: str                     # as filed, e.g. "Borrowings and Credit Agreements"
    label: str
    text: str
    truncated: bool = False


@dataclass
class ControlsFinding:
    """Management's own conclusion that internal controls were not effective as of
    the filing's period end (research/controls.py).

    `quote` is PURE FILING TEXT and a grounding segment, so, as for DebtNote, nothing
    computed may be mixed into it. `basis` and `as_of` are computed, so they travel in
    the prompt-only `controls.context_line`.
    """
    form: str                      # "10-K"
    accession: str
    basis: str                     # "icfr" | "dcp" — which conclusion was adverse
    as_of: str                     # YYYY-MM-DD the conclusion anchors to
    label: str                     # e.g. "10-K controls conclusion"
    quote: str


@dataclass
class FilingBundle:
    """The documents fed to one research brief. `tenk` is the current, displayed 10-K.
    `tenq_mda` and `added_risks_text` are additive context (may be ""). `cache_key` is
    only the FILING half of the on-disk key (the accessions); the rest is in
    `research/cachekey.py:brief_key`. The prior-year 10-K (diff baseline) is never
    exposed here whole."""
    tenk: FilingText
    primary_accession: str
    cache_key: str
    filing_date: str
    tenq_mda: str = ""
    added_risks_text: str = ""
    text_similarity: Optional[float] = None
    # ^ YoY cosine vs the prior-year 10-K, computed in fetch_bundle. PROMPT-ONLY: a
    # computed number must never enter haystack().
    eightks: list = field(default_factory=list)
    # ^ list[EightKText] (research/eightk.py). Empty keeps the prompt and report
    # byte-identical.
    tenq_accession: str = ""
    # ^ set whenever a 10-Q was LOCATED, whether or not its MD&A parsed.
    # `tenq_accession and not tenq_mda` means the filing was found but Item 2 could not
    # be read; report that as a data gap. See docs/audits/2026-08-14-tenq-mda-recovery-kill.md.
    tenq_added_risks: str = ""
    # ^ Part II Item 1A blocks absent from the 10-K's Item 1A
    # (research/filings.py:_tenq_added_risks). Filing text, so unlike `text_similarity`
    # it enters the haystack, as its own segment.
    debt_notes: list = field(default_factory=list)
    # ^ list[DebtNote] (research/notes.py). Empty keeps the prompt byte-identical.
    # 10-K notes first; a 10-Q may legitimately carry none.
    controls: Optional["ControlsFinding"] = None
    # ^ an ADVERSE internal-control conclusion, or None (the common case). None keeps
    # the prompt byte-identical.

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
        if self.controls is not None:
            parts.append((self.controls.label, self.controls.quote))
        return [(label, text) for label, text in parts if text]

    def haystack(self) -> str:
        """All text shown to the model — the grounding corpus. Excludes the
        prior-year 10-K (never shown), includes the current 10-K + 10-Q MD&A +
        added-risk blocks + any recent 8-K substance."""
        return "\n\n".join(text for _, text in self.segments())


@dataclass
class NewsItem:
    """One event cluster from the company-news feed, already sanitized (research/news.py)."""
    date: str                  # first report in the cluster, UTC YYYY-MM-DD
    source: str
    headline: str
    summary: str = ""
    url: str = ""              # http(s) only, else ""
    reports: int = 1           # articles in the cluster


@dataclass
class Finding:
    claim: str
    evidence: str
    verified: bool = False
    source: str = ""               # provenance label of the segment that verified it ("" if none)


@dataclass
class Moat:
    summary: str = ""
    # {claim, evidence} pairs, NOT bare strings, so claims can be grounded.
    # An empty `evidence` is valid here (declared inference); see _evidence_pairs.
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
    # ScoreCard.confidence at decision time; it drives two conviction guards, so a
    # retrospective can attribute a conviction (docs/audits/2026-08-04-deep-brief-assessment.md D4).
    confidence: Optional[float] = None
    # What the MODEL said before apply_guards overwrote `stance`/`conviction`. Python-owned,
    # never parsed from the payload. `clamp_note` names only the gates, so these tell
    # "the gate agreed" from "the gate overruled a Buy". None means the guard did not fire
    # or the brief is older; readers must treat both the same (omit the line).
    model_stance: Optional[str] = None       # pre-clamp stance, set iff stance_clamped
    model_conviction: Optional[str] = None   # pre-cap conviction, set iff conviction_capped


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
    # Declared-inference claims (empty evidence). NOT folded into unverified_count, which
    # is the fabrication signal.
    inference_count: int = 0
    notes: list[str] = field(default_factory=list)
    added_risks: list[Finding] = field(default_factory=list)
    cache_key: str = ""        # narrow filing key from assess(); _enrich_card overwrites it
                               # with the wide on-disk key before report.write
    text_similarity: Optional[float] = None   # Lazy-Prices YoY cosine; None == not computed
    stub_sections: list[str] = field(default_factory=list)  # FilingText.stub_sections() of the 10-K
    # The "ROIC persistence" section (research/durability.py) and its status code.
    # Computed AFTER assess() returns; never in the prompt or a grounding segment.
    durability_line: str = ""
    durability_status: str = ""
    # The "Recent news" section (research/news.py), fetched AFTER assess() returns.
    # Status "" means the section was off.
    news_items: list[NewsItem] = field(default_factory=list)
    news_status: str = ""
    news_as_of: str = ""
    news_from: str = ""             # first date the items can come from
    news_truncated: bool = False    # the feed's article cap cut the window short
    screening_call: Optional[ScreeningCall] = None

    @property
    def synthesis(self) -> str:
        """The thesis takeaway. Not a dataclass field, so asdict() omits it; see report.write."""
        return self.thesis.takeaway


def _finding_from(item: dict) -> Finding:
    """Build a Finding from a payload item's claim/evidence (missing -> empty)."""
    return Finding(claim=str(item.get("claim", "")),
                   evidence=str(item.get("evidence", "")))


def _evidence_pairs(raw, limit: Optional[int] = None) -> list[Finding]:
    """Tolerant parse of the two lists where an empty quote is legal (`moat.sources`,
    `management_findings`). Accepts BOTH shapes on purpose:

    - a bare string is the legacy form: claim only, no quote;
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


def _findings(payload: dict, key: str, limit: Optional[int] = None) -> list[Finding]:
    out: list[Finding] = []
    for item in (payload.get(key) or []):
        if not isinstance(item, dict):
            raise ValueError(f"{key} items must be objects")
        out.append(_finding_from(item))
        if limit is not None and len(out) >= limit:
            break
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
                            max_risks: int = 8,
                            max_red_flags: int = 8,
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
        risks=_findings(payload, "risks", max_risks),
        red_flags=_findings(payload, "red_flags", max_red_flags),
        management_capital_allocation=str(payload["management_capital_allocation"]),
        management_findings=_evidence_pairs(payload.get("management_findings"),
                                            max_management_findings),
        reconciliation=_reconciliation(payload, valid_signals=vs, max_conflicts=max_conflicts),
        thesis=_thesis(payload, max_falsifiers=max_falsifiers),
        added_risks=_added_risks(payload, max_added_risks),
    )

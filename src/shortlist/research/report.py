from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import Optional

from .models import Finding, QualitativeAssessment, call_disclaimer, stance_label, stub_sections_note


def _safe(accession: str) -> str:
    return (accession or "unknown").replace("/", "-")


def brief_path(ticker: str, accession: str, root) -> Path:
    return Path(root) / ticker.upper() / f"{_safe(accession)}.md"


def record_path(ticker: str, accession: str, root) -> Path:
    return Path(root) / ticker.upper() / f"{_safe(accession)}.json"


def is_cached(ticker: str, accession: str, root) -> bool:
    """A brief for this exact key exists. `accession` is the wide cache key from
    `research/cachekey.py:brief_key`, not a bare filing accession."""
    return brief_path(ticker, accession, root).exists()


def _findings_md(findings, empty_label: str) -> list[str]:
    if not findings:
        return [f"- {empty_label}"]
    lines = []
    for f in findings:
        # Coerce a bare string, never raise: rendering must not drop a brief.
        if isinstance(f, str):
            f = Finding(claim=f, evidence="")
        # Three states. A declared inference (empty quote) must not read as a failed
        # grounding check. The label avoids "analyst inference": the claim may be a
        # filing fact from a section never sent to the model (see docs/RESEARCH.md).
        if not f.verified and not (f.evidence or "").strip():
            lines.append(f"- **{f.claim}** _(unquoted — inference or from a section "
                         "not provided)_")
            continue
        mark = "" if f.verified else " _(unverified)_"
        # Name the source document only when it is not the 10-K.
        source = getattr(f, "source", "") or ""
        if f.verified and source and source != "10-K":
            mark = f" _— verified against {source}_"
        lines.append(f"- **{f.claim}**{mark}")
        if f.evidence:
            lines.append(f"  > {f.evidence}")
    return lines


def _reconciliation_md(conflicts) -> list[str]:
    if not conflicts:
        return ["- None — numbers and filing not reconciled."]
    lines = []
    for c in conflicts:
        if c.verdict == "silent":
            mark = " _(filing silent)_"
        else:
            mark = "" if c.verified else " _(unverified)_"
        lines.append(f"- **{c.signal}** ({c.verdict}) — {c.tension}{mark}")
        if c.filing_says:
            lines.append(f"  > {c.filing_says}")
    return lines


def _watch_line(a) -> str:
    t = a.thesis
    if t.what_would_change_my_mind:
        return t.what_would_change_my_mind[0]
    return t.bear_case or ""


def _call_md(a, config=None):
    """Return (badge_line, block_lines) for the screening call, or None if no call."""
    c = a.screening_call
    if c is None:
        return None
    label = stance_label(c.stance, config)
    disc = call_disclaimer(config)
    watch = _watch_line(a)
    badge = f"> **SCREENING CALL: {label}** · conviction {c.conviction.title()}"
    if watch:
        badge += f" · _but watch: {watch}_"
    badge += f" · {disc}"
    block = ["", "## Screening call _(triage — not investment advice)_",
             f"- **Call:** {label} · **conviction** {c.conviction.title()}"]
    if c.stance_clamped:
        # Name the stance the gate replaced: only "Avoid, but the model said Buy" means a
        # rule overrode a judgment. model_stance is None on older briefs.
        if c.model_stance:
            was = stance_label(c.model_stance, config)
            why = (f"the {c.clamp_note} overrode the model's {was}" if c.clamp_note
                   else f"a tripped gate overrode the model's {was}")
        else:
            why = (f"Auto-downgraded: {c.clamp_note}." if c.clamp_note
                   else "Auto-downgraded by a tripped gate.")
        block.append(f"- **Why:** {why}")
        if c.rationale:
            block.append(f"- _Model's pre-clamp view: {c.rationale}_")
    elif c.rationale:
        block.append(f"- **Why:** {c.rationale}")
    if c.confidence is not None:
        block.append(f"- **Data confidence:** {c.confidence:.2f}")
    if watch:
        block.append(f"- **But watch:** {watch}")
    if c.decided_without:
        block.append(f"- **Decided without:** {'; '.join(c.decided_without)}")
    if c.not_applicable:
        block.append(f"- **Not applicable:** {'; '.join(c.not_applicable)}")
    if c.conviction_capped and not c.stance_clamped:
        was = f" (the model said {c.model_conviction.title()})" if c.model_conviction else ""
        block.append(f"- _Conviction capped by data coverage / corroboration{was}._")
    return badge, block


def to_markdown(a: QualitativeAssessment, config=None,
                capital_allocation: Optional[tuple[str, str]] = None) -> str:
    """`capital_allocation` is research/capital_allocation.py's (body, status); None omits it."""
    t = a.thesis
    rendered = _call_md(a, config)
    call_badge, call_block = rendered if rendered is not None else ("", [])
    cmm = [f"- {x}" for x in t.what_would_change_my_mind] or ["- (none stated)"]
    lines = [
        f"# {a.ticker} — qualitative read",
        "",
        f"> **LLM-generated** from {a.filing_accession} ({a.filing_date}) by "
        f"`{a.model}`. Verify against the source filing. Not investment advice.",
        "",
        *([f"> **{stub_sections_note(a.stub_sections)}**", ""] if a.stub_sections else []),
        *([call_badge, ""] if call_badge else []),
        "## Thesis _(analyst judgment — not filing facts)_",
        f"- **Bull:** {t.bull_case or 'n/a'}",
        f"- **Bear:** {t.bear_case or 'n/a'}",
        "- **What would change my mind:**", *[f"  {x}" for x in cmm],
        f"- **Takeaway:** {t.takeaway or 'n/a'}",
        "",
        "## Reconciliation (numbers vs the filing)",
        *_reconciliation_md(a.reconciliation),
    ]
    if a.silent_count:
        lines += [f"_{a.silent_count} reconciliation(s) unaddressed by the filing._"]
    # Deterministic sections the model did not write (research/durability.py,
    # research/news.py). Their headings say so, against the banner above.
    if a.durability_line:
        from .durability import HEADING
        lines += ["", f"## {HEADING}", a.durability_line]
    if a.news_status:
        from .news import markdown_lines
        lines += markdown_lines(a.news_items, a.news_status, a.news_as_of, a.news_from,
                                a.news_truncated)
    # No Lazy-Prices section on purpose: `text_similarity` is stored in the brief JSON,
    # but the metric keeps stopwords, so every real YoY pair rounds to "0% rewritten".
    # See docs/design/PLAN_INVENTORY_DECOMPOSITION.md §0.4.
    lines += ["", "## Moat",
              f"- **Trajectory:** {a.moat.trajectory or 'n/a'}",
              f"- {a.moat.summary}"]
    if a.moat.sources:
        lines += ["", "## Sources of advantage", *_findings_md(a.moat.sources, "None identified.")]
    lines += ["", "## Business model", a.business_model_summary,
              "", "## Management & capital allocation", a.management_capital_allocation]
    if a.management_findings:
        lines += ["", *_findings_md(a.management_findings, "None identified.")]
    if capital_allocation and capital_allocation[0]:
        from .capital_allocation import HEADING as CA_HEADING
        lines += ["", f"## {CA_HEADING}", capital_allocation[0]]
    lines += [
              "", "## Material risks", *_findings_md(a.risks, "None identified."),
              "", "## Red flags", *_findings_md(a.red_flags, "None identified."),
              "", "## Newly disclosed risks (vs prior year)",
              *_findings_md(a.added_risks, "No newly disclosed risks identified.")]
    if a.unverified_count:
        lines += ["", f"_{a.unverified_count} claim(s) could not be verified "
                  "against the filing text._"]
    # Separate from the count above, which is the reader's fabrication signal.
    if a.inference_count:
        lines += ["", f"_{a.inference_count} claim(s) stated without a filing quote "
                  "(declared inference, or from a section not provided)._"]
    lines += call_block
    return "\n".join(lines) + "\n"


def write(a: QualitativeAssessment, root, config=None,
          capital_allocation: Optional[tuple[str, str]] = None) -> Path:
    """Write both the markdown brief and the JSON record; return the brief path.
    Keyed on a.cache_key (see research/cachekey.py:brief_key), else filing_accession."""
    key = a.cache_key or a.filing_accession
    bp = brief_path(a.ticker, key, root)
    bp.parent.mkdir(parents=True, exist_ok=True)
    # The .md is the commit marker (is_cached keys on it): write it LAST, so a crash
    # never strands a "cached" brief with no JSON record.
    record = dataclasses.asdict(a)
    record["synthesis"] = a.thesis.takeaway   # asdict drops the property; preserve the key
    record["capital_allocation_line"], record["capital_allocation_status"] = (
        capital_allocation or ("", ""))
    record_path(a.ticker, key, root).write_text(
        json.dumps(record, indent=2, default=str), encoding="utf-8")
    # Atomic: a truncated .md would read as "cached" forever.
    tmp = bp.with_name(f"{bp.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(to_markdown(a, config, capital_allocation), encoding="utf-8")
        os.replace(tmp, bp)
    finally:
        tmp.unlink(missing_ok=True)
    return bp

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

from .models import QualitativeAssessment, call_disclaimer, stance_label


def _safe(accession: str) -> str:
    return (accession or "unknown").replace("/", "-")


def brief_path(ticker: str, accession: str, root) -> Path:
    return Path(root) / ticker.upper() / f"{_safe(accession)}.md"


def record_path(ticker: str, accession: str, root) -> Path:
    return Path(root) / ticker.upper() / f"{_safe(accession)}.json"


def is_cached(ticker: str, accession: str, root) -> bool:
    """A brief for this exact filing already exists (keyed by accession, not date)."""
    return brief_path(ticker, accession, root).exists()


def _findings_md(findings, empty_label: str) -> list[str]:
    if not findings:
        return [f"- {empty_label}"]
    lines = []
    for f in findings:
        mark = "" if f.verified else " _(unverified)_"
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
        why = f"Auto-downgraded: {c.clamp_note}." if c.clamp_note else "Auto-downgraded by a tripped gate."
        block.append(f"- **Why:** {why}")
        if c.rationale:
            block.append(f"- _Model's pre-clamp view: {c.rationale}_")
    elif c.rationale:
        block.append(f"- **Why:** {c.rationale}")
    if watch:
        block.append(f"- **But watch:** {watch}")
    if c.decided_without:
        block.append(f"- **Decided without:** {'; '.join(c.decided_without)}")
    if c.not_applicable:
        block.append(f"- **Not applicable:** {'; '.join(c.not_applicable)}")
    if c.conviction_capped and not c.stance_clamped:
        block.append("- _Conviction capped by data coverage / corroboration._")
    return badge, block


def to_markdown(a: QualitativeAssessment, config=None) -> str:
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
    lines += ["", "## Moat",
              f"- **Trajectory:** {a.moat.trajectory or 'n/a'}",
              f"- {a.moat.summary}"]
    if a.moat.sources:
        lines += ["", "**Sources of advantage:**"] + [f"- {s}" for s in a.moat.sources]
    lines += ["", "## Business model", a.business_model_summary,
              "", "## Management & capital allocation", a.management_capital_allocation,
              "", "## Material risks", *_findings_md(a.risks, "None identified."),
              "", "## Red flags", *_findings_md(a.red_flags, "None identified."),
              "", "## Newly disclosed risks (vs prior year)",
              *_findings_md(a.added_risks, "No newly disclosed risks identified.")]
    if a.unverified_count:
        lines += ["", f"_{a.unverified_count} claim(s) could not be verified "
                  "against the filing text._"]
    lines += call_block
    return "\n".join(lines) + "\n"


def write(a: QualitativeAssessment, root, config=None) -> Path:
    """Write both the markdown brief and the JSON record; return the brief path.
    Keyed on a.cache_key (composite 10-K+10-Q), falling back to filing_accession for
    back-compat with assessments that predate the bundle."""
    key = a.cache_key or a.filing_accession
    bp = brief_path(a.ticker, key, root)
    bp.parent.mkdir(parents=True, exist_ok=True)
    # Commit order matters: is_cached() keys on the .md brief, so the .md is the
    # COMMIT MARKER and must be written LAST. JSON record first — a crash between
    # the two writes must never strand a "cached" brief with no screening-call record.
    record = dataclasses.asdict(a)
    record["synthesis"] = a.thesis.takeaway   # asdict drops the property; preserve the key
    record_path(a.ticker, key, root).write_text(
        json.dumps(record, indent=2, default=str), encoding="utf-8")
    # The marker itself must be atomic too: a truncated .md would read as
    # "cached" forever. PID-unique temp + os.replace (the store.py pattern).
    tmp = bp.with_name(f"{bp.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(to_markdown(a, config), encoding="utf-8")
        os.replace(tmp, bp)
    finally:
        tmp.unlink(missing_ok=True)
    return bp

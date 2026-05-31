from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from .models import QualitativeAssessment


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


def to_markdown(a: QualitativeAssessment) -> str:
    lines = [
        f"# {a.ticker} — qualitative read",
        "",
        f"> **LLM-generated** from {a.filing_accession} ({a.filing_date}) by "
        f"`{a.model}`. Verify against the source filing. Not investment advice.",
        "",
        "## Synthesis", a.synthesis, "",
        "## Moat",
        f"- **Trajectory:** {a.moat.trajectory or 'n/a'}",
        f"- {a.moat.summary}",
    ]
    if a.moat.sources:
        lines += ["", "**Sources of advantage:**"] + [f"- {s}" for s in a.moat.sources]
    lines += ["", "## Business model", a.business_model_summary,
              "", "## Management & capital allocation", a.management_capital_allocation,
              "", "## Material risks", *_findings_md(a.risks, "None identified."),
              "", "## Red flags", *_findings_md(a.red_flags, "None identified.")]
    if a.unverified_count:
        lines += ["", f"_{a.unverified_count} finding(s) could not be verified "
                  "against the filing text._"]
    return "\n".join(lines) + "\n"


def write(a: QualitativeAssessment, root) -> Path:
    """Write both the markdown brief and the JSON record; return the brief path."""
    bp = brief_path(a.ticker, a.filing_accession, root)
    bp.parent.mkdir(parents=True, exist_ok=True)
    bp.write_text(to_markdown(a))
    record_path(a.ticker, a.filing_accession, root).write_text(
        json.dumps(dataclasses.asdict(a), indent=2, default=str))
    return bp

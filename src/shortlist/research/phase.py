"""Guardrailed orchestration around `research.enrich` — kill-switch, auth probe, hard cap,
wall-clock phase budget.

The Telegram bot's `/deep` is its only caller. The guardrails are the reason this is not
just a call to `enrich()`: an unavailable `claude` CLI, a hung subprocess, or a config typo
must degrade to a note rather than hang or crash the caller.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
from pathlib import Path

from ..env import redact_secrets

# File kill-switch for the research phase behind /deep.
_STOP_FILES = ("research/STOP_RESEARCH",)
_STOP_ENVS = ("SHORTLIST_NO_RESEARCH",)


def _stopped() -> bool:
    if any(os.environ.get(v) == "1" for v in _STOP_ENVS):
        return True
    return any(Path(p).exists() for p in _STOP_FILES)


def research_phase(
    cards,
    config,
    research_cfg,
    *,
    require_passed=True,
    top_n=None,
    macro=None,
    _is_available=None,
    _enrich=None,
) -> tuple[dict, dict, list, str | None, dict]:
    """Run the research layer over `cards` under guardrails.

    Returns (briefs, assessments, researched, note, skipped): briefs is
    dict[ticker, one_line_str], assessments is dict[ticker, full QualitativeAssessment
    record], researched is list[ticker], note is str|None, and skipped is
    dict[ticker, reason_str] — the per-ticker reasons enrich() declined a name
    (e.g. "no 10-K", "assessment failed", "filing error: …"), surfaced so the caller can
    explain gaps instead of silently omitting research.

    Optional _is_available/_enrich kwargs allow injection in tests without monkeypatching
    the import machinery. When omitted the real research module is imported lazily.

    REAL API: enrich() returns list[ResearchResult] (not dict[ticker, path]).
    ResearchResult.synthesis is the 2-3 sentence LLM text; ResearchResult.brief_path is the
    .md file path (a matching .json record is written alongside it). Synthesis is used
    directly for in-session results; cached results (from_cache=True) carry an empty
    synthesis, so we fall back to reading the record JSON.
    """
    if _stopped():
        return {}, {}, [], "research skipped: kill-switch", {}
    if _is_available is None or _enrich is None:
        try:
            from . import enrich as _en
            from . import is_available as _ia
            _is_available = _is_available or _ia
            _enrich = _enrich or _en
        except Exception:  # noqa: BLE001
            return {}, {}, [], "research skipped: layer unavailable", {}
    if not _is_available():
        return {}, {}, [], "research skipped: claude CLI / edgartools not available", {}
    n = top_n if top_n is not None else research_cfg.get("research_top_n", 3)
    budget_s = research_cfg.get("research_phase_budget_s", 600)
    try:
        # Wrap the entire enrich() call in a ThreadPoolExecutor so we can enforce a
        # wall-clock ceiling (research_phase_budget_s). N hung claude calls serialise
        # inside enrich(); without this timeout the phase budget is decorative config.
        #
        # IMPORTANT: do NOT use `with ThreadPoolExecutor(...) as pool:` — that context
        # manager calls shutdown(wait=True) on exit, which blocks until the thread finishes
        # even after a TimeoutError. Construct explicitly and shutdown(wait=False) so we
        # abandon the hung thread immediately.
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(_enrich, cards, config, top_n=n, refresh=False,
                             require_passed=require_passed, macro=macro)
        try:
            results = future.result(timeout=budget_s)
        except concurrent.futures.TimeoutError:
            return {}, {}, [], f"research skipped: phase budget {budget_s}s exceeded", {}
        finally:
            # Always shut down — on success, timeout, OR an exception raised inside
            # _enrich() (which escapes to the outer handler). wait=False so we never block
            # on a hung thread (see the IMPORTANT note above re: `with`).
            pool.shutdown(wait=False)
    except Exception as e:  # noqa: BLE001
        return {}, {}, [], f"research failed: {redact_secrets(str(e))}", {}

    briefs: dict[str, str] = {}
    assessments: dict[str, dict] = {}
    researched: list[str] = []
    skipped: dict[str, str] = {}
    for r in results:
        if r.skipped:
            skipped[r.ticker] = r.skipped
            continue
        researched.append(r.ticker)
        brief_text = r.synthesis if r.synthesis else one_line_brief_from_file(r.brief_path)
        briefs[r.ticker] = brief_text[:200]
        rec = assessment_record_from_file(r.brief_path)
        if rec:
            assessments[r.ticker] = rec
    return briefs, assessments, researched, None, skipped


def _record_json(brief_path) -> dict | None:
    """Load the JSON record report.write() saves alongside the .md (<ticker>/<accession>.json).

    Suffix-safe (.with_suffix avoids replacing any ".md" substring); returns None on any
    read/parse failure.
    """
    try:
        return json.loads(Path(brief_path).with_suffix(".json").read_text())
    except Exception:  # noqa: BLE001
        return None


def assessment_record_from_file(brief_path) -> dict | None:
    """Read the full QualitativeAssessment record (JSON) report.write() saved next to the .md."""
    return _record_json(brief_path)


def one_line_brief_from_file(brief_path) -> str:
    """Read synthesis from the JSON record file that report.write() writes alongside the .md."""
    data = _record_json(brief_path)
    if data is None:
        return "brief generated"
    # QualitativeAssessment fields in the JSON: 'synthesis' is the 2-3 sentence text
    return (data.get("synthesis")
            or (data.get("thesis") or {}).get("takeaway")
            or data.get("summary") or "")[:200]

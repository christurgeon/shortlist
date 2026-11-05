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
    dict[ticker, one_line_str], assessments is dict[ticker, the brief's JSON record],
    researched is list[ticker], note is str|None, and skipped is dict[ticker, reason_str]
    — the per-ticker reasons enrich() declined a name, so the caller can explain gaps
    instead of silently omitting research.

    `_is_available`/`_enrich` are test seams; omitted, the research package is imported
    lazily. A cached result carries an empty `synthesis`, so the one-liner then comes
    from the JSON record beside the brief.
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
        # Do NOT use `with ThreadPoolExecutor`: its exit calls shutdown(wait=True) and
        # blocks on a hung thread even after a TimeoutError.
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(_enrich, cards, config, top_n=n, refresh=False,
                             require_passed=require_passed, macro=macro)
        try:
            results = future.result(timeout=budget_s)
        except concurrent.futures.TimeoutError:
            return {}, {}, [], f"research skipped: phase budget {budget_s}s exceeded", {}
        finally:
            # wait=False: never block on a hung thread.
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
        rec = _record_json(r.brief_path)
        briefs[r.ticker] = (r.synthesis or _one_line(rec))[:200]
        if rec:
            assessments[r.ticker] = rec
    return briefs, assessments, researched, None, skipped


def _record_json(brief_path) -> dict | None:
    """The JSON record report.write() saves beside the .md, or None on any failure."""
    try:
        rec = json.loads(Path(brief_path).with_suffix(".json").read_text())
    except Exception:  # noqa: BLE001
        return None
    return rec if isinstance(rec, dict) else None


def _one_line(rec: dict | None) -> str:
    if rec is None:
        return "brief generated"
    return (rec.get("synthesis") or (rec.get("thesis") or {}).get("takeaway")
            or rec.get("summary") or "")

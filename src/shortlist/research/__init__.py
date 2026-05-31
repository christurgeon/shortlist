# shortlist.research — opt-in qualitative layer.
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from ..env import redact_secrets
from . import claude_cli, report
from .assess import assess as _assess
from .filings import fetch_10k as _fetch_10k

__all__ = ["enrich", "ResearchResult", "is_available"]


@dataclass
class ResearchResult:
    ticker: str
    brief_path: Optional[str] = None
    cost_usd: float = 0.0
    synthesis: str = ""
    skipped: Optional[str] = None   # human-readable reason if not produced


def is_available() -> bool:
    """True if both the `claude` CLI and edgartools are usable."""
    if not claude_cli.is_available():
        return False
    try:
        import edgar  # noqa: F401
    except ImportError:
        return False
    return True


def enrich(cards, config: dict, *, top_n: int, refresh: bool = False,
           fetch: Callable = _fetch_10k, assess_fn: Callable = _assess) -> list[ResearchResult]:
    """Enrich the top-N non-gated cards (already sorted by composite desc).
    `fetch`/`assess_fn` are injectable for testing. One failure never aborts the
    batch — each name yields a ResearchResult (with `skipped` set on failure)."""
    root = config.get("research", {}).get("output_root", "research")
    selected = [c for c in cards if not c.gates][:top_n]
    results: list[ResearchResult] = []
    for card in selected:
        try:
            filing = fetch(card.ticker)
        except Exception as e:  # network/edgartools/identity errors
            results.append(ResearchResult(card.ticker, skipped=f"filing error: {redact_secrets(e)}"))
            continue
        if filing is None:
            results.append(ResearchResult(card.ticker, skipped="no 10-K"))
            continue
        if not refresh and report.is_cached(card.ticker, filing.accession, root):
            bp = report.brief_path(card.ticker, filing.accession, root)
            results.append(ResearchResult(card.ticker, brief_path=str(bp), synthesis="(cached)"))
            continue
        assessment = assess_fn(card, filing, config)
        if assessment is None:
            results.append(ResearchResult(card.ticker, skipped="assessment failed"))
            continue
        bp = report.write(assessment, root)
        results.append(ResearchResult(
            card.ticker, brief_path=str(bp), cost_usd=assessment.cost_usd or 0.0,
            synthesis=assessment.synthesis))
    return results

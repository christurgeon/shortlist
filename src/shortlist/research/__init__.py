# shortlist.research — opt-in qualitative layer.
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from ..env import redact_secrets
from ..models import rank_key
from . import claude_cli, report
from .assess import assess as _assess
from .filings import fetch_bundle as _fetch_bundle

__all__ = ["enrich", "ResearchResult", "is_available"]


@dataclass
class ResearchResult:
    ticker: str
    brief_path: Optional[str] = None
    cost_usd: float = 0.0
    synthesis: str = ""
    from_cache: bool = False
    skipped: Optional[str] = None   # human-readable reason if not produced


def is_available() -> bool:
    """True if both the `claude` CLI and edgartools are usable."""
    if not claude_cli.is_available():
        return False
    try:
        from edgar import Company, set_identity  # noqa: F401
    except ImportError:
        return False
    return True


def _enrich_card(card, config: dict, root: str, refresh: bool,
                 fetch: Callable, assess_fn: Callable) -> ResearchResult:
    """Research a single card. Never raises — failures become a skipped result."""
    try:
        bundle = fetch(card.ticker, config=config)
    except Exception as e:  # network/edgartools/identity errors
        return ResearchResult(card.ticker, skipped=f"filing error: {redact_secrets(e)}")
    if bundle is None:
        return ResearchResult(card.ticker, skipped="no 10-K")
    if not refresh and report.is_cached(card.ticker, bundle.cache_key, root):
        bp = report.brief_path(card.ticker, bundle.cache_key, root)
        return ResearchResult(card.ticker, brief_path=str(bp), from_cache=True)
    from .filings import cap_bundle
    bundle = cap_bundle(bundle, config.get("research", {}).get("max_chars"))
    assessment = assess_fn(card, bundle, config)
    if assessment is None:
        return ResearchResult(card.ticker, skipped="assessment failed")
    bp = report.write(assessment, root, config)
    return ResearchResult(
        card.ticker, brief_path=str(bp), cost_usd=assessment.cost_usd or 0.0,
        synthesis=assessment.synthesis)


def enrich(cards, config: dict, *, top_n: int, refresh: bool = False,
           require_passed: bool = True,
           fetch: Callable = _fetch_bundle, assess_fn: Callable = _assess) -> list[ResearchResult]:
    """Enrich the top-N cards. Sorts by `rank_key` (scored, composite, confidence)
    before selecting — the caller need not pre-sort. By default only `passed`
    (not-gated AND scored) cards are eligible; `require_passed=False` selects the
    top-N regardless of gate status (used by the interactive `/deep` command, where
    the operator deliberately names the ticker). `fetch`/`assess_fn` are injectable
    for testing. One failure never aborts the batch — each name yields a
    ResearchResult (with `skipped` set on failure)."""
    root = config.get("research", {}).get("output_root", "research")
    ranked = sorted(cards, key=rank_key, reverse=True)
    eligible = ranked if not require_passed else [c for c in ranked if c.passed]
    selected = eligible[:top_n]
    return [_enrich_card(card, config, root, refresh, fetch, assess_fn) for card in selected]

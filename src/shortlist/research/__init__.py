# shortlist.research — opt-in qualitative layer.
from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass
from typing import Callable, Optional

from ..env import redact_secrets
from ..models import rank_key
from . import cachekey, claude_cli, report
from .assess import assess as _assess
from .filings import fetch_bundle as _fetch_bundle
from .filings import log_abstain as _log_abstain
from .filings import no_10k_reason as _no_10k_reason

__all__ = ["enrich", "ResearchResult", "is_available"]

# Bounds concurrent `claude -p` and SEC-fetch calls, whatever `top_n` is.
_MAX_ENRICH_WORKERS = 5


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
                 fetch: Callable, assess_fn: Callable,
                 reason_fn: Callable = _no_10k_reason, macro=None) -> ResearchResult:
    """Research a single card. Never raises — failures become a skipped result."""
    try:
        bundle = fetch(card.ticker, config=config)
    except Exception as e:  # network/edgartools/identity errors
        # A ticker with no SEC CIK mapping raises before the no-10-K path can run.
        # Matched by type NAME: edgar is an optional extra and cannot be imported here.
        if type(e).__name__ == "CompanyNotFoundError":
            return ResearchResult(card.ticker, skipped=(
                f"no SEC registrant for '{card.ticker}' — likely a fund/ETF "
                "share class or non-SEC-listed security; no filings to research"))
        return ResearchResult(card.ticker, skipped=f"filing error: {redact_secrets(e)}")
    if bundle is None:
        return ResearchResult(card.ticker, skipped=reason_fn(card.ticker))
    # Compute the WIDE key BEFORE the is_cached check; see research/cachekey.py.
    # Degrade, never raise: both batch callers catch only at batch level, so an
    # exception here would drop every name in the run.
    try:
        key = cachekey.brief_key(bundle, card, macro=macro, config=config)
    except Exception as e:
        # Falling back to the narrow key must stay visible, not look like a cache hit.
        _log_abstain("brief_key failed, falling back to the narrow accession key",
                     card.ticker, e)
        key = bundle.cache_key
    if not refresh and report.is_cached(card.ticker, key, root):
        bp = report.brief_path(card.ticker, key, root)
        return ResearchResult(card.ticker, brief_path=str(bp), from_cache=True)
    # cap_bundle, assess and report.write can raise; isolate them too.
    try:
        from .filings import cap_bundle
        bundle = cap_bundle(bundle, config.get("research", {}).get("max_chars"))
        assessment = assess_fn(card, bundle, config, macro=macro)
        if assessment is None:
            return ResearchResult(card.ticker, skipped="assessment failed")
        # assess() sets the narrow key; the brief is written under the wide key.
        assessment.cache_key = key
        # AFTER the model call: these sections are display-only and must not reach
        # the prompt.
        dcfg = (config.get("research") or {}).get("durability")
        if isinstance(dcfg, dict) and dcfg.get("enabled", False):
            from . import durability
            assessment.durability_line, assessment.durability_status = durability.fetch_section(
                card, bundle, config)
        ncfg = (config.get("research") or {}).get("news")
        if isinstance(ncfg, dict) and ncfg.get("enabled", False):
            from . import news
            s = news.fetch_section(card, config)
            (assessment.news_items, assessment.news_status, assessment.news_as_of,
             assessment.news_from, assessment.news_truncated) = (
                s.items, s.status, s.as_of, s.covers_from, s.truncated)
        from . import capital_allocation
        bp = report.write(assessment, root, config,
                          capital_allocation=capital_allocation.section(card))
    except Exception as e:  # assess/render/write errors
        return ResearchResult(card.ticker, skipped=f"research error: {redact_secrets(e)}")
    return ResearchResult(
        card.ticker, brief_path=str(bp), cost_usd=assessment.cost_usd or 0.0,
        synthesis=assessment.synthesis)


def _filtered_reason(card) -> str:
    """Why a card was filtered out of the research selection: gated, not scored, or both.
    The fallback must not be "", which callers read as "not skipped"."""
    parts = []
    if getattr(card, "gates", None):
        parts.append("gated (" + ", ".join(card.gates) + ")")
    if not getattr(card, "scored", True):
        parts.append("not scored (below the validity floor)")
    return "; ".join(parts) or "filtered out of the research selection"


def enrich(cards, config: dict, *, top_n: int, refresh: bool = False,
           require_passed: bool = True,
           fetch: Callable = _fetch_bundle, assess_fn: Callable = _assess,
           reason_fn: Callable = _no_10k_reason, macro=None) -> list[ResearchResult]:
    """Enrich the top-N cards, sorted by `rank_key`. Only `passed` cards are eligible
    unless `require_passed=False` (the interactive `/deep` command). `fetch` and
    `assess_fn` are injectable for tests. One failure never aborts the batch: each name
    yields a ResearchResult, with `skipped` set on failure.

    Gate-filtered names inside the top-N come back as skipped results, so a fully gated
    selection is not empty. Results are in rank order and may exceed `top_n` in length."""
    root = config.get("research", {}).get("output_root", "research")
    ranked = sorted(cards, key=rank_key, reverse=True)
    eligible = ranked if not require_passed else [c for c in ranked if c.passed]
    selected = eligible[:top_n]
    picked = {c.ticker for c in selected}
    # Report gate-filtered names in the top-N as skipped. `selected` is unchanged and
    # the report is bounded by top_n.
    reported = picked | {c.ticker for c in ranked[:top_n]}
    ordered = [card for card in ranked if card.ticker in reported]

    if not picked:
        return [ResearchResult(card.ticker, skipped=_filtered_reason(card))
                for card in ordered]

    # Concurrent: each _enrich_card call is independent and never raises, so
    # future.result() cannot propagate an exception.
    results: dict[str, ResearchResult] = {}
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(len(picked), _MAX_ENRICH_WORKERS)) as pool:
        futures = {
            pool.submit(_enrich_card, card, config, root, refresh, fetch, assess_fn,
                       reason_fn, macro=macro): card.ticker
            for card in ordered if card.ticker in picked}
        for future in concurrent.futures.as_completed(futures):
            results[futures[future]] = future.result()

    return [results[card.ticker] if card.ticker in picked
            else ResearchResult(card.ticker, skipped=_filtered_reason(card))
            for card in ordered]

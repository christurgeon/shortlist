# shortlist.research — opt-in qualitative layer.
from __future__ import annotations

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
        # A ticker with no SEC CIK mapping (fund/ETF share class, non-SEC-listed
        # security) raises before the no-10-K classification path can run.
        # Matched by type NAME: edgar is an optional extra we can't import here.
        if type(e).__name__ == "CompanyNotFoundError":
            return ResearchResult(card.ticker, skipped=(
                f"no SEC registrant for '{card.ticker}' — likely a fund/ETF "
                "share class or non-SEC-listed security; no filings to research"))
        return ResearchResult(card.ticker, skipped=f"filing error: {redact_secrets(e)}")
    if bundle is None:
        return ResearchResult(card.ticker, skipped=reason_fn(card.ticker))
    # The WIDE key must be computed here, BEFORE the is_cached short-circuit: this
    # is the check that saves the LLM call, and keying it on accessions alone is
    # what let a brief outlive its own inputs. See research/cachekey.py.
    #
    # Degrade, never raise: _enrich_card's contract (docstring, line 41) is that one
    # bad ticker never aborts the batch, and BOTH batch callers (screen.py:229-233,
    # research/phase.py:92) catch only at the batch level — an exception here would
    # return {} for every name in the run.
    try:
        key = cachekey.brief_key(bundle, card, macro=macro, config=config)
    except Exception as e:
        # Silent-degrade-to-narrow-key is exactly the stale-forever mode this
        # whole feature exists to close, so a ticker landing here systematically
        # must be visible, not indistinguishable from a healthy cache hit.
        # log_abstain already runs the message through redact_secrets.
        _log_abstain("brief_key failed, falling back to the narrow accession key",
                     card.ticker, e)
        key = bundle.cache_key
    if not refresh and report.is_cached(card.ticker, key, root):
        bp = report.brief_path(card.ticker, key, root)
        return ResearchResult(card.ticker, brief_path=str(bp), from_cache=True)
    # cap_bundle / assess / report.write (filesystem I/O, prompt building, the LLM
    # call) can all raise; isolate them too so the docstring promise — one failure
    # never aborts the batch — holds for the whole pipeline, not just fetch().
    try:
        from .filings import cap_bundle
        bundle = cap_bundle(bundle, config.get("research", {}).get("max_chars"))
        assessment = assess_fn(card, bundle, config, macro=macro)
        if assessment is None:
            return ResearchResult(card.ticker, skipped="assessment failed")
        # assess() sets the narrow bundle key (assess.py:659); the brief is written
        # under the wide key so the two never diverge on disk.
        assessment.cache_key = key
        bp = report.write(assessment, root, config)
    except Exception as e:  # assess/render/write errors
        return ResearchResult(card.ticker, skipped=f"research error: {redact_secrets(e)}")
    return ResearchResult(
        card.ticker, brief_path=str(bp), cost_usd=assessment.cost_usd or 0.0,
        synthesis=assessment.synthesis)


def _filtered_reason(card) -> str:
    """Why a card was filtered out of the research selection. `passed` is
    `not gates and scored`, so BOTH halves must be reportable — a gated name and an
    abstained one are different answers to "why is there no brief". The final
    fallback is unreachable by construction (only non-`passed` cards reach here) but
    must not return "", which downstream reads as "not skipped"."""
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
    """Enrich the top-N cards. Sorts by `rank_key` (scored, composite, confidence)
    before selecting — the caller need not pre-sort. By default only `passed`
    (not-gated AND scored) cards are eligible; `require_passed=False` selects the
    top-N regardless of gate status (used by the interactive `/deep` command, where
    the operator deliberately names the ticker). `fetch`/`assess_fn` are injectable
    for testing. One failure never aborts the batch — each name yields a
    ResearchResult (with `skipped` set on failure).

    Gate-filtered names inside the top-N are RETURNED as skipped results, so a fully
    gated selection reports itself instead of coming back empty. Results are in rank
    order and may therefore exceed `top_n` in length — but never more than one entry
    per name in `ranked[:top_n]` plus the `top_n` researched."""
    root = config.get("research", {}).get("output_root", "research")
    ranked = sorted(cards, key=rank_key, reverse=True)
    eligible = ranked if not require_passed else [c for c in ranked if c.passed]
    selected = eligible[:top_n]
    picked = {c.ticker for c in selected}
    # Gate-filtered names that ranked INTO the top-N are reported as skipped rather
    # than dropped in silence: an all-gated selection used to return [], which made
    # screen.py's `if results:` header vanish too, so the run printed nothing at all
    # (measured 2026-08-21 on HDSN). Reporting is additive — `selected` is unchanged,
    # so the research budget is still filled from the eligible pool — and bounded by
    # top_n, so a 200-name screen cannot emit 190 skip lines.
    reported = picked | {c.ticker for c in ranked[:top_n]}
    return [_enrich_card(card, config, root, refresh, fetch, assess_fn, reason_fn,
                         macro=macro)
            if card.ticker in picked
            else ResearchResult(card.ticker, skipped=_filtered_reason(card))
            for card in ranked if card.ticker in reported]

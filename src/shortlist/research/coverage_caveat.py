"""Turn a ScoreCard's coverage/abstention machinery into human data-completeness
strings for the screening call. Dependency-free leaf — Python owns this, never the
LLM. Returns (decided_without, not_applicable):

  decided_without — REAL gaps (provider gating/throttle/error, or a null sub-score
    with no coverage block). These cap conviction.
  not_applicable  — STRUCTURAL N/A (sector-masked sub-scores). NOT a failure.
"""
from __future__ import annotations

# The six composite sub-scores that can be a "gap" (risk is composite-only/excluded).
_AXIS = ("quality", "moat", "growth", "momentum", "value", "insider")


def _short_reason(cov) -> str:
    p = getattr(cov, "providers", None) or {}
    if p.get("fmp") == "gated_402":
        return "FMP gated this symbol (402)"
    if p.get("fmp") == "rate_limited_429":
        return "FMP rate-limited this run (429)"
    flagged = sorted(n for n, s in p.items()
                     if s in ("gated_402", "rate_limited_429", "empty", "error"))
    if flagged:
        return f"{', '.join(flagged)} supplied no usable data"
    return ""


def _phrase(axes: list[str]) -> str:
    noun = "axis" if len(axes) == 1 else "axes"
    return f"{', '.join(axes)} {noun}"


def coverage_caveats(card) -> tuple[list[str], list[str]]:
    if card is None:
        return [], []
    abst = getattr(card, "abstentions", None) or []
    masked = [a["field"] for a in abst
              if a.get("scope") == "subscore" and a.get("reason") == "inapplicable"]
    masked_set = set(masked)

    cov = getattr(card, "coverage", None)
    if cov is not None:
        gap_axes = [f for f in (getattr(cov, "unavailable", None) or []) if f in _AXIS]
        reason = _short_reason(cov)
    else:
        gap_axes = [f for f in _AXIS
                    if getattr(card, f, None) is None and f not in masked_set]
        reason = "not available from any source"

    decided_without: list[str] = []
    if gap_axes:
        decided_without.append(_phrase(gap_axes) + (f" — {reason}" if reason else ""))

    not_applicable: list[str] = []
    if masked:
        bucket = getattr(card, "sic_bucket", None) or "this sector"
        not_applicable.append(f"{_phrase(masked)} — not applicable ({bucket})")

    return decided_without, not_applicable

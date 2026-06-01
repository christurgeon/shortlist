from __future__ import annotations

from typing import Optional

from .models import Coverage, ScoreCard


def classify_failure(exc: Exception) -> str:
    """Map a provider fetch exception to a status. HTTP 402 (FMP paid/gated
    symbol) -> "gated_402"; anything else -> "error". Detection is by status
    code (not string parsing) and needs no `requests` import: requests.HTTPError
    exposes `.response.status_code`, and other exceptions simply lack it."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return "gated_402" if status == 402 else "error"


_SUBSCORE_FIELDS = ("quality", "moat", "growth", "momentum", "value", "insider")

# Single source of truth for the per-provider fetch statuses that signal a coverage
# problem, mapped to their human label. A status with a label here is "flagged"
# (drives both the note and the stderr line); "ok" is intentionally absent. Add any
# new status here only — both `_build_note` and `coverage_note_line` key off it, so
# they can never silently disagree about which statuses to surface.
_STATUS_LABEL = {"gated_402": "gated (402)", "empty": "empty", "error": "fetch error"}

_FMP_NOTE = (
    "FMP gated this symbol (402); the value axis (PE-vs-history, FCF yield, "
    "target upside) and FMP-sourced quality inputs (ROE/ROIC) need FMP Starter tier"
)


def build_coverage(outcomes: dict, contributed: set, card: ScoreCard) -> Optional[Coverage]:
    """Assemble a Coverage record, or None when every provider is "ok".

    `outcomes` maps provider name -> raise-time status ("ok" on success, else the
    classify_failure result). `contributed` is the set of provider names whose own
    fetch returned at least one field — judged BEFORE merge, so a provider that
    fetched real data but lost every field to a higher-priority source still counts
    as contributing. An "ok" provider absent from `contributed` returned nothing
    usable and is reclassified "empty"."""
    providers = dict(outcomes)
    for name, status in list(providers.items()):
        if status == "ok" and name not in contributed:
            providers[name] = "empty"

    if all(status == "ok" for status in providers.values()):
        return None

    unavailable = [f for f in _SUBSCORE_FIELDS if getattr(card, f) is None]
    upside = card.metrics.upside_to_target() if card.metrics else None
    if upside is None:
        unavailable.append("upside_to_target")

    return Coverage(providers=providers, unavailable=unavailable,
                    note=_build_note(providers))


def _build_note(providers: dict) -> Optional[str]:
    flagged = {n: s for n, s in providers.items() if s in _STATUS_LABEL}
    if not flagged:
        return None
    # FMP recognized-pattern note takes precedence over the generic multi-provider note.
    # Only fire for gated_402/empty — an fmp "error" is a transient fetch failure,
    # not a tier-gating issue, so it must NOT claim "needs Starter tier".
    if providers.get("fmp") in ("gated_402", "empty"):
        return _FMP_NOTE
    return (f"{', '.join(sorted(flagged))}: supplied no usable data for this "
            "symbol")


def coverage_note_line(ticker: str, cov: Coverage) -> str:
    """One-line stderr rendering, e.g.
    `  SCHW   fmp gated (402) -> value, upside_to_target unavailable`."""
    flagged = [f"{n} {_STATUS_LABEL[s]}"
               for n, s in sorted(cov.providers.items())
               if s in _STATUS_LABEL]
    unavail = ", ".join(cov.unavailable) or "—"
    return f"  {ticker:<6} {'; '.join(flagged)} -> {unavail} unavailable"

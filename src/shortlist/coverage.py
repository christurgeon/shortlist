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


_SUBSCORE_FIELDS = ("quality", "moat", "momentum", "value", "insider")

_FMP_NOTE = (
    "FMP gated this symbol (402); value axis (PE-vs-history, FCF yield, "
    "target upside) needs FMP Starter tier"
)


def build_coverage(outcomes: dict, card: ScoreCard) -> Optional[Coverage]:
    """Assemble a Coverage record, or None when every provider is "ok".

    `outcomes` maps provider name -> raise-time status ("ok" on success, else the
    classify_failure result). A provider that did not raise but contributed zero
    fields to the merged metrics (per `card.metrics.sources`) is reclassified
    "empty"."""
    providers = dict(outcomes)
    contributed = set(card.metrics.sources.values()) if card.metrics else set()
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
    flagged = {n: s for n, s in providers.items() if s in ("gated_402", "empty")}
    if not flagged:
        return None
    # "fmp" is the registry name (fmp.py: name = "fmp") — load-bearing string.
    if providers.get("fmp") in ("gated_402", "empty"):
        return _FMP_NOTE
    return (f"{', '.join(sorted(flagged))}: provider supplied no data for this "
            "symbol (see stderr)")

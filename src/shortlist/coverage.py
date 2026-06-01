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

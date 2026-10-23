"""Tiny dependency-free helpers shared across both stacks (screener + harness)."""

import math
from typing import Any, Optional


def retry_after_seconds(header_value: Optional[str], fallback: float,
                        *, cap: float = 30.0) -> float:
    """Backoff delay (seconds) from an HTTP ``Retry-After`` header.

    Parses delay-seconds or HTTP-date (RFC 7231). Returns ``fallback`` when the header
    is absent or unparseable. The result is capped at ``cap``. Never raises.
    """
    if header_value:
        try:
            v = float(header_value)
            if math.isfinite(v):     # `nan` would pass min() and make sleep() never return
                return min(v, cap)
        except (ValueError, TypeError):
            pass
        try:
            from datetime import datetime, timezone
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(header_value)
            if dt is not None:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                delta = (dt - datetime.now(timezone.utc)).total_seconds()
                return min(max(delta, 0.0), cap)
        except (TypeError, ValueError):
            pass
    return min(fallback, cap)


def first(payload: Any) -> Optional[dict]:
    """First record of an FMP-style response, or None.

    FMP ``/stable/`` endpoints return a single-element list or a bare object.
    Empty lists and any other shape return ``None``.
    """
    if isinstance(payload, list):
        return payload[0] if payload else None
    if isinstance(payload, dict):
        return payload
    return None


def pick(row: dict, *keys: str) -> Any:
    """First non-``None`` value among ``keys`` in ``row`` (or ``None``).

    For provider field aliases. Unlike ``row.get(a, row.get(b))``, it falls through
    when the primary key is present but null.
    """
    for k in keys:
        v = row.get(k)
        if v is not None:
            return v
    return None


def pct(x: Any) -> Optional[float]:
    """Percentage -> fraction (Finnhub reports margins/returns as percentages).

    Non-numeric input returns ``None``.
    """
    return x / 100.0 if isinstance(x, (int, float)) else None


def from_millions(x: Any) -> Optional[float]:
    """Millions of USD -> absolute dollars (e.g. Finnhub reports market cap in $M).

    Market cap is stored in absolute dollars, as in FMP ``quote.marketCap``, because
    the ``below_min_mktcap`` gate and the insider net-flow ratio assume dollars.
    Non-numeric input returns ``None``.
    """
    return x * 1.0e6 if isinstance(x, (int, float)) else None

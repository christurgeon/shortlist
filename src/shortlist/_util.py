"""Tiny dependency-free helpers shared across both stacks (screener + harness)."""

from typing import Any, Optional


def retry_after_seconds(header_value: Optional[str], fallback: float,
                        *, cap: float = 30.0) -> float:
    """Backoff delay (seconds) from an HTTP ``Retry-After`` header.

    RFC 7231 permits either a delay-seconds integer OR an HTTP-date; parse both,
    fall back to ``fallback`` on an absent/unparseable header (e.g. a date-form
    header from an intermediary, which a bare ``float()`` would choke on), and cap
    the result so a hostile/huge value can't hang a run. Never raises.
    """
    if header_value:
        try:
            return min(float(header_value), cap)
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

    FMP ``/stable/`` endpoints return either a single-element list or a bare
    object; this normalizes both to one dict. Empty lists and any other shape
    (``None``, scalar, ...) return ``None``.
    """
    if isinstance(payload, list):
        return payload[0] if payload else None
    if isinstance(payload, dict):
        return payload
    return None


def pick(row: dict, *keys: str) -> Any:
    """First non-``None`` value among ``keys`` in ``row`` (or ``None``).

    For provider field aliases (e.g. FMP ``/stable/`` vs legacy key names). Unlike
    ``row.get(a, row.get(b))``, this falls through when the primary key is
    *present but null* — providers routinely emit explicit ``null`` for a missing
    line item, which the default-arg form would return instead of trying ``b``.
    """
    for k in keys:
        v = row.get(k)
        if v is not None:
            return v
    return None


def pct(x: Any) -> Optional[float]:
    """Percentage -> fraction (Finnhub reports margins/returns as percentages).

    Non-numeric input (``None``, strings, ...) returns ``None`` rather than
    raising — the ``isinstance`` guard tolerates the soft-failure payloads
    upstream sources occasionally emit.
    """
    return x / 100.0 if isinstance(x, (int, float)) else None


def from_millions(x: Any) -> Optional[float]:
    """Millions of USD -> absolute dollars (e.g. Finnhub reports market cap in $M).

    A generic millions->dollars scaler. The motivating case is market cap, stored
    absolute to match FMP's ``quote.marketCap`` so the ``below_min_mktcap`` gate and
    the insider net-flow ratio (both assuming dollars) line up. Non-numeric input
    returns ``None`` rather than raising.
    """
    return x * 1.0e6 if isinstance(x, (int, float)) else None

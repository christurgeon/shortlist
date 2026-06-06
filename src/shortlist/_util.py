"""Tiny dependency-free helpers shared across both stacks (screener + harness)."""

from typing import Any, Optional


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
    """Millions of USD -> absolute dollars (Finnhub reports market cap in $M).

    Stored absolute to match FMP's ``quote.marketCap``; the ``below_min_mktcap``
    gate and the insider net-flow ratio both assume dollars.
    """
    return x * 1.0e6 if isinstance(x, (int, float)) else None

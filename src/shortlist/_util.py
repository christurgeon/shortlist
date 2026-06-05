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

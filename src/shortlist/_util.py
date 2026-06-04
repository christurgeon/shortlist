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

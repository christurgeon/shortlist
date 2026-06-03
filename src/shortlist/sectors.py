"""Sector applicability: SIC -> bucket and per-leg/gate applicability.

Pure, dependency-free leaf (pattern: providers/_form4.py). The ONLY interpreter of
config['sectors']. Scoring reads applicability through here; it must never key off
the free-text StockMetrics.sector (source-dependent and divergent across stacks).
"""
from __future__ import annotations

from typing import Optional


def extract_sic(company) -> Optional[str]:
    """Best-effort 4-digit SIC string off an edgartools Company. Swallows ALL
    exceptions and coerces missing/empty/'None'/non-numeric -> None, so a SIC
    lookup can never regress an otherwise-successful fetch and both stacks
    normalize identically."""
    if company is None:
        return None
    try:
        raw = getattr(company, "sic", None)
    except Exception:
        return None
    if raw is None:
        return None
    s = str(raw).strip().lstrip("0")    # tolerate zero-padded "0006798"
    return s if s.isdigit() else None   # rejects "", "None", "abc"


def resolve_bucket(sic, config: dict) -> str:
    """Map a SEC SIC (str|int|None) to a bucket name, or 'unknown'. First bucket
    whose inclusive ranges contain the SIC wins; buckets are an ORDERED list so
    resolution never depends on dict-key order."""
    code = _as_int(sic)
    if code is None:
        return "unknown"
    for bucket in config.get("sectors", {}).get("buckets", []):
        for lo, hi in bucket.get("sic_ranges", []):
            if lo <= code <= hi:
                return bucket["name"]
    return "unknown"


def leg_applicable(bucket: str, leg: str, config: Optional[dict]) -> bool:
    if bucket == "unknown" or config is None:
        return True
    return leg not in config.get("sectors", {}).get("masked_legs", [])


def gate_applicable(bucket: str, gate: str, config: Optional[dict]) -> bool:
    if bucket == "unknown" or config is None:
        return True
    return gate not in config.get("sectors", {}).get("masked_gates", [])


def _as_int(sic) -> Optional[int]:
    if sic is None:
        return None
    s = str(sic).strip()
    if not s or not s.isdigit():
        return None
    return int(s)

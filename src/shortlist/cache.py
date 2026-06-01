"""Persistent HTTP-response cache for the FMP/Finnhub data boundary.

Repeated ad-hoc runs would otherwise re-fetch identical data and exhaust free-tier
daily quotas (FMP ~250/day). This caches parsed JSON payloads on disk (SQLite) keyed
by (provider, endpoint, params) with a per-bucket TTL, so a warm re-run makes zero
upstream calls. See docs/superpowers/specs/2026-06-01-http-cache-design.md.

The module is a dependency-light leaf: only stdlib + env.redact_secrets.
"""

from __future__ import annotations

import hashlib
import json
import sys

_SECRET_PARAMS = {"apikey", "token", "api_key"}


def cache_key(provider: str, endpoint: str, params: dict) -> str:
    """Stable SHA-256 key for (provider, endpoint, params). Secrets are stripped by
    name so key rotation doesn't fragment the cache and no secret enters the store.
    Raises TypeError on a non-JSON-serializable param (fail loud — the call site is
    wrong) rather than papering over it with default=str. SHA-256 (not hash()) because
    hash() is PYTHONHASHSEED-salted and unstable across the separate processes a
    persistent cache must serve."""
    clean = {k: v for k, v in params.items() if k.lower() not in _SECRET_PARAMS}
    canon = json.dumps(clean, sort_keys=True, separators=(",", ":"))
    raw = f"v1:{provider}:{endpoint}:{canon}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _is_cacheable(payload: object) -> bool:
    """A 2xx body can still be a soft failure (FMP gating / no-coverage returns 200
    with [] or {}; Finnhub returns error-keyed bodies). Don't store those — they'd
    poison the cache for the whole TTL. Genuine successes here are always non-empty."""
    if payload is None or payload == [] or payload == {} or payload == "":
        return False
    if isinstance(payload, dict) and any(k.lower() == "error" for k in payload):
        return False
    return True


# (provider, endpoint-path) -> bucket. Keyed on the pair, not path alone, because
# "quote" is used by both FMP and Finnhub.
_BUCKET_BY_KEY = {
    ("fmp", "quote"): "quote",
    ("fmp", "stock-price-change"): "quote",
    ("finnhub", "quote"): "quote",
    ("fmp", "ratios-ttm"): "fundamentals",
    ("fmp", "ratios"): "fundamentals",
    ("fmp", "key-metrics-ttm"): "fundamentals",
    ("fmp", "key-metrics"): "fundamentals",
    ("finnhub", "stock/metric"): "fundamentals",
    ("fmp", "price-target-consensus"): "analyst",
    ("fmp", "grades-consensus"): "analyst",
    ("fmp", "insider-trading/search"): "analyst",
    ("finnhub", "stock/recommendation"): "analyst",
    ("finnhub", "stock/insider-sentiment"): "analyst",
    ("fmp", "income-statement"): "statements",
    ("fmp", "balance-sheet-statement"): "statements",
    ("fmp", "cash-flow-statement"): "statements",
    ("fmp", "profile"): "profile",
    ("finnhub", "stock/profile2"): "profile",
}

_DEFAULT_TTLS = {
    "quote": 21600,         # 6h  — quotes, price-change, live metric
    "fundamentals": 86400,  # 1d  — ratios, key-metrics
    "analyst": 86400,       # 1d  — grades, targets, recommendations, insider-sentiment
    "statements": 604800,   # 7d  — income/balance/cashflow (change only on a filing)
    "profile": 604800,      # 7d  — company profile
    "default": 86400,       # 1d  — any unmapped endpoint
}

_warned_unmapped: set = set()


def ttl_for(provider: str, endpoint: str, ttl_config: dict) -> float:
    """Resolve the TTL (seconds) for an endpoint; ttl_config overrides per bucket.
    An unmapped (provider, endpoint) falls to the 1d default and is logged once so a
    new endpoint surfaces rather than silently inheriting a wrong TTL."""
    bucket = _BUCKET_BY_KEY.get((provider, endpoint))
    if bucket is None:
        bucket = "default"
        if (provider, endpoint) not in _warned_unmapped:
            _warned_unmapped.add((provider, endpoint))
            print(f"cache: unmapped endpoint {provider}/{endpoint} -> default TTL",
                  file=sys.stderr)
    return ttl_config.get(bucket, _DEFAULT_TTLS[bucket])

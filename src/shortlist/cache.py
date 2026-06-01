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

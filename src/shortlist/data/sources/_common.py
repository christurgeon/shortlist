from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Optional

from ...env import redact_secrets
from ..diskcache import read_json_cache, write_json_cache


async def _load_ticker_name_index(client: Any, cache_dir: str) -> tuple[dict, Optional[str]]:
    """Bulk-load SEC company_tickers.json into a ticker->name index, shared by
    GovContractsSource and LobbyingSource (both resolve ticker->entity name the
    same way via `backtest.xbrl`'s month-cached bulk fetch). Returns
    `(index, error)`: error is None on success; on failure index is `{}` and
    error is the redacted exception string."""
    try:
        from ...backtest import xbrl
        month = date.today().strftime("%Y-%m")
        raw = await xbrl.fetch_company_tickers_raw(client, cache_dir=cache_dir, month=month)
        return xbrl.build_name_index(raw), None
    except Exception as e:
        return {}, redact_secrets(e)


def _read_versioned_cache(path: Path, version: int) -> Optional[dict]:
    """Version-gated JSON-dict cache read, shared by GovContractsSource and
    LobbyingSource: a stale-shape / legacy un-versioned payload is treated as a
    miss so bumping `version` refetches rather than deserializing garbage."""
    payload = read_json_cache(path)
    if isinstance(payload, dict) and payload.get("v") == version:
        return payload
    return None


def _write_versioned_cache(path: Path, version: int, payload: dict) -> None:
    write_json_cache(path, {"v": version, **payload})

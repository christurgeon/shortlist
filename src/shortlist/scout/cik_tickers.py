"""CIK -> ticker resolver, common-stock preferred. Reads SEC company_tickers.json.

Verified (2026-06-28): the file lists the common stock FIRST per CIK (PECE before
PECEU/PECER/PECEW; GOOGL before GOOG*; BAC before BAC-PB), and first-occurrence alone
is correct for 1,472/1,473 multi-ticker CIKs. So first-occurrence is authoritative and
the suffix backstop is sibling-relative ONLY — a blanket 'de-prioritize W/U/R/-P' rule
would mis-bind ~54 liquid issuers to a foreign-OTC (*F) or preferred sibling.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import httpx

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
# unit/warrant/right suffixes whose base, IF also a ticker of the same CIK, is the common
_UNIT_SUFFIX = re.compile(r"^(?P<base>[A-Z]{2,})(?:U|W|R|WS)$")
_PREF_SUFFIX = re.compile(r"^(?P<base>[A-Z]+)-P[A-Z]?$")


def _norm_cik(cik: str | int) -> str:
    return f"{int(cik):010d}"


def build_cik_to_ticker(raw: dict) -> dict[str, str]:
    """{10-digit CIK -> best ticker}. First occurrence wins; a unit/warrant/right or
    hyphenated preferred that appears FIRST is replaced by its base ONLY when the base is
    also a ticker of the same CIK (sibling-relative)."""
    by_cik: dict[str, list[str]] = {}
    for row in raw.values():
        cik = _norm_cik(row["cik_str"])
        by_cik.setdefault(cik, []).append(str(row["ticker"]).upper())
    out: dict[str, str] = {}
    for cik, tickers in by_cik.items():
        chosen = tickers[0]                       # first-occurrence authoritative
        members = set(tickers)
        m = _UNIT_SUFFIX.match(chosen) or _PREF_SUFFIX.match(chosen)
        if m and m.group("base") in members:      # sibling-relative backstop only
            chosen = m.group("base")
        out[cik] = chosen
    return out


def resolve_ticker(cik: str | int, index: dict[str, str]) -> str | None:
    try:
        return index.get(_norm_cik(cik))
    except (TypeError, ValueError):
        return None


def load_cik_to_ticker(identity: str, *, cache_dir: str = ".cache/sec_tickers",
                       _today: date | None = None, _client: httpx.Client | None = None) -> dict[str, str]:
    """Day-cached company_tickers.json -> resolver index. SEC blocks UA-less GETs, so a
    contact-email User-Agent is mandatory. Never raises: returns {} on any failure."""
    day = (_today or date.today()).isoformat()
    cp = Path(cache_dir) / f"company_tickers-{day}.json"
    try:
        if cp.exists():
            return build_cik_to_ticker(json.loads(cp.read_text()))
        client = _client or httpx.Client(timeout=30.0, headers={"User-Agent": identity})
        try:
            resp = client.get(_TICKERS_URL)
            resp.raise_for_status()
            raw = resp.json()
        finally:
            if _client is None:
                client.close()
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps(raw))
        return build_cik_to_ticker(raw)
    except Exception:  # noqa: BLE001 — degrade: empty index -> signal abstains, never crashes
        return {}

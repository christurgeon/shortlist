"""SEC XBRL companyfacts I/O for the backtest: ticker->CIK resolution and a
disk-cached companyfacts fetch. Keyless; SEC fair-access requires a descriptive
User-Agent carrying a contact email (SEC_IDENTITY)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"


def build_cik_index(raw: dict) -> dict[str, str]:
    """{TICKER -> zero-padded 10-digit CIK} from SEC company_tickers.json."""
    idx = {}
    for row in raw.values():
        tk = str(row.get("ticker", "")).upper()
        cik = row.get("cik_str")
        if tk and cik is not None:
            idx[tk] = f"{int(cik):010d}"
    return idx


def cik_for(ticker: str, index: dict[str, str]) -> Optional[str]:
    return index.get(ticker.upper())

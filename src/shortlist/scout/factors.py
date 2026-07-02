"""Fama-French 3-factor monthly series (Ken French Data Library, free) for risk-adjusting
the calendar-time portfolio. Percent -> decimal. The parser is pure + tested; the fetch is a
thin day-cached I/O wrapper. See spec §6.3."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_Factors_CSV.zip"


def parse_ff3_monthly(text: str) -> dict[str, tuple[float, float, float, float]]:
    """Parse the monthly block of the F-F research-factors CSV into
    {"YYYY-MM": (mkt_rf, smb, hml, rf)} as decimals. Stops at the 'Annual Factors' block;
    ignores any row whose first field is not a 6-digit YYYYMM with month 01-12."""
    out: dict[str, tuple[float, float, float, float]] = {}
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        key = parts[0]
        if len(key) != 6 or not key.isdigit():
            continue
        mm = int(key[4:6])
        if not (1 <= mm <= 12):
            continue
        try:
            mkt, smb, hml, rf = (float(parts[1]) / 100.0, float(parts[2]) / 100.0,
                                 float(parts[3]) / 100.0, float(parts[4]) / 100.0)
        except ValueError:
            continue
        out[f"{key[:4]}-{key[4:6]}"] = (mkt, smb, hml, rf)
    return out


async def fetch_ff3_monthly(client, *, cache_dir: str, today: str) -> dict[str, tuple[float, float, float, float]]:
    """Day-cached fetch+parse of the FF3 monthly factors. `client` is an httpx.AsyncClient."""
    path = Path(cache_dir) / f"ff3-monthly-{today}.json"
    if path.exists():
        try:
            return {k: tuple(v) for k, v in json.loads(path.read_text()).items()}
        except (ValueError, OSError):
            pass
    resp = await client.get(_URL, timeout=30.0)
    resp.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(resp.content))
    text = z.read(z.namelist()[0]).decode("latin-1")
    parsed = parse_ff3_monthly(text)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(parsed))
    return parsed

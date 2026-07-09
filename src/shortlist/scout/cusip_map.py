"""CUSIP -> ticker resolver leaf for the 13F cloning originator (scout).

13F information tables identify holdings by 9-char CUSIP, not ticker. A layered resolver:

1. **SEC fails-to-deliver (FTD) files** (keyless): the two most recent published half-month
   files (`sec.gov/files/data/fails-deliver-data/cnsfails{YYYYMM}{a|b}.zip`) carry
   `SETTLEMENT DATE|CUSIP|SYMBOL|...` rows. Coverage is broad (any name with a single fail
   appears) but not universal. A CUSIP can map to more than one SYMBOL across rows (symbol
   churn) — the row with the **most recent settlement date wins** (9-char CUSIPs already
   encode the share class, so class collisions are not the failure mode).
2. **Issuer-name fallback:** conservative EXACT normalized-name match of `nameOfIssuer`
   against the SEC `company_tickers.json` names (uppercase, strip punctuation + INC/CORP/CO
   /LTD/PLC-style suffix tokens). Ambiguous normalized names (two CIKs collapse to one key)
   abstain rather than guess.
3. **Abstain** — return None; the signal counts abstentions in its coverage detail.

Pure parse/aggregation is separated from the live fetch (the shared-leaf pattern) so tests
run fully offline. The live FTD fetch is throttled by the caller's SEC min-interval helper.
"""
from __future__ import annotations

import io
import re
import zipfile
from datetime import date
from pathlib import Path
from typing import Callable, Optional

from ..data.diskcache import read_json_cache, write_json_cache
from ..env import redact_secrets

_FTD_URL = "https://www.sec.gov/files/data/fails-deliver-data/cnsfails{ym}{half}.zip"

# Corporate-form / share-class suffix tokens stripped before an EXACT name comparison. The
# fallback is deliberately conservative (FTD is the primary resolver) — it must abstain on a
# near miss, never fuzzy-match, so we only cancel boilerplate that is noise across both the
# 13F nameOfIssuer and the company_tickers title.
_SUFFIX_TOKENS = frozenset({
    "INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "COMPANIES",
    "LTD", "LIMITED", "PLC", "LP", "LLC", "LLP", "LP.", "HOLDINGS", "HLDGS", "HLDG",
    "GROUP", "GRP", "TRUST", "THE", "COM", "CLASS", "CL", "SA", "AG", "NV", "SE", "PLC.",
})


def normalize_issuer_name(name: str | None) -> str:
    """Uppercase, drop punctuation, strip corporate-form/share-class suffix tokens, collapse
    whitespace. Returns '' for an empty/degenerate name (which never matches — abstain)."""
    if not name:
        return ""
    up = re.sub(r"[^A-Z0-9 ]", " ", str(name).upper())
    toks = [t for t in up.split() if t and t not in _SUFFIX_TOKENS]
    return " ".join(toks)


def build_name_to_ticker(raw: dict) -> dict[str, str]:
    """{normalized issuer name -> ticker} from the raw company_tickers.json payload.
    First-occurrence per CIK wins (common stock is listed first per CIK, mirroring
    cik_tickers), so a same-CIK dual-class issuer whose classes share a normalized name
    (GOOGL/GOOG, BRK-A/BRK-B) keeps the first ticker. Only a CROSS-CIK normalized-name
    collision (two DIFFERENT issuers collapsing to one key) is ambiguous and dropped
    (abstain, never guess)."""
    out: dict[str, str] = {}
    first_cik: dict[str, str] = {}
    ambiguous: set[str] = set()
    for row in raw.values():
        key = normalize_issuer_name(row.get("title"))
        tkr = str(row.get("ticker") or "").upper()
        cik = str(row.get("cik_str") or "")   # falsy/absent -> "" (an UNKNOWN cik, never "same")
        if not key or not tkr:
            continue
        prev = out.get(key)
        if prev is None:
            out[key] = tkr
            first_cik[key] = cik
        elif tkr != prev and not (cik and first_cik[key] and cik == first_cik[key]):
            # Keep the first ticker ONLY on a genuine same-CIK dual-class collision (both ciks
            # truthy AND equal). If EITHER row's cik is falsy/absent, two different issuers can
            # collapse to cik "" == "" and the first ticker would be a wrong-ticker guess -> drop.
            ambiguous.add(key)          # DIFFERENT (or unknown) issuer, same normalized name
    for key in ambiguous:
        out.pop(key, None)
    return out


def _cusip9(cusip: str | None) -> str:
    return str(cusip or "").strip().upper()


def parse_ftd_text(text: str) -> list[dict]:
    """Pipe-delimited FTD rows -> `[{"settlement","cusip","symbol"}]`. Skips the header and
    any short/blank row. Never raises."""
    rows: list[dict] = []
    for line in text.splitlines():
        parts = line.split("|")
        if len(parts) < 3:
            continue
        settle, cusip, symbol = parts[0].strip(), parts[1].strip().upper(), parts[2].strip().upper()
        if not settle or not settle[:1].isdigit():   # header ("SETTLEMENT DATE") or junk
            continue
        if not cusip or not symbol:
            continue
        rows.append({"settlement": settle, "cusip": cusip, "symbol": symbol})
    return rows


def parse_ftd_zip(raw: bytes) -> list[dict]:
    """Unzip the single FTD .txt member and parse it. Never raises: a bad archive -> []."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = z.namelist()
            if not names:
                return []
            text = z.read(names[0]).decode("latin-1")
        return parse_ftd_text(text)
    except Exception:  # noqa: BLE001 — a corrupt archive is a cache miss, never a crash
        return []


def build_cusip_to_symbol(row_lists: list[list[dict]]) -> dict[str, str]:
    """{9-char CUSIP -> SYMBOL}, most-recent-settlement-date wins on symbol churn.
    `settlement` is a YYYYMMDD string, so a lexicographic max is a chronological max."""
    best: dict[str, tuple[str, str]] = {}
    for rows in row_lists:
        for r in rows:
            c = _cusip9(r.get("cusip"))
            s = str(r.get("settlement") or "")
            sym = str(r.get("symbol") or "").upper()
            if not c or not sym:
                continue
            cur = best.get(c)
            if cur is None or s > cur[0]:
                best[c] = (s, sym)
    return {c: v[1] for c, v in best.items()}


def _period_filenames(today: date, max_attempts: int) -> list[tuple[str, str]]:
    """Newest-first (filename, cache-key) FTD candidates, walking back half-months from the
    current month ('b' = second half before 'a' = first half). Bounded at `max_attempts`."""
    out: list[tuple[str, str]] = []
    y, m = today.year, today.month
    while len(out) < max_attempts:
        ym = f"{y}{m:02d}"
        for half in ("b", "a"):
            out.append((_FTD_URL.format(ym=ym, half=half), f"cnsfails{ym}{half}"))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return out[:max_attempts]


def _http_get_bytes(url: str, identity: str, timeout: float) -> Optional[bytes]:
    """Fetch a URL as bytes; return None on a non-200 (a not-yet-published FTD file 404s)."""
    import httpx  # lazy: only for live runs
    with httpx.Client(timeout=timeout, headers={"User-Agent": identity}) as c:
        r = c.get(url)
        if r.status_code != 200:
            return None
        return r.content


def fetch_ftd_files(identity: str, *, cache_dir: str = ".cache/sec_ftd", timeout: float = 30.0,
                    today: date | None = None, max_attempts: int = 6, want: int = 2,
                    throttle: Callable[[], None] | None = None,
                    _http_get: Callable[[str, str, float], Optional[bytes]] | None = None,
                    ) -> list[list[dict]]:
    """Download + parse the `want` most-recent published FTD files, walking back from the
    current half-month (bounded at `max_attempts` — the current period may not be posted
    yet). Returns a list of per-file row lists (newest first). Never raises: a failure yields
    fewer files (fewer resolutions), the resolver then leans on the name fallback / abstains.
    `throttle` is invoked before each network GET (SEC fair-access).

    Cache envelope distinguishes real rows from an empty-marker so neither a legacy poisoned
    cache nor a persistently-empty parse can wedge the walk-back:
    - NON-EMPTY parsed rows -> cached FOREVER by filename as a plain list (a published FTD
      file is immutable). Read back and used unconditionally.
    - EMPTY parse (200-status truncated zip / HTML error body — `parse_ftd_zip` swallows all)
      -> write a marker `{"empty_on": "<today iso>"}` instead of rows. A fresh marker (< 7d)
      SKIPS the file WITHOUT downloading (a bounded backoff — counts as a failed attempt, so
      the walk-back continues); a stale marker (>= 7d) refetches (the file may have been
      re-posted intact).
    - A legacy `[]` cache (or any empty list) with NO marker is treated as a MISS -> refetch
      once, which HEALS pre-fix poisoned files."""
    getter = _http_get or _http_get_bytes
    today_d = today or date.today()
    out: list[list[dict]] = []
    for url, key in _period_filenames(today_d, max_attempts):
        if len(out) >= want:
            break
        cp = Path(cache_dir) / f"{key}.json"
        cached = read_json_cache(cp)
        if isinstance(cached, list) and cached:
            out.append(cached)                        # immutable non-empty rows -> use
            continue
        if isinstance(cached, dict) and cached.get("empty_on"):
            try:
                marked = date.fromisoformat(str(cached["empty_on"]))
            except (TypeError, ValueError):
                marked = None
            if marked is not None and (today_d - marked).days < 7:
                continue                              # fresh empty-marker -> skip, no download
            # stale marker (>= 7d) -> fall through and refetch (file may be re-posted intact)
        # else: absent OR legacy-[] (no marker) -> a MISS; refetch (heals poisoned caches)
        try:
            if throttle is not None:
                throttle()
            raw = getter(url, identity, timeout)
        except Exception:  # noqa: BLE001 — a transient fetch error just skips this period
            raw = None
        if not raw:
            continue                                  # not published yet -> walk back
        rows = parse_ftd_zip(raw)
        if not rows:
            # Empty parse: persist a dated marker (NOT the immutable rows key) so we don't
            # re-download this multi-MB zip every run for 7 days, then retry. Counts as a
            # failed attempt (no `out` append) -> the walk-back continues.
            write_json_cache(cp, {"empty_on": today_d.isoformat()})
            continue
        write_json_cache(cp, rows)
        out.append(rows)
    return out


class CusipResolver:
    """Layered CUSIP/issuer-name -> ticker resolver. Pure (no I/O); the caller builds the two
    indexes. `resolve` returns None (abstain) on a miss — never a guess."""

    def __init__(self, cusip_to_symbol: dict[str, str], name_to_ticker: dict[str, str]) -> None:
        self.cusip_to_symbol = cusip_to_symbol
        self.name_to_ticker = name_to_ticker

    def resolve(self, cusip: str | None, name: str | None) -> Optional[str]:
        sym = self.cusip_to_symbol.get(_cusip9(cusip))
        if sym:
            return sym
        key = normalize_issuer_name(name)
        if key:
            return self.name_to_ticker.get(key)
        return None


def load_cusip_resolver(identity: str, *, resolver_cache_dir: str = ".cache/sec_tickers",
                        ftd_cache_dir: str = ".cache/sec_ftd", timeout: float = 30.0,
                        today: date | None = None,
                        throttle: Callable[[], None] | None = None) -> CusipResolver:
    """Build a live `CusipResolver` from the day-cached company_tickers.json (name fallback)
    + the two most-recent FTD files (CUSIP map). Never raises: any failure yields emptier
    indexes and the resolver simply abstains more."""
    from .cik_tickers import load_raw_company_tickers
    try:
        raw = load_raw_company_tickers(identity, cache_dir=resolver_cache_dir)
        name_index = build_name_to_ticker(raw)
    except Exception:  # noqa: BLE001
        name_index = {}
    try:
        ftd = fetch_ftd_files(identity, cache_dir=ftd_cache_dir, timeout=timeout,
                              today=today, throttle=throttle)
        cusip_index = build_cusip_to_symbol(ftd)
    except Exception as exc:  # noqa: BLE001 — never let resolver construction crash the scan
        _ = redact_secrets(str(exc))
        cusip_index = {}
    return CusipResolver(cusip_index, name_index)

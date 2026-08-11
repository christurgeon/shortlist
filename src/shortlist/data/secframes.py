"""SEC XBRL `frames` — one concept across EVERY filer, in ONE request.

`https://data.sec.gov/api/xbrl/frames/{ns}/{tag}/{unit}/{frame}.json` returns every filer's
value for a single concept and period. Measured 2026-08-05: `Assets`/CY2026Q1I is 5,498
filers in 0.7 MB / 0.5 s, so a full-universe fundamental snapshot costs ~12 requests and
~8 MB. See `docs/audits/2026-08-05-standing-screen-data-source.md`.

Why this and not the alternatives:
  - **DERA bulk** is one request but **127-215 days stale** (its newest published quarter
    holds filings filed 4-7 months ago) -> cannot inform a live decision.
  - **per-ticker companyfacts** is current but ~4,620 requests and ~3.8 GB.

**LIVE USE ONLY — never backtest from this.** A frame carries no filing date (its fields are
`accn, cik, end, entityName, loc, val`), so it returns the CURRENT best value for a period
and a later restatement silently overwrites what was knowable at the time. Historical work
must use the DERA archive, which carries `filed` per row. Importing frames into a backfill
would put restatement look-ahead into every verdict.

Pure parsing is separated from I/O (the `_form4.py` / `data/finra.py` shared-leaf pattern) so
the whole thing is testable offline.

**No production caller since 2026-08-11.** Its consumer (the quality floor) retired with the scout
(`docs/audits/2026-08-11-scout-retirement.md`), so nothing in `shortlist` imports this
on the `/screen` or `/deep` path. Same deal as `shortlist/edgar/`: CI pins the PARSE
shapes, but upstream shape drift is only caught by the live tests, which are
`pytest.mark.live` and skip by default.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import httpx

from ..env import redact_secrets
from .diskcache import read_json_cache, write_json_cache

_BASE = "https://data.sec.gov/api/xbrl/frames"


@dataclass(frozen=True)
class Frame:
    """One filer's value for one concept in one period."""
    val: float
    end: str            # period end (ISO) — NOT a filing date; see the module docstring
    accn: str = ""


def frame_url(tag: str, frame: str, *, ns: str = "us-gaap", unit: str = "USD") -> str:
    return f"{_BASE}/{ns}/{tag}/{unit}/{frame}.json"


def _norm_cik(cik: Any) -> Optional[str]:
    try:
        return f"{int(cik):010d}"
    except (TypeError, ValueError):
        return None


def parse_frame(payload: Any) -> dict[str, Frame]:
    """`{10-digit CIK -> Frame}`. Never raises.

    A malformed row is skipped INDIVIDUALLY — one bad entry among thousands must not discard
    the whole frame (the `build_cik_to_ticker` precedent). CIKs are zero-padded to 10 so they
    join directly with `cik_tickers` and the point-in-time `Symbology` resolver, both of which
    key on that form; a raw int would silently miss every lookup.
    """
    try:
        rows = payload.get("data")
    except AttributeError:
        return {}
    if not isinstance(rows, list):
        return {}
    out: dict[str, Frame] = {}
    for row in rows:
        try:
            cik = _norm_cik(row.get("cik"))
            val = float(row.get("val"))
        except (AttributeError, TypeError, ValueError):
            continue
        if cik is None:
            continue
        out.setdefault(cik, Frame(val=val, end=str(row.get("end") or ""),
                                  accn=str(row.get("accn") or "")))
    return out


def merge_family(frames: Iterable[dict[str, Frame]]) -> dict[str, Frame]:
    """Priority union across a tag family: the FIRST frame reporting a CIK owns it.

    Families are never summed — a filer tagging both `Revenues` and
    `RevenueFromContractWithCustomer...` would be double-counted. This mirrors
    `_xbrl_facts.annual_series`, which fills each period from the first concept that reports
    it. The union matters: measured on CY2025, the four revenue tags cover 2,663 / 2,191 /
    643 / 0 filers individually but **4,605 as a union**.
    """
    out: dict[str, Frame] = {}
    for f in frames:
        for cik, fr in (f or {}).items():
            out.setdefault(cik, fr)
    return out


def instant_frames(today: date, n: int = 3) -> list[str]:
    """Instant (balance-sheet) frame labels, newest first: `["CY2026Q2I", "CY2026Q1I", ...]`.

    Starts at the last COMPLETED quarter and walks back. Walking back matters: a frame keeps
    filling as filers report (CY2026Q2I held only 1,807 filers on 2026-08-05 against
    CY2026Q1I's 5,498), so an older, fuller frame backfills filers missing from the newest.
    Feed the list to `merge_family` newest-first — first-wins then prefers fresh data and
    falls back to older only where fresh is absent.
    """
    q = (today.month - 1) // 3          # 0-3; the quarter `today` is IN
    y = today.year
    out = []
    for _ in range(max(0, n)):
        q -= 1                          # step back to the last COMPLETED quarter
        if q < 0:
            y, q = y - 1, 3
        out.append(f"CY{y}Q{q + 1}I")
    return out


def annual_frames(today: date, n: int = 3) -> list[str]:
    """Annual (duration) frame labels, newest first: `["CY2025", "CY2024", ...]`."""
    return [f"CY{today.year - 1 - i}" for i in range(max(0, n))]


def _cache_path(cache_dir: str, ns: str, tag: str, unit: str, frame: str, today: date) -> Path:
    return Path(cache_dir) / f"{ns}-{tag}-{unit}-{frame}-{today.isoformat()}.json"


def fetch_frame(tag: str, frame: str, *, identity: str,
                cache_dir: str = ".cache/sec_frames",
                ns: str = "us-gaap", unit: str = "USD",
                today: Optional[date] = None,
                client: Optional[httpx.Client] = None,
                throttle: Optional[Callable[..., None]] = None,
                timeout: float = 60.0) -> dict[str, Frame]:
    """Day-cached `{CIK -> Frame}` for one concept/period. **Never raises** (degrades to {}).

    Day-cached rather than cached forever: a frame keeps filling as filers report (CY2026Q2I
    held 1,807 filers on 2026-08-05 and will keep growing), so an immutable cache would
    freeze a partial period.

    A FAILURE IS NEVER CACHED — caching a 404/5xx would pin the whole day to "this concept
    does not exist", the same class of bug as caching a WAF block as an empty price series.
    """
    today = today or date.today()
    cp = _cache_path(cache_dir, ns, tag, unit, frame, today)
    cached = read_json_cache(cp)
    if cached is not None:
        return parse_frame(cached)

    if throttle is None:
        from ..edgar.sec_throttle import sec_throttle
        throttle = sec_throttle()
    own = client is None
    cl = client or httpx.Client(timeout=timeout, headers={"User-Agent": identity})
    try:
        throttle("secframes")     # data.sec.gov is SEC load: paced AND counted
        resp = cl.get(frame_url(tag, frame, ns=ns, unit=unit))
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001 — a missing concept/period is normal; degrade
        import warnings
        warnings.warn(f"secframes: {ns}/{tag}/{frame} unavailable: "
                      f"{redact_secrets(str(exc))}", stacklevel=2)
        return {}
    finally:
        if own:
            cl.close()
    parsed = parse_frame(payload)
    if parsed:                    # only a payload that actually parsed is worth caching
        write_json_cache(cp, payload)
    return parsed


def fetch_family(tags: list[str], frame: str, **kw) -> dict[str, Frame]:
    """`merge_family` over `tags` in PRIORITY order — one request per tag."""
    return merge_family([fetch_frame(t, frame, **kw) for t in tags])

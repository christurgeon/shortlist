"""Pure 13F information-table parsing + new-position diff for the marquee-fund cloning
originator (scout). See docs/superpowers/specs/2026-07-09-thirteenf-buyback-originators-design.md §1.

Fetch and pure aggregation are separated (the shared-leaf pattern) so the whole diff runs
offline in tests. Live SEC requests go through the PROCESS-WIDE `sec_throttle()`
(`scout/sec_throttle.py`) — this module used to own a private `SecThrottle`, which meant its
~3 req/s ran on top of the Form 4 sweep's rate rather than inside one shared ceiling; see
`docs/audits/2026-08-05-discovery-funnel-audit.md` §4. `SecThrottle` is re-exported here for
back-compat.

Marquee-fund new positions clone an established-positive academic prior (Martin &
Puthenpurackal 2008; Cohen-Polk-Silli 2010 "best ideas"), measured from the FILING date
(the 45-day disclosure lag is priced into the literature). Discovery plumbing only —
scoring.score() is byte-identical, the downstream scorer + gates remain the skeptic.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Callable, Optional

from .eightk import _junk_suffix
from .models import Emission
from .sec_throttle import SecThrottle, sec_throttle  # noqa: F401 — SecThrottle re-exported

SIGNAL = "edgar:13f_new_position"

# Emission strength = within-book conviction, capped at 1.0 (design §1.8): a 5%-of-book new
# position is a full-conviction bet. Below the 13D/Form-4 marquee tier only via the smaller
# default weight the signal ships at (the information is up to 45 days stale).


# --- submissions -> latest/prior 13F-HR selection --------------------------------------

def parse_submissions_13fhr(subm: dict) -> list[dict]:
    """Recent EXACT `13F-HR` filings (newest first) as `[{"accession","filing_date",
    "period"}]`. `13F-HR/A` amendments are EXCLUDED (a restatement diff would double-fire).
    The SEC `filings.recent` arrays are already newest-first. Never raises."""
    try:
        recent = (subm.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        accs = recent.get("accessionNumber") or []
        fdates = recent.get("filingDate") or []
        periods = recent.get("reportDate") or []
    except AttributeError:
        return []
    out: list[dict] = []
    for i, form in enumerate(forms):
        if form != "13F-HR":                          # exact — excludes 13F-HR/A
            continue
        out.append({"accession": accs[i] if i < len(accs) else "",
                    "filing_date": fdates[i] if i < len(fdates) else "",
                    "period": periods[i] if i < len(periods) else ""})
    return out


# --- information table XML parse -------------------------------------------------------

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_infotable(xml_bytes: bytes | str) -> list[dict]:
    """13F information-table XML -> `[{"cusip","name","title","value","shares","put_call",
    "ssh_type"}]`, one dict per `<infoTable>` row. Namespace-agnostic (strips namespaces via
    local tag names). `value` is a float (SEC reports whole dollars post-2023, $1000s before
    — the within-filing weight normalizes it away either way).

    `shares` (`sshPrnamt`) is the only PRICE-INDEPENDENT quantity in the filing, and is what
    `material_add_diff` detects on: `value` is quarter-end MARKET value, so a position whose
    stock rose 50% with zero shares bought shows a ~50% book-weight increase and would
    otherwise read as fund conviction. `None` (missing/unparseable) is kept distinct from 0.0
    so downstream can abstain instead of reading a gap as "holds nothing".

    Never raises: a parse error yields []."""
    try:
        root = ET.fromstring(xml_bytes if isinstance(xml_bytes, bytes)
                             else xml_bytes.encode("utf-8"))
    except ET.ParseError:
        return []
    rows: list[dict] = []
    for it in root.iter():
        if _local(it.tag) != "infoTable":
            continue
        rec: dict = {"cusip": "", "name": "", "title": "", "value": None,
                     "put_call": "", "ssh_type": "", "shares": None}
        for child in it.iter():
            tag = _local(child.tag)
            txt = (child.text or "").strip()
            if tag == "cusip":
                rec["cusip"] = txt.upper()
            elif tag == "nameOfIssuer":
                rec["name"] = txt
            elif tag == "titleOfClass":
                rec["title"] = txt
            elif tag == "putCall":
                rec["put_call"] = txt
            elif tag == "sshPrnamt":
                try:
                    rec["shares"] = float(txt.replace(",", ""))
                except ValueError:
                    rec["shares"] = None
            elif tag == "sshPrnamtType":
                rec["ssh_type"] = txt
            elif tag == "value":
                try:
                    rec["value"] = float(txt.replace(",", ""))
                except ValueError:
                    rec["value"] = None
        rows.append(rec)
    return rows


def aggregate_positions(rows: list[dict]) -> dict[str, dict]:
    """Rows -> `{cusip -> {"value","name","title"}}`. Drops option rows (`put_call` present)
    and non-share rows (`ssh_type != "SH"` — PRN convertible debt), then SUMS `value` across
    the multiple `<infoTable>` rows a single holding legitimately spans (sole/shared/none
    voting split, combined-manager filings). Never raises."""
    agg: dict[str, dict] = {}
    for r in rows:
        if (r.get("put_call") or "").strip():
            continue                                  # options are not equity positions
        if (r.get("ssh_type") or "").strip().upper() != "SH":
            continue                                  # PRN (convertible debt), not equity
        cusip = (r.get("cusip") or "").strip().upper()
        val = r.get("value")
        if not cusip or val is None:
            continue
        cur = agg.get(cusip)
        if cur is None:
            agg[cusip] = {"value": float(val), "name": r.get("name") or "",
                          "title": r.get("title") or ""}
        else:
            cur["value"] += float(val)
    return agg


def new_position_diff(latest: dict[str, dict], prior: dict[str, dict], *,
                      min_position_pct: float = 0.005,
                      full_strength_pct: float = 0.05) -> list[dict]:
    """New positions (CUSIP in `latest`, absent in `prior`) that clear `min_position_pct` of
    the latest book, sorted by within-book weight descending. Each carries `weight` and a
    `strength` = min(1.0, weight / full_strength_pct) (design §1.8 — a `full_strength_pct`
    position is full conviction). Material ADDS to existing positions are out of scope
    (v1: new positions are the sharpest best-idea event). An empty/zero-total latest book
    yields [] (no division by zero)."""
    total = sum(p["value"] for p in latest.values() if p.get("value"))
    if total <= 0:
        return []
    out: list[dict] = []
    for cusip, pos in latest.items():
        if cusip in prior:
            continue                                  # not new
        weight = pos["value"] / total
        if weight < min_position_pct:
            continue
        strength = min(1.0, weight / full_strength_pct) if full_strength_pct > 0 else 1.0
        out.append({"cusip": cusip, "name": pos.get("name") or "",
                    "title": pos.get("title") or "", "value": pos["value"],
                    "weight": weight, "strength": strength})
    out.sort(key=lambda d: (-d["weight"], d["cusip"]))
    return out


def thirteenf_emissions(new_positions: list[dict], *, resolve_fn: Callable,
                        fund_name: str, period: str, filing_date: str,
                        fund_cik: str | int | None = None, accession: str = "",
                        deny_list=None, top_n: int = 10) -> tuple[list[Emission], int]:
    """New-position dicts -> `(emissions, n_abstained)`. Resolves each CUSIP/name to a
    ticker (abstain on a miss — counted, never guessed), drops deny-listed + 5th-letter
    junk-suffix symbols, dedups within the filing, and caps at `top_n` KEPT names (an
    unresolved position never consumes a slot). Emissions carry `cik=None` (the CUSIP
    resolver yields a ticker but no *subject* CIK — a stated measurement limit); the FUND's
    identity + filing accession ride `meta` (`fund_cik`/`fund_name`/`adsh`) as firehose join
    keys for per-fund attribution, matching the 8-K/13D/buyback emissions. Never raises."""
    deny = {str(d).upper() for d in (deny_list or [])}
    out: list[Emission] = []
    seen: set[str] = set()
    abstained = 0
    for pos in new_positions:                         # already weight-desc
        tkr = resolve_fn(pos["cusip"], pos["name"])
        if not tkr:
            abstained += 1
            continue
        tkr = str(tkr).upper()
        if tkr in deny or _junk_suffix(tkr) or tkr in seen:
            continue
        seen.add(tkr)
        pct = pos["weight"] * 100.0
        qlabel = _quarter_label(period)
        ev = (f"{fund_name} new 13F position ({qlabel}, filed {filing_date}): "
              f"{pct:.1f}% of book")
        out.append(Emission(tkr, SIGNAL, pos["strength"], ev, is_discovery=True,
                            cik=None, meta={"cusip": pos["cusip"], "period": period,
                                            "filing_date": filing_date, "weight": pos["weight"],
                                            "fund_cik": (str(fund_cik) if fund_cik is not None
                                                         else None),
                                            "fund_name": fund_name, "adsh": accession}))
        if len(out) >= top_n:
            break
    return out, abstained


def _quarter_label(period: str) -> str:
    """A 'Q1 2026'-style label from an ISO reportDate (YYYY-MM-DD, quarter end). Falls back
    to the raw period string on any parse issue."""
    try:
        y, m, _d = period.split("-")
        q = {"03": "Q1", "06": "Q2", "09": "Q3", "12": "Q4"}.get(m, f"M{m}")
        return f"{q} {y}"
    except (ValueError, AttributeError):
        return period or "latest"


# --- live fetch (throttled; injected in tests) -----------------------------------------

_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
_ARCHIVE_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/index.json"
_ARCHIVE_FILE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{name}"


def _acc_nodash(accession: str) -> str:
    return accession.replace("-", "")


def fetch_submissions(cik: str | int, identity: str, *, timeout: float = 30.0,
                      throttle: Callable[[], None] | None = None,
                      _http_json: Callable | None = None) -> dict:
    """GET data.sec.gov/submissions/CIK{cik10}.json. One request/fund/session."""
    cik10 = f"{int(cik):010d}"
    if _http_json is not None:
        return _http_json(_SUBMISSIONS_URL.format(cik10=cik10), identity, timeout)
    import httpx
    if throttle is not None:
        throttle("thirteenf")
    with httpx.Client(timeout=timeout, headers={"User-Agent": identity}) as c:
        r = c.get(_SUBMISSIONS_URL.format(cik10=cik10))
        r.raise_for_status()
        return r.json()


def _find_infotable_name(index_json: dict) -> Optional[str]:
    """The information-table member of a filing directory: an `.xml` file that is neither the
    cover-page `primary_doc.xml` nor an `xslForm13F` rendered view. Returns the first such
    name (13F filings carry exactly one), or None."""
    items = ((index_json.get("directory") or {}).get("item")) or []
    for it in items:
        name = it.get("name") or ""
        low = name.lower()
        if low.endswith(".xml") and low != "primary_doc.xml" and "primary_doc" not in low \
                and not low.startswith("xslform13f"):
            return name
    return None


def fetch_infotable_rows(cik: str | int, accession: str, identity: str, *,
                         timeout: float = 30.0, throttle: Callable[[], None] | None = None,
                         _http_json: Callable | None = None,
                         _http_bytes: Callable | None = None) -> list[dict]:
    """Fetch a filing's index.json, locate the information-table XML, fetch + parse it.
    Two SEC requests. Returns [] when the infotable can't be located/parsed."""
    cik_i = int(cik)
    acc = _acc_nodash(accession)
    if _http_json is not None:
        index_json = _http_json(_ARCHIVE_INDEX_URL.format(cik=cik_i, acc=acc), identity, timeout)
    else:
        import httpx
        if throttle is not None:
            throttle("thirteenf")
        with httpx.Client(timeout=timeout, headers={"User-Agent": identity}) as c:
            r = c.get(_ARCHIVE_INDEX_URL.format(cik=cik_i, acc=acc))
            r.raise_for_status()
            index_json = r.json()
    name = _find_infotable_name(index_json)
    if not name:
        return []
    if _http_bytes is not None:
        raw = _http_bytes(_ARCHIVE_FILE_URL.format(cik=cik_i, acc=acc, name=name), identity, timeout)
    else:
        import httpx
        if throttle is not None:
            throttle("thirteenf")
        with httpx.Client(timeout=timeout, headers={"User-Agent": identity}) as c:
            r = c.get(_ARCHIVE_FILE_URL.format(cik=cik_i, acc=acc, name=name))
            r.raise_for_status()
            raw = r.content
    return parse_infotable(raw)

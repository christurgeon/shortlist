"""SEC DERA bulk Form 3/4/5 ingest -> InsiderTxn records + a per-insider trade-month index.

Quarterly ZIPs (~12.8 MB each) at sec.gov/files/structureddata/data/insider-transactions-
data-sets/. Publication lags a quarter, so this is the HISTORY side only; live detection
reads Form 4 XML (scout/insider.py). Both produce the same InsiderTxn from RAW fields.

Design: docs/FORM4_INSIDER.md
"""
from __future__ import annotations

import csv
import io
import json
import os
import urllib.request
import warnings
import zipfile
from datetime import date
from pathlib import Path

from ..env import redact_secrets
from .insider import InsiderTxn
from .sec_throttle import sec_throttle

_BASE = ("https://www.sec.gov/files/structureddata/data/"
         "insider-transactions-data-sets")

_UA = "shortlist-scout turgechr@duck.com"

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}

_TRUE = {"1", "true", "yes", "y"}


def dera_zip_url(quarter: str) -> str:
    """'2025q1' -> the quarterly Form 345 ZIP URL."""
    return f"{_BASE}/{quarter}_form345.zip"


def parse_dera_date(raw: str | None) -> date | None:
    """DERA dates are DD-MON-YYYY ('27-MAR-2025'), NOT ISO. None-safe."""
    s = (raw or "").strip().upper()
    parts = s.split("-")
    if len(parts) != 3 or parts[1] not in _MONTHS:
        return None
    try:
        return date(int(parts[2]), _MONTHS[parts[1]], int(parts[0]))
    except ValueError:
        return None


def _roles(raw: str | None) -> frozenset[str]:
    """'Director,Officer,TenPercentOwner' -> {'director','officer','tenpercent'}."""
    out = set()
    for part in (raw or "").split(","):
        p = part.strip().lower()
        if p == "director":
            out.add("director")
        elif p == "officer":
            out.add("officer")
        elif p in ("tenpercentowner", "tenpercent"):
            out.add("tenpercent")
    return frozenset(out)


def _num(raw: str | None) -> float | None:
    try:
        return float(raw) if (raw or "").strip() else None
    except ValueError:
        return None


def parse_dera_tsvs(sub_fh, owner_fh, trans_fh) -> list[InsiderTxn]:
    """The three DERA TSVs -> InsiderTxn records, matching parse_form4_xml exactly."""
    subs = {r["ACCESSION_NUMBER"]: r for r in csv.DictReader(sub_fh, delimiter="\t")
            if r.get("DOCUMENT_TYPE") == "4"}
    owners: dict[str, list[dict]] = {}
    for r in csv.DictReader(owner_fh, delimiter="\t"):
        owners.setdefault(r["ACCESSION_NUMBER"], []).append(r)

    out: list[InsiderTxn] = []
    for r in csv.DictReader(trans_fh, delimiter="\t"):
        s = subs.get(r["ACCESSION_NUMBER"])
        if not s:
            continue
        d = parse_dera_date(r.get("TRANS_DATE"))
        if d is None:
            continue
        os_ = owners.get(r["ACCESSION_NUMBER"], [])
        o = os_[0] if os_ else {}
        title = (o.get("RPTOWNER_TITLE") or "").strip() or None
        out.append(InsiderTxn(
            owner_cik=(o.get("RPTOWNERCIK") or "").strip(),
            ticker=(s.get("ISSUERTRADINGSYMBOL") or "").strip().upper(),
            date=d,
            code=(r.get("TRANS_CODE") or "").strip(),
            shares=_num(r.get("TRANS_SHARES")),
            price=_num(r.get("TRANS_PRICEPERSHARE")),
            plan_10b5_1=str(s.get("AFF10B5ONE") or "").strip().lower() in _TRUE,
            roles=_roles(o.get("RPTOWNER_RELATIONSHIP")),
            title=title,
            # >1 reporting owner: neither source joins a transaction to a PARTICULAR
            # owner, so any single attribution is a guess. Abstain (spec §5.1).
            joint_filing=len(os_) > 1,
            issuer_cik=(s.get("ISSUERCIK") or "").strip(),
        ))
    return out


def build_trade_month_index(txns) -> dict[str, set[tuple[int, int]]]:
    """owner_cik -> {(year, month)} they transacted in.

    Built from ALL transaction codes, deliberately. An insider who sells every March under a
    standing arrangement is ROUTINE -- precisely the noise the CMP filter strips. Indexing
    only purchases would classify such a trader as opportunistic and the filter would do
    nothing. (docs/FORM4_INSIDER.md §6)

    Keyed on a zero-padded CIK: owner_cik is the join key between this history index and the
    live Form 4 XML path (classify_tier). If the two sides ever disagreed on zero-padding,
    every lookup would miss and every insider would silently classify as "unclassified" --
    still emitted, just at reduced strength, so the failure would never surface as an error.
    Canonicalizing here (and in classify_tier) makes the join robust to that regardless of
    which representation a caller passes in.
    """
    idx: dict[str, set[tuple[int, int]]] = {}
    for t in txns:
        if not t.owner_cik or t.date is None:
            continue
        key = t.owner_cik.strip().zfill(10)
        idx.setdefault(key, set()).add((t.date.year, t.date.month))
    return idx


def quarters_back(as_of: date, n: int) -> list[str]:
    """The `n` quarters ending with the one before `as_of`'s, newest first ('2025q1').

    DERA publishes roughly a quarter in arrears, so the CURRENT quarter is normally absent
    and `ensure_quarters` skips it rather than failing (verified 2026-07-26: 2026q1 was
    published, 2026q2 was not).
    """
    y, q = as_of.year, (as_of.month - 1) // 3 + 1
    out = []
    for _ in range(n):
        q -= 1
        if q == 0:
            y, q = y - 1, 4
        out.append(f"{y}q{q}")
    return out


def ensure_quarters(quarters, cache_dir: str, identity: str = _UA) -> list[Path]:
    """Download each quarterly ZIP to `cache_dir` if absent; return the paths that exist.

    Cached FOREVER by filename -- a published quarter is immutable. A 404 means "not
    published yet" and is SKIPPED, never raised: a missing recent quarter must degrade the
    history, not abort the daily run.

    Writes are ATOMIC (PID-unique sibling temp file + os.replace, the positions.json
    pattern) -- I-3: a kill or disk-full mid-write must not leave a truncated `p` that
    `p.exists()` treats as "already downloaded" forever after (a corrupt ZIP `_index_from_zip`
    would then fail on, every run, with no self-healing retry).
    """
    d = Path(cache_dir)
    d.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    for q in quarters:
        p = d / f"{q}_form345.zip"
        if not p.exists():
            tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
            try:
                # www.sec.gov — draws on the shared budget. DERA is few requests but very
                # large ones, and a DERA 429 is the evidence the 2026-08-04 cascade was
                # diagnosed from, so it must be paced and counted with everything else.
                sec_throttle()("dera")
                req = urllib.request.Request(dera_zip_url(q), headers={"User-Agent": identity})
                with urllib.request.urlopen(req, timeout=120) as r:
                    tmp.write_bytes(r.read())
                # A 200 carrying an SEC block page or an edge-cache error body is NOT a
                # ZIP. Without this check it is cached forever, and _index_from_zip then
                # raises BadZipFile on every later run -- the same never-self-healing shape
                # as the C-1 cache-poisoning bug. Atomic write guards truncation, not
                # garbage-but-complete payloads.
                if not zipfile.is_zipfile(tmp):
                    warnings.warn(f"dera: {q} response was not a ZIP; discarding",
                                  stacklevel=2)
                    continue
                os.replace(tmp, p)
            except Exception as exc:  # noqa: BLE001 -- absent quarter degrades history
                warnings.warn(f"dera: {q} unavailable: {redact_secrets(str(exc))}",
                              stacklevel=2)
                continue
            finally:
                tmp.unlink(missing_ok=True)
        out.append(p)
    return out


def _index_from_zip(path: Path) -> list[InsiderTxn]:
    """Parse one quarterly ZIP straight from its member file handles -- never materializes
    the whole archive (or a whole-universe list of ZIPs) in memory at once."""
    with zipfile.ZipFile(path) as z, z.open("SUBMISSION.tsv") as s, \
         z.open("REPORTINGOWNER.tsv") as o, z.open("NONDERIV_TRANS.tsv") as t:
        return parse_dera_tsvs(
            io.TextIOWrapper(s, "utf-8", errors="replace"),
            io.TextIOWrapper(o, "utf-8", errors="replace"),
            io.TextIOWrapper(t, "utf-8", errors="replace"))


def load_index(cache_dir: str, quarters, identity: str = _UA) -> tuple[dict, int]:
    """Trade-month index across `quarters`, disk-cached as compact JSON.

    Returns (index, n_missing): `n_missing` counts requested quarters that did NOT
    contribute -- not yet published, or a download/parse failure (indistinguishable from
    here; both degrade the history the same way). Callers surface both numbers so an
    operator can tell "running on a thin history" from "the feature silently stopped."

    C-1 FIX (2026-07-27, REVISED 2026-07-30): the cache is written only when at least one
    quarter contributed (`if contributed`). The first revision required EVERY requested
    quarter, which was self-defeating: `quarters_back` yields the previous quarter first and
    DERA publishes ~a quarter in arrears, so `n_missing >= 1` in the steady state (a live run
    reported 15/16) and the cache therefore NEVER wrote -- rebuilding ~15 ZIPs at the ~288 MB
    peak every single night. Requiring `contributed` is enough, because the key is derived
    from the quarters that ACTUALLY contributed, so a late-arriving quarter mints its own
    key rather than reusing a thinner index. A prior version wrote the cache unconditionally, so a
    single network blip on the very first run after deploy persisted an EMPTY (or partial)
    index to disk FOREVER -- every later run hit that cache and never retried the failed
    download, silently classifying every insider UNCLASSIFIED (measured 48.5% of the
    population would otherwise be ROUTINE and correctly dropped). See the Task-5
    fix-round-2 report for the reproduction this guards against.

    The cache key is the SORTED list of quarters that actually CONTRIBUTED, not the
    requested list (I-5): a quarter that 404s today (DERA publishes ~a quarter in arrears,
    so the newest requested quarter is routinely absent for weeks) and shows up six weeks
    later gets its own cache entry once it does, instead of being pinned behind a
    requested-quarter key that never changes within the quarter. The tradeoff: while any
    requested quarter is missing, nothing is cached and every call rebuilds from the ZIPs
    that ARE present -- correctness over efficiency, and an under-complete cache file from
    an earlier session is simply orphaned (harmless; not cleaned up).

    Rebuilding a COMPLETE set from ~16 ZIPs on every daily run would be wasteful, which is
    why a complete index is persisted. Values are stored as `y*12+m` ints (far smaller than
    [y, m] pairs) and rehydrated to the (year, month) tuples `classify_tier` expects.

    Memory: quarters are processed ONE AT A TIME (`_index_from_zip` per path, merged into
    `idx` and discarded) -- the parsed InsiderTxn list for a quarter never coexists with
    another quarter's, and the ~900k-submission full-history case never materializes as one
    list. This runs on a 1.9 GB VPS alongside the live daily scout.
    """
    paths = ensure_quarters(quarters, cache_dir, identity)
    downloaded = sorted(p.name.removesuffix("_form345.zip") for p in paths)

    # Fast path: a cache keyed on exactly the quarters we have on disk.
    probe = Path(cache_dir) / f"index-{'-'.join(downloaded)}.json"
    if downloaded and probe.exists():
        try:
            raw = json.loads(probe.read_text())
            return ({k: {(v // 12, v % 12 + 1) for v in vs} for k, vs in raw.items()},
                    len(quarters) - len(downloaded))
        except (json.JSONDecodeError, OSError):
            probe.unlink(missing_ok=True)  # I-3: don't re-serve a corrupt cache forever

    # `contributed` is built from quarters that actually PARSED, not merely downloaded -- a
    # corrupt archive must not be encoded into the cache key as though it had contributed.
    idx: dict[str, set] = {}
    contributed: list[str] = []
    for p in paths:
        try:
            quarter_idx = build_trade_month_index(_index_from_zip(p))
        except Exception as exc:  # noqa: BLE001 -- one corrupt archive degrades ONE quarter
            # Unlink so the next run re-downloads rather than failing identically forever.
            warnings.warn(f"dera: {p.name} unreadable, discarding: "
                          f"{redact_secrets(str(exc))}", stacklevel=2)
            p.unlink(missing_ok=True)
            continue
        contributed.append(p.name.removesuffix("_form345.zip"))
        for cik, months in quarter_idx.items():
            idx.setdefault(cik, set()).update(months)

    contributed.sort()
    n_missing = len(quarters) - len(contributed)
    cache = Path(cache_dir) / f"index-{'-'.join(contributed)}.json"

    if contributed:
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        tmp = cache.with_name(f"{cache.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(json.dumps(
                {k: sorted(y * 12 + (m - 1) for (y, m) in vs) for k, vs in idx.items()}))
            os.replace(tmp, cache)
        finally:
            tmp.unlink(missing_ok=True)
    return idx, n_missing

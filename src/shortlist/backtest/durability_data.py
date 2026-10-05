"""Bulk SEC archives -> the small files the moat-durability study reads. Stdlib only.

`companyfacts.zip` is 1.41 GB and `submissions.zip` 1.57 GB (2026-10-04). Both are read member
by member and NEVER extracted: extracted they are well over 18 GB, and the machine that ran
the study had 9.4 GB free."""
from __future__ import annotations

import gzip
import json
import zipfile
import zlib
from pathlib import Path
from typing import Iterator, Optional

from ..durability import CUR_DEBT, LT_NONCURRENT, LT_TOTAL
from ..providers._xbrl_facts import ASSETS, COGS, EQUITY, GROSS_PROFIT, OP_INCOME, REVENUE

KEEP_TAGS = tuple(REVENUE + OP_INCOME + EQUITY + ASSETS + LT_NONCURRENT + LT_TOTAL + CUR_DEBT
                  + GROSS_PROFIT + COGS)
# 20-F is in _xbrl_facts._ANNUAL_FORMS; the study is 10-K filers only, so it is dropped HERE.
KEEP_FORMS = frozenset({"10-K", "10-K/A"})
_FIELDS = ("start", "end", "val", "filed", "form")
_NEEDS_ONE_OF = tuple(REVENUE + OP_INCOME)


def compact_facts(raw: dict) -> Optional[dict]:
    """The us-gaap USD facts the study needs, 10-K forms only, or None when the filer has no
    annual revenue or operating-income fact at all."""
    gaap = (raw.get("facts") or {}).get("us-gaap") or {}
    out: dict[str, dict] = {}
    for tag in KEEP_TAGS:
        rows = ((gaap.get(tag) or {}).get("units") or {}).get("USD") or []
        kept = [{k: f[k] for k in _FIELDS if k in f} for f in rows if f.get("form") in KEEP_FORMS]
        if kept:
            out[tag] = {"units": {"USD": kept}}
    if not any(t in out for t in _NEEDS_ONE_OF):
        return None
    return {"facts": {"us-gaap": out}}


def _cik_of(member: str) -> Optional[str]:
    """'CIK0000320193.json' -> '0000320193'. Paging files ('CIK…-submissions-001.json') and
    anything else return None."""
    name = member.rsplit("/", 1)[-1]
    stem = name[3:-5]
    if name.startswith("CIK") and name.endswith(".json") and len(stem) == 10 and stem.isdigit():
        return stem
    return None


def require_zip(path: str | Path) -> None:
    """Raise ValueError — and delete the file — unless `path` is a ZIP. A 200 carrying an SEC
    block page or an edge-cache error body is complete and is not a ZIP; cached, it would fail
    every later run."""
    p = Path(path)
    if not zipfile.is_zipfile(p):
        p.unlink(missing_ok=True)
        raise ValueError(f"{p.name} is not a ZIP archive")


# What reading one member can raise when the member, not the archive, is bad: invalid JSON or
# text (ValueError), a failed CRC (BadZipFile), a broken or truncated deflate stream.
_BAD_MEMBER = (ValueError, zipfile.BadZipFile, zlib.error, EOFError)


def compact_companyfacts_zip(zip_path: str | Path, out_path: str | Path,
                             unreadable: Optional[list[str]] = None) -> int:
    """Write one gzip JSONL line per kept filer: {"cik", "name", "facts"}. Returns the count.
    A member that cannot be read is skipped — one bad file must not sink a 1.4 GB pass — and
    its name is appended to `unreadable`, because a skipped member is a filer missing from the
    study.

    Written beside `out_path` and renamed on success. A pass that fails halfway would otherwise
    leave a partial file, and a partial gzip stream reads back as a valid, shorter one."""
    out_path = Path(out_path)
    tmp = out_path.with_name(out_path.name + ".tmp")
    n = 0
    try:
        with zipfile.ZipFile(zip_path) as z, gzip.open(tmp, "wt", encoding="utf-8") as out:
            for member in z.namelist():
                cik = _cik_of(member)
                if cik is None:
                    continue
                try:
                    raw = json.loads(z.read(member))
                except _BAD_MEMBER:
                    if unreadable is not None:
                        unreadable.append(member)
                    continue
                kept = compact_facts(raw) if isinstance(raw, dict) else None
                if kept is None:
                    continue
                kept["cik"] = cik
                kept["name"] = raw.get("entityName")
                out.write(json.dumps(kept, separators=(",", ":")) + "\n")
                n += 1
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(out_path)
    return n


def iter_compacted(path: str | Path) -> Iterator[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def sic_from_submissions_zip(zip_path: str | Path, ciks: set[str]) -> dict[str, str]:
    """{cik -> SIC} for the wanted CIKs. CURRENT SIC, not point-in-time: a dead filer's is
    frozen at its last filing, a survivor's can have drifted."""
    out: dict[str, str] = {}
    with zipfile.ZipFile(zip_path) as z:
        for member in z.namelist():
            cik = _cik_of(member)
            if cik is None or cik not in ciks:
                continue
            try:
                sic = json.loads(z.read(member)).get("sic")
            except (*_BAD_MEMBER, AttributeError):
                continue
            if sic:
                out[cik] = str(sic)
    return out

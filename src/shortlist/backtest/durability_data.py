"""Bulk SEC archives -> the small files the moat-durability study reads. Stdlib only.

`companyfacts.zip` is 1.41 GB and `submissions.zip` 1.57 GB (2026-10-04). Both are read member
by member and NEVER extracted: extracted they are well over 18 GB, and the machine that ran
the study had 9.4 GB free."""
from __future__ import annotations

import gzip
import io
import json
import zipfile
import zlib
from pathlib import Path
from typing import Iterator, Optional

from ..durability import _kept, compact_facts

# The annual forms of foreign issuers. NOT IN THE STUDY. They are kept apart, under their own
# key, for the reproduction gate alone: SEC frames counts these filers, so the count that is
# compared with the frames targets has to count them too.
FOREIGN_ANNUAL_FORMS = frozenset({"20-F", "20-F/A", "40-F", "40-F/A"})


def foreign_annual_facts(raw: dict) -> Optional[dict]:
    """The same facts from the foreign annual forms, each row relabelled 10-K so that
    `annual_series` reads 40-F as it reads 20-F. None when there is no such revenue or
    operating-income fact."""
    kept = _kept(raw, FOREIGN_ANNUAL_FORMS)
    if kept is not None:
        for node in kept["facts"]["us-gaap"].values():
            for row in node["units"]["USD"]:
                row["form"] = "10-K"
    return kept


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
    """Write one gzip JSONL line per kept filer: {"cik", "name", "facts"} and, for a filer with
    foreign annual forms, "foreign_forms". Returns the count. `facts` is what the study reads;
    a filer on foreign forms only has an empty one and is no firm to the study.

    A member that cannot be read is skipped — one bad file must not sink a 1.4 GB pass — and
    its name is appended to `unreadable`, because a skipped member is a filer missing from the
    study.

    THE SAME ARCHIVE GIVES THE SAME BYTES. The SHA-256 of the output binds the gates to the
    data, so the gzip header carries no timestamp and no file name.

    Written beside `out_path` and renamed on success. A pass that fails halfway would otherwise
    leave a partial file, and a partial gzip stream reads back as a valid, shorter one."""
    out_path = Path(out_path)
    tmp = out_path.with_name(out_path.name + ".tmp")
    n = 0
    try:
        with zipfile.ZipFile(zip_path) as z, tmp.open("wb") as fh, \
                gzip.GzipFile(filename="", mode="wb", fileobj=fh, mtime=0) as gz, \
                io.TextIOWrapper(gz, encoding="utf-8") as out:
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
                if not isinstance(raw, dict):
                    continue
                kept, foreign = compact_facts(raw), foreign_annual_facts(raw)
                if kept is None and foreign is None:
                    continue
                rec = kept or {"facts": {"us-gaap": {}}}
                rec["cik"] = cik
                rec["name"] = raw.get("entityName")
                if foreign is not None:
                    rec["foreign_forms"] = foreign
                out.write(json.dumps(rec, separators=(",", ":")) + "\n")
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

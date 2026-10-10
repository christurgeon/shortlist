"""Moat-durability study — did a refactor of the shared code move anything?

Phase 1 moves the form filter, the compaction and the per-firm predictor arithmetic from the
study modules into `shortlist/durability.py`, so that the `/deep` section is computed on the
measured basis. That changes the code digest the raw outputs name. This script is the evidence
that it changes nothing else: SHA-256 digests of every row the study reads and builds, from the
COMMITTED data file. It was committed and run BEFORE the refactor
(`raw-2026-10-04-durability/equivalence.json`); `tests/test_durability_equivalence.py`
recomputes the digests on the current code and compares.

    uv run python docs/audits/scripts/probe_durability_equivalence.py            # print
    uv run python docs/audits/scripts/probe_durability_equivalence.py --write    # clean tree only
    uv run python docs/audits/scripts/probe_durability_equivalence.py --write-portable

THE DIGESTS OF `equivalence.json` ARE EXACT ONLY WHERE THEY WERE WRITTEN (macOS, arm64). One
digested value, `share_stability`, is a `math.log`, and the C library's `log` is not the same
on every platform: under glibc 10 of its 2,906 cohort values differ from Apple's by one unit
in the last place (no rank differs, and no other field). `portable_digests` hashes that one
value to 9 significant digits and everything else exactly as `digests` does;
`equivalence-portable.json` holds them, and the test compares them on every platform.
`--write-portable` writes that file only from rows whose exact digests are the committed ones.

NOT A STUDY STEP. No beta is computed and no gate is read.

IT USES ONLY NAMES THAT EXIST ON BOTH SIDES OF THE REFACTOR (`build_firm`, `cross_section`,
`build_cohort`, `_predictors`, `_peer_totals`, `quintile_floor`, and the fields of `Row` and
`YearRow` by name), so the same text runs at both commits. Keep it that way.

WHAT IT CANNOT SHOW: that the SEC's per-CIK API returns the same rows, in the same order, as
the bulk archive this file was compacted from. `tests/test_durability_live.py` checks that."""
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

from shortlist.backtest import durability_study as ds
from shortlist.backtest.durability_data import iter_compacted
from shortlist.config import load_config
from shortlist.sectors import resolve_bucket

ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "docs/audits/raw-2026-10-04-durability"
COMPACT = RAW / "companyfacts-10k.jsonl.gz"
SIC = RAW / "sic.json"
OUT = RAW / "equivalence.json"
PORTABLE_OUT = RAW / "equivalence-portable.json"
# The study's snapshot years, plus 2025: the reference year of the `/deep` table.
YEARS = range(2011, 2026)
START_YEARS = range(2011, 2022)
TABLE_YEAR = 2025
_ROW_FIELDS = ("end", "revenue", "op_income", "equity", "debt", "assets", "gross_profit")
# The predictors computed through the C math library, and the digits of them that
# `portable_digests` hashes. The measured difference between two libraries is one unit in the
# last place, the 16th or 17th significant digit. WHY 9 AND NOT MORE: the digest must not
# depend on which way one value rounds. At 12 digits one cohort value is a single unit in the
# last place from a value that prints differently, and the digest was the same under two
# libraries by luck. At 9 the nearest of the 3,222 values is 261 units away (10: 14; 11: 5).
LIBM_PREDICTORS = ("share_stability",)
PORTABLE_DIGITS = 9


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def load_firms(years=YEARS) -> list:
    """The study's firms, as `probe_durability.load_firms` builds them, from the committed
    file in place."""
    config = load_config(str(ROOT / "config.yaml"))
    sic = json.loads(SIC.read_text())
    firms = []
    for rec in iter_compacted(COMPACT):
        code = sic.get(rec["cik"])
        firm = ds.build_firm(rec["cik"], code, resolve_bucket(code, config) != "unknown",
                             rec, years)
        if firm is not None:
            firms.append(firm)
    return firms


class _Digest:
    def __init__(self) -> None:
        self._h = hashlib.sha256()
        self.n = 0

    def add(self, *parts) -> None:
        self._h.update(("|".join(repr(p) for p in parts) + "\n").encode())
        self.n += 1

    def hexdigest(self) -> str:
        return self._h.hexdigest()


def _year_rows(firms) -> _Digest:
    d = _Digest()
    for f in sorted(firms, key=lambda f: f.cik):
        d.add("firm", f.cik, f.sic, f.masked, f.last_bucket)
        for snap_year in sorted(f.snaps):
            for bucket in sorted(f.snaps[snap_year]):
                row = f.snaps[snap_year][bucket]
                d.add(f.cik, snap_year, bucket, *(getattr(row, k) for k in _ROW_FIELDS))
    return d


def portable(value: float) -> str:
    return f"{value:.{PORTABLE_DIGITS}g}"


def _preds(preds: dict, as_portable: bool) -> list:
    if not as_portable:
        return sorted(preds.items())
    return sorted((k, portable(v) if k in LIBM_PREDICTORS and v is not None else v)
                  for k, v in preds.items())


def libm_values(firms) -> list:
    """Every value the portable digests round: the cohort rows' and the table inputs'."""
    rows = [r.preds for year in START_YEARS for r in ds.build_cohort(firms, year)]
    rows += list(table_inputs(firms)["members"].values())
    return [p[k] for p in rows for k in LIBM_PREDICTORS if p[k] is not None]


def _cohort_rows(firms, as_portable: bool = False) -> _Digest:
    d = _Digest()
    for year in START_YEARS:
        for r in ds.build_cohort(firms, year):
            d.add(r.cik, r.year, r.sic2, r.sic3, r.roic, r.revenue, _preds(r.preds, as_portable),
                  r.state, r.held, r.rank_t3, r.rev_ratio, r.compounded, r.c0, r.c2,
                  sorted(r.p.items()))
    return d


def table_inputs(firms) -> dict:
    """What the `/deep` table is built from: the ROIC universe of each history bucket as seen
    in the TABLE_YEAR snapshot, its top-fifth floor, and each top-fifth member's predictors."""
    hist = {y: sorted(ds.cross_section(firms, TABLE_YEAR, y).values())
            for y in range(TABLE_YEAR - ds.HISTORY + 1, TABLE_YEAR + 1)}
    floors = {y: ds.quintile_floor(v) for y, v in hist.items()}
    now_xs = ds.cross_section(firms, TABLE_YEAR, TABLE_YEAR)
    peers = ds._peer_totals(firms, TABLE_YEAR)
    members = {f.cik: ds._predictors(f, TABLE_YEAR, hist, floors, peers)
               for f in firms if now_xs.get(f.cik, float("-inf")) >= floors[TABLE_YEAR]}
    return {"hist": hist, "floors": floors, "members": members}


def _table_digest(firms, as_portable: bool = False) -> _Digest:
    t = table_inputs(firms)
    d = _Digest()
    for y in sorted(t["hist"]):
        d.add("hist", y, t["floors"][y], t["hist"][y])
    for cik in sorted(t["members"]):
        d.add("member", cik, _preds(t["members"][cik], as_portable))
    return d


def digests(firms) -> dict:
    yr, co, tb = _year_rows(firms), _cohort_rows(firms), _table_digest(firms)
    return {
        "year_rows": {"sha256": yr.hexdigest(), "n": yr.n},
        "cohort_rows": {"sha256": co.hexdigest(), "n": co.n},
        "table_inputs": {"sha256": tb.hexdigest(), "n": tb.n},
        "universe_sizes": {str(y): len(ds.cross_section(firms, y, y)) for y in YEARS},
    }


def portable_digests(firms) -> dict:
    """The two digests that reach a `LIBM_PREDICTORS` value, in a form that is the same on
    every platform. `year_rows` and `universe_sizes` need none: they hold no such value."""
    co, tb = _cohort_rows(firms, as_portable=True), _table_digest(firms, as_portable=True)
    return {
        "cohort_rows": {"sha256": co.hexdigest(), "n": co.n},
        "table_inputs": {"sha256": tb.hexdigest(), "n": tb.n},
        "libm_predictors": list(LIBM_PREDICTORS),
        "significant_digits": PORTABLE_DIGITS,
    }


def exact_platform() -> bool:
    """True where `equivalence.json` was written, so where its digests are exact."""
    return sys.platform == "darwin" and platform.machine() == "arm64"


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout.strip()


def main() -> None:
    write = "--write" in sys.argv[1:]
    if write and _git("status", "--porcelain"):
        raise SystemExit("uncommitted changes. The digests must name the commit that made them.")
    out = {"code_commit": _git("rev-parse", "HEAD"), "compacted_sha256": _sha256(COMPACT),
           "sic_sha256": _sha256(SIC)}
    firms = load_firms()
    out["n_firms"] = len(firms)
    if "--write-portable" in sys.argv[1:]:
        # No commit is named. What ties this file to the code before the refactor is the check
        # below: it is written only from rows whose exact digests are the committed ones.
        committed, now = json.loads(OUT.read_text()), digests(firms)
        if not exact_platform() or any(now[k] != committed[k] for k in now):
            raise SystemExit("the exact digests are not those of equivalence.json, or this is "
                             "not the platform that wrote it. Nothing is written.")
        del out["code_commit"]
        out.update(portable_digests(firms), written_on=platform.platform())
        PORTABLE_OUT.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
        return
    out.update(digests(firms))
    text = json.dumps(out, indent=1, sort_keys=True) + "\n"
    if write:
        OUT.write_text(text)
    print(text, end="")


if __name__ == "__main__":
    main()

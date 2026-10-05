"""Moat-durability study — the Phase 0 measurement.

Evidence for docs/audits/2026-10-04-moat-durability-verdict.md. Definitions and the pass rule
are fixed by docs/audits/2026-10-04-moat-durability-prereg.md; this script only does I/O and
calls shortlist.backtest.durability_study. Run from the repo root, in this order:

    set -a && . ./.env && set +a
    uv run python docs/audits/scripts/probe_durability.py fetch       # 2 requests, ~3 GB transient
    uv run python docs/audits/scripts/probe_durability.py gates
    uv run python docs/audits/scripts/probe_durability.py discovery
    #   ... write the discovery section of the verdict note, COMMIT it and discovery.json ...
    uv run python docs/audits/scripts/probe_durability.py holdout

`discovery` refuses to run until the pre-registration is committed and `gates` has passed.
`holdout` refuses to run until discovery.json is committed and unmodified. Both checks read
git, so an uncommitted edit cannot pass as pre-registered. There is no flag to skip them.

Disk: the two SEC archives are 1.41 GB and 1.57 GB. Each is compacted and deleted before the
next is fetched; neither is extracted.
"""
import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from shortlist.backtest import durability_study as ds
from shortlist.backtest.durability_data import (
    compact_companyfacts_zip,
    iter_compacted,
    require_zip,
    sic_from_submissions_zip,
)
from shortlist.config import load_config
from shortlist.edgar.sec_throttle import sec_throttle
from shortlist.env import load_env, redact_secrets
from shortlist.sectors import resolve_bucket

FACTS_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
SUBS_URL = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"
CACHE = Path(".cache/durability")
RAW = Path("docs/audits/raw-2026-10-04-durability")
PREREG = Path("docs/audits/2026-10-04-moat-durability-prereg.md")
COMPACT = CACHE / "companyfacts-10k.jsonl.gz"
YEARS = range(2011, 2025)        # snapshot years: start years 2011-2021 plus their outcomes

# Filers that stopped filing, with the last bucket they must reach. Verified present in
# per-CIK companyfacts on 2026-10-04. The last entry is the CIK-successor case (Google ->
# Alphabet): the old CIK must read as an exit, which is why it is in this list.
DEAD_FILERS = {"0000791907": ("Linear Technology", 2016), "0001110783": ("Monsanto", 2017),
               "0000816284": ("Celgene", 2018), "0001105705": ("Time Warner", 2017),
               "0001137411": ("Rockwell Collins", 2018), "0001087423": ("Red Hat", 2018),
               "0000743988": ("Xilinx", 2020), "0001271024": ("LinkedIn", 2015),
               "0001288776": ("Google (old CIK)", 2014)}
ENERGY_MINING_SIC2 = {"10", "12", "13", "14", "29"}


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _download(url: str, dest: Path) -> None:
    identity = os.environ.get("SEC_IDENTITY")
    if not identity:
        raise SystemExit("SEC_IDENTITY (a contact email) is required by the SEC")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    sec_throttle()("durability-bulk")           # the one process-wide sec.gov budget
    try:
        req = urllib.request.Request(url, headers={"User-Agent": identity})
        with urllib.request.urlopen(req, timeout=300) as r, tmp.open("wb") as out:
            while chunk := r.read(1 << 20):
                out.write(chunk)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"download failed: {redact_secrets(str(e))}") from None
    try:
        require_zip(tmp)
    except ValueError as e:
        raise SystemExit(f"{url}: {e}") from None
    tmp.replace(dest)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _write(name: str, payload: dict) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    payload = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "compacted_sha256": _sha256(COMPACT), **payload}
    (RAW / name).write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    _log(f"wrote {RAW / name}")


def _committed(path: Path) -> bool:
    """True when `path` is tracked AND has no uncommitted change."""
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", str(path)],
                             capture_output=True).returncode == 0
    dirty = subprocess.run(["git", "status", "--porcelain", "--", str(path)],
                           capture_output=True, text=True).stdout.strip()
    return tracked and not dirty


def fetch() -> None:
    zp = CACHE / "companyfacts.zip"
    _download(FACTS_URL, zp)
    n = compact_companyfacts_zip(zp, COMPACT)
    if n == 0:
        # The member layout ('CIK##########.json') is ASSUMED from the per-CIK API; this run
        # is its first test. Keep the archive so the layout can be read without a re-download.
        raise SystemExit(f"compacted 0 filers: the archive layout is not what "
                         f"durability_data._cik_of expects. Archive kept at {zp}.")
    zp.unlink()
    _log(f"compacted {n} filers -> {COMPACT}")
    ciks = {rec["cik"] for rec in iter_compacted(COMPACT)}
    zp = CACHE / "submissions.zip"
    _download(SUBS_URL, zp)
    sic = sic_from_submissions_zip(zp, ciks)
    zp.unlink()
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / "sic.json").write_text(json.dumps(sic, indent=0, sort_keys=True) + "\n")
    _log(f"SIC for {len(sic)} of {len(ciks)} filers -> {RAW / 'sic.json'}")


def load_firms() -> list[ds.Firm]:
    config = load_config("config.yaml")
    sic = json.loads((RAW / "sic.json").read_text())
    firms = []
    for i, rec in enumerate(iter_compacted(COMPACT)):
        code = sic.get(rec["cik"])
        firm = ds.build_firm(rec["cik"], code, resolve_bucket(code, config) != "unknown",
                             rec, YEARS)
        if firm is not None:
            firms.append(firm)
        if i % 1000 == 999:
            _log(f"  {i + 1} filers read")
    return firms


def cohorts(firms: list[ds.Firm], window: range) -> list[ds.Row]:
    return [row for year in window for row in ds.build_cohort(firms, year)]


def gates() -> None:
    firms = load_firms()
    by_cik = {f.cik: f for f in firms}
    dead = {cik: {"name": name, "need": need,
                  "got": by_cik[cik].last_bucket if cik in by_cik else None}
            for cik, (name, need) in DEAD_FILERS.items()}
    sizes = {y: len(ds.cross_section(firms, y, y)) for y in ds.FRAMES_UNIVERSE}
    disc = cohorts(firms, ds.DISCOVERY)
    every = disc + cohorts(firms, ds.HOLDOUT)
    gap = {}
    for y in sorted({r.year for r in every}):
        rows = [r for r in every if r.year == y]
        gap[y + ds.HORIZON] = sum(r.state == "gap" for r in rows) / len(rows)
    slope = ds.level_slope(ds.sample(disc, None, "held"))
    result = {
        "completeness": dead,
        "completeness_ok": all(d["got"] is not None and d["got"] >= d["need"]
                               for d in dead.values()),
        "universe_sizes": sizes,
        "reproduction_failures": ds.reproduction_failures(sizes),
        "level_slope_discovery": slope,
        "gap_rate_by_outcome_year": gap,
        "gap_spikes": ds.gap_spikes(gap),
        "discovery_cohort_n_by_year": {y: sum(r.year == y for r in disc) for y in ds.DISCOVERY},
        "discovery_state_shares": ds.state_shares(disc),
        "sectors_config_sha256": hashlib.sha256(json.dumps(
            load_config("config.yaml")["sectors"]["buckets"], sort_keys=True).encode()).hexdigest(),
    }
    result["passed"] = bool(result["completeness_ok"] and not result["reproduction_failures"]
                            and slope > 0 and not result["gap_spikes"])
    _write("gates.json", result)
    if not result["passed"]:
        raise SystemExit("GATES FAILED — no verdict. Diagnose first (prereg §5.6).")


def _run(rows: list[ds.Row], *, with_bounds: bool) -> dict:
    out = {}
    for pred, outcome in ds.TESTS:
        _log(f"  {pred} / {outcome}")
        m = ds.measure(rows, pred, outcome, with_bounds=with_bounds)
        m["exit_rate_by_tercile"] = ds.exit_rate_by_tercile(rows, pred)
        try:        # reported, never decision-bearing: SIC-2 cells do not hold a sub-industry fixed
            m["beta_sic3_cells"] = ds.fit(ds.sample(rows, pred, outcome), pred, digits=3)
        except (ValueError, ZeroDivisionError):
            m["beta_sic3_cells"] = None
        m["beta_by_year"] = {}
        for y in sorted({r.year for r in rows}):
            try:
                m["beta_by_year"][y] = ds.fit(
                    ds.sample([r for r in rows if r.year == y], pred, outcome), pred)
            except (ValueError, ZeroDivisionError):
                m["beta_by_year"][y] = None
        out[f"{pred}/{outcome}"] = m
    return out


def _descriptive(rows: list[ds.Row]) -> dict:
    """Reported, never decision-bearing (prereg §5.9)."""
    no_energy = [r for r in rows if r.sic2 not in ENERGY_MINING_SIC2]
    def beta(sub, p, o):
        try:
            return ds.fit(ds.sample(sub, p, o), p)
        except (ValueError, ZeroDivisionError):
            return None

    return {
        "ex_energy_mining": {f"{p}/{o}": beta(no_energy, p, o) for p, o in ds.TESTS},
        "investment_on_compounded": beta(rows, "investment", "compounded"),
        "state_shares": ds.state_shares(rows),
        "hold_rate": {o: (lambda s: sum(y for _, y in s) / len(s))(ds.sample(rows, None, o))
                      for o in ("held", "compounded")},
    }


def discovery() -> None:
    if not _committed(PREREG):
        raise SystemExit(f"{PREREG} is not committed (or has uncommitted edits). "
                         "Pre-register first.")
    gate_file = RAW / "gates.json"
    if not gate_file.exists() or not json.loads(gate_file.read_text()).get("passed"):
        raise SystemExit("gates.json is missing or did not pass. Run `gates` first.")
    rows = cohorts(load_firms(), ds.DISCOVERY)
    tests = _run(rows, with_bounds=True)
    for m in tests.values():
        m["rules"] = (dict.fromkeys(("magnitude", "bounds", "continuous_sign"), False)
                      if "error" in m else
                      ds.discovery_rules(m["beta"], m["se"], m["bound_held"], m["bound_not"],
                                         m["rank_beta"]))
    _write("discovery.json", {"window": [ds.DISCOVERY.start, ds.DISCOVERY.stop - 1],
                              "rows": len(rows), "tests": tests,
                              "descriptive": _descriptive(rows)})


def holdout() -> None:
    disc_file = RAW / "discovery.json"
    if not _committed(disc_file):
        raise SystemExit(f"{disc_file} is not committed (or was modified). Write the discovery "
                         "section of the verdict note and commit both before opening the holdout.")
    disc = json.loads(disc_file.read_text())["tests"]
    firms = load_firms()
    seen = {r.cik for r in cohorts(firms, ds.DISCOVERY)}
    rows = cohorts(firms, ds.HOLDOUT)
    tests = _run(rows, with_bounds=False)
    fresh = [r for r in rows if r.cik not in seen]
    for key, m in tests.items():
        pred, outcome = key.split("/")
        m["holdout_rule"] = "error" not in m and ds.holdout_rule(m["beta"], m["se"])
        m["passes"] = ds.passes(disc[key]["rules"], m["holdout_rule"])
        samp = ds.sample(fresh, pred, outcome)
        try:
            m["new_firms_only"] = {"n": len(samp), "beta": ds.fit(samp, pred)}
        except (ValueError, ZeroDivisionError):
            m["new_firms_only"] = {"n": len(samp), "beta": None}
    _write("holdout.json", {"window": [ds.HOLDOUT.start, ds.HOLDOUT.stop - 1],
                            "rows": len(rows), "tests": tests,
                            "descriptive": _descriptive(rows),
                            "passed": sorted(k for k, m in tests.items() if m["passes"])})


if __name__ == "__main__":
    load_env()
    steps = {"fetch": fetch, "gates": gates, "discovery": discovery, "holdout": holdout}
    if len(sys.argv) != 2 or sys.argv[1] not in steps:
        raise SystemExit(f"usage: probe_durability.py {{{'|'.join(steps)}}}")
    steps[sys.argv[1]]()

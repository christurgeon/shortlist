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
`holdout` refuses to run until discovery.json and the discovery section of the verdict note are
committed and unmodified. Both checks read git, so an uncommitted edit cannot pass as
pre-registered. There is no flag to skip them.

A STEP IS BOUND TO ITS DATA AND ITS CODE. Every output records the SHA-256 of the compacted
file and of the SIC map, the commit, and a digest of the files in `CODE`. No step runs while one
of those files differs from its committed version, and `discovery` / `holdout` refuse a
gates.json / discovery.json written from other data or other code: after any change, re-run
from `gates`.

A RESULT THAT WAS SEEN STAYS ON RECORD. `gates`, `discovery` and `holdout` refuse to run while
their own output from an earlier run is not committed, and `discovery` needs gates.json
committed. A failed gate is therefore in git before the run that follows the diagnosis, unless
the file is deleted by hand.

Disk: the two SEC archives are 1.41 GB and 1.57 GB, and neither is extracted. Both are kept
until the gates pass, because a failed gate is diagnosed against them, and `fetch` reads a kept
archive again without a new download (a run that follows a diagnosis needs the same data, not
the data of a later day; delete a file to force its download). About 3.5 GB until then.
"""
import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from shortlist.backtest import durability_study as ds
from shortlist.backtest.durability_data import (
    compact_companyfacts_zip,
    iter_compacted,
    require_zip,
    sic_from_submissions_zip,
)
from shortlist.config import load_config
from shortlist.durability import count_filed_before_period_end
from shortlist.edgar.sec_throttle import sec_throttle
from shortlist.env import load_env, redact_secrets
from shortlist.sectors import resolve_bucket

FACTS_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
SUBS_URL = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"
CACHE = Path(".cache/durability")
RAW = Path("docs/audits/raw-2026-10-04-durability")
PREREG = Path("docs/audits/2026-10-04-moat-durability-prereg.md")
VERDICT = Path("docs/audits/2026-10-04-moat-durability-verdict.md")
# The files whose text decides a number: this script, the three study modules and what they
# import. Paths from the repo root. tests/test_probe_durability.py walks the imports and fails
# when one is missing here.
CODE = ("docs/audits/scripts/probe_durability.py", "src/shortlist/durability.py",
        "src/shortlist/backtest/durability_data.py", "src/shortlist/backtest/durability_study.py",
        "src/shortlist/backtest/_ols.py", "src/shortlist/providers/_xbrl_facts.py",
        "src/shortlist/providers/_gaap_tags.py", "src/shortlist/stats.py",
        "src/shortlist/sectors.py", "src/shortlist/config.py", "config.yaml")
COMPACT = CACHE / "companyfacts-10k.jsonl.gz"
FACTS_ZIP = CACHE / "companyfacts.zip"
SUBS_ZIP = CACHE / "submissions.zip"
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


def _code_state() -> dict:
    """The commit and a digest of `CODE`. Refuses unless every file is byte-identical to its
    committed version: an output must be reproducible from the commit it names. The bytes are
    compared with HEAD directly, so an index flag that hides an edit from `git status` does
    not hide it here."""
    h, changed = hashlib.sha256(), []
    for rel in CODE:
        blob = subprocess.run(["git", "show", f"HEAD:{rel}"], capture_output=True)
        if blob.returncode != 0 or not Path(rel).exists() or Path(rel).read_bytes() != blob.stdout:
            changed.append(rel)
        h.update(rel.encode() + b"\0" + blob.stdout)
    if changed:
        raise SystemExit("uncommitted changes in the study's code. Commit them first:\n  "
                         + "\n  ".join(changed))
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
    return {"code_commit": head.stdout.strip(), "code_sha256": h.hexdigest(),
            "python_version": ".".join(map(str, sys.version_info[:3]))}


def _data_state() -> dict:
    for path in (COMPACT, RAW / "sic.json"):
        if not path.exists():
            raise SystemExit(f"{path} is missing. Run `fetch` first.")
    return {"compacted_sha256": _sha256(COMPACT), "sic_sha256": _sha256(RAW / "sic.json")}


def _inputs() -> dict:
    """What a step runs on. Read ONCE, when the step starts: the modules are already imported,
    so a commit made during a long run must not be the one the output names."""
    code = _code_state()
    return {**_data_state(), **code}


def _write(name: str, payload: dict, inputs: dict) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    payload = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               **inputs, **payload}
    (RAW / name).write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    _log(f"wrote {RAW / name}")


def _require_same_inputs(prior: dict, inputs: dict, name: str) -> None:
    """A pass counts only for the data files and the code it was computed from."""
    for key in ("compacted_sha256", "sic_sha256", "code_sha256"):
        if prior.get(key) != inputs[key]:
            raise SystemExit(f"{name} was written from a different {key}. Re-run from `gates`.")


def _keep_on_record(name: str) -> None:
    """Refuse to replace an output that is not in git. A second run must not erase the numbers
    of the first: a fix that follows a seen number has to show the number it followed."""
    if (RAW / name).exists() and not _committed(RAW / name):
        raise SystemExit(f"{RAW / name} holds a result that is not committed. Commit it before "
                         "this step runs again.")


def _committed(path: Path) -> bool:
    """True when `path` is tracked AND has no uncommitted change."""
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", str(path)],
                             capture_output=True).returncode == 0
    dirty = subprocess.run(["git", "status", "--porcelain", "--", str(path)],
                           capture_output=True, text=True).stdout.strip()
    return tracked and not dirty


def _archive(url: str, dest: Path) -> None:
    if dest.exists():
        _log(f"reading the kept {dest} again (delete it to force a new download)")
    else:
        _download(url, dest)


def fetch() -> None:
    code = _code_state()                        # refuse before the download, not after it
    _archive(FACTS_URL, FACTS_ZIP)
    unreadable: list[str] = []
    n = compact_companyfacts_zip(FACTS_ZIP, COMPACT, unreadable)
    if n == 0:
        # The member layout ('CIK##########.json') is ASSUMED from the per-CIK API; this run
        # is its first test. Keep the archive so the layout can be read without a re-download.
        COMPACT.unlink(missing_ok=True)
        raise SystemExit(f"compacted 0 filers: the archive layout is not what "
                         f"durability_data._cik_of expects. Archive kept at {FACTS_ZIP}.")
    # Neither archive is deleted here. `gates` deletes both once it passes.
    # A record with no 10-K facts is a filer on foreign annual forms only. The reproduction
    # gate counts it; it is no firm to the study, so it is not a "filer" below.
    ciks = {rec["cik"] for rec in iter_compacted(COMPACT) if rec["facts"]["us-gaap"]}
    _log(f"compacted {len(ciks)} filers (and {n - len(ciks)} on foreign forms only) -> {COMPACT}")
    if unreadable:
        _log(f"  {len(unreadable)} members could not be read (listed in fetch.json)")
    _archive(SUBS_URL, SUBS_ZIP)
    sic = sic_from_submissions_zip(SUBS_ZIP, ciks)
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / "sic.json").write_text(json.dumps(sic, indent=0, sort_keys=True) + "\n")
    _log(f"SIC for {len(sic)} of {len(ciks)} filers -> {RAW / 'sic.json'}")
    _write("fetch.json", {"filers": len(ciks), "foreign_only_filers": n - len(ciks),
                          "sic_found": len(sic), "unreadable_members": sorted(unreadable)},
           {**_data_state(), **code})


def load_firms(panels: Optional[list] = None, dropped: Optional[list] = None) -> list[ds.Firm]:
    """The study's firms. With `panels`, also appends every filer's comparison panel to it —
    the reproduction gate's population, which holds filers that are no firm to the study. With
    `dropped`, appends per filer the number of 10-K rows filed before their period ended."""
    config = load_config("config.yaml")
    sic = json.loads((RAW / "sic.json").read_text())
    firms = []
    for i, rec in enumerate(iter_compacted(COMPACT)):
        if panels is not None:
            panels.append(ds.comparison_panel(rec))
        if dropped is not None:
            dropped.append(count_filed_before_period_end(rec))
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
    inputs = _inputs()
    _keep_on_record("gates.json")
    panels: list = []
    dropped: list = []
    firms = load_firms(panels, dropped)
    by_cik = {f.cik: f for f in firms}
    dead = {cik: {"name": name, "need": need,
                  "got": by_cik[cik].last_bucket if cik in by_cik else None}
            for cik, (name, need) in DEAD_FILERS.items()}
    sizes = {y: len(ds.cross_section(firms, y, y)) for y in ds.FRAMES_UNIVERSE}
    # What the frames targets count: latest values, no sector mask, the foreign annual forms
    # too (prereg, amendment 3).
    compared = {y: ds.comparison_count(panels, y) for y in ds.FRAMES_UNIVERSE}
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
        "universe_sizes_unmasked": {y: len(ds.cross_section(firms, y, y, masked=False))
                                    for y in ds.FRAMES_UNIVERSE},
        # Not a gate. Firms with a ROIC that are out of the universe for a revenue tag.
        "universe_no_revenue_tag": {y: ds.no_revenue_count(firms, y) for y in ds.FRAMES_UNIVERSE},
        "universe_no_revenue_tag_assets_500m": {y: ds.no_revenue_count(firms, y, min_assets=5e8)
                                                for y in ds.FRAMES_UNIVERSE},
        # Not a gate. Rows never read because they were filed before their own period ended.
        "facts_filed_before_period_end": {"rows": sum(dropped), "filers": sum(n > 0 for n in dropped)},
        "comparison_counts": compared,
        "reproduction_failures": ds.reproduction_failures(compared),
        "level_slope_discovery": slope,
        "gap_rate_by_outcome_year": gap,
        "gap_spikes": ds.gap_spikes(gap),
        "discovery_cohort_n_by_year": {y: sum(r.year == y for r in disc) for y in ds.DISCOVERY},
        "discovery_state_shares": ds.state_shares(disc),
        # Not a gate. No debt tag reads as zero debt (prereg, Limitations).
        "zero_debt_share": {
            "universe": {y: ds.zero_debt_share(firms, y) for y in ds.FRAMES_UNIVERSE},
            "discovery_cohort": {y: ds.zero_debt_share(firms, y, {r.cik for r in disc if r.year == y})
                                 for y in ds.DISCOVERY}},
        # Not a gate. Read it before any beta: a row alone in its cell carries no weight.
        "discovery_share_alone_in_cell": {"sic2": ds.share_alone_in_cell(disc, 2),
                                          "sic3": ds.share_alone_in_cell(disc, 3)},
        "sectors_config_sha256": hashlib.sha256(json.dumps(
            load_config("config.yaml")["sectors"]["buckets"], sort_keys=True).encode()).hexdigest(),
    }
    result["passed"] = bool(result["completeness_ok"] and not result["reproduction_failures"]
                            and slope > 0 and not result["gap_spikes"])
    _write("gates.json", result, inputs)
    if not result["passed"]:
        raise SystemExit("GATES FAILED — no verdict. Diagnose first (prereg §Gates). "
                         f"{FACTS_ZIP} and {SUBS_ZIP} are kept for that.")
    FACTS_ZIP.unlink(missing_ok=True)
    SUBS_ZIP.unlink(missing_ok=True)


def _run(rows: list[ds.Row], *, with_bounds: bool) -> dict:
    out = {}
    for pred, outcome in ds.TESTS:
        _log(f"  {pred} / {outcome}")
        m = ds.measure(rows, pred, outcome, with_bounds=with_bounds)
        m["exit_rate_by_tercile"] = ds.exit_rate_by_tercile(rows, pred)
        m["gap_rate_by_tercile"] = ds.state_rate_by_tercile(rows, pred, "gap")
        m["low_ic_rate_by_tercile"] = ds.state_rate_by_tercile(rows, pred, "low_ic")
        m["beta_by_year"], m["n_by_year"] = {}, {}
        for y in sorted({r.year for r in rows}):
            samp = ds.sample([r for r in rows if r.year == y], pred, outcome)
            # Early start years are thin for a predictor that reads a year before XBRL.
            m["n_by_year"][y] = len(samp)
            try:
                m["beta_by_year"][y] = ds.fit(samp, pred)
            except (ValueError, ZeroDivisionError):
                m["beta_by_year"][y] = None
        out[f"{pred}/{outcome}"] = m
    return out


def _descriptive(rows: list[ds.Row]) -> dict:
    """Reported, never decision-bearing (pre-registration, "Reported, not decision-bearing")."""
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
        "hold_rate": {o: (lambda s: sum(y for _, y in s) / len(s) if s else None)(
            ds.sample(rows, None, o)) for o in ("held", "compounded")},
    }


def discovery() -> None:
    inputs = _inputs()
    if not _committed(PREREG):
        raise SystemExit(f"{PREREG} is not committed (or has uncommitted edits). "
                         "Pre-register first.")
    _keep_on_record("discovery.json")
    gate_file = RAW / "gates.json"
    if not gate_file.exists() or not json.loads(gate_file.read_text()).get("passed"):
        raise SystemExit("gates.json is missing or did not pass. Run `gates` first.")
    if not _committed(gate_file):
        raise SystemExit("gates.json is not committed (or was modified). Commit the gate result "
                         "before any beta is computed.")
    _require_same_inputs(json.loads(gate_file.read_text()), inputs, "gates.json")
    rows = cohorts(load_firms(), ds.DISCOVERY)
    tests = _run(rows, with_bounds=True)
    for m in tests.values():
        m["rules"] = (dict.fromkeys(("magnitude", "bounds", "continuous_sign", "sub_industry"), False)
                      if "error" in m else
                      ds.discovery_rules(m["beta"], m["se"], m["bound_held"], m["bound_not"],
                                         m["rank_beta"], m["beta_sic3_cells"]))
    _write("discovery.json", {"window": [ds.DISCOVERY.start, ds.DISCOVERY.stop - 1],
                              "rows": len(rows), "tests": tests,
                              "descriptive": _descriptive(rows)}, inputs)


def holdout() -> None:
    inputs = _inputs()
    _keep_on_record("holdout.json")
    disc_file = RAW / "discovery.json"
    if not _committed(disc_file):
        raise SystemExit(f"{disc_file} is not committed (or was modified). Write the discovery "
                         "section of the verdict note and commit both before opening the holdout.")
    if not _committed(VERDICT) or not any(
            line.startswith("## Discovery") for line in VERDICT.read_text().splitlines()):
        raise SystemExit(f"the verdict note {VERDICT} is not committed with its '## Discovery' "
                         "section. The discovery result is written down before the holdout opens.")
    _require_same_inputs(json.loads(disc_file.read_text()), inputs, "discovery.json")
    disc = json.loads(disc_file.read_text())["tests"]
    firms = load_firms()
    seen = {r.cik for r in cohorts(firms, ds.DISCOVERY)}
    rows = cohorts(firms, ds.HOLDOUT)
    tests = _run(rows, with_bounds=False)
    fresh = [r for r in rows if r.cik not in seen]
    for key, m in tests.items():
        pred, outcome = key.split("/")
        m["holdout_rule"] = "error" not in m and ds.holdout_rule(m["beta"], m["se"],
                                                                 m["beta_sic3_cells"])
        m["passes"] = ds.passes(disc[key]["rules"], m["holdout_rule"])
        samp = ds.sample(fresh, pred, outcome)
        try:
            m["new_firms_only"] = {"n": len(samp), "beta": ds.fit(samp, pred)}
        except (ValueError, ZeroDivisionError):
            m["new_firms_only"] = {"n": len(samp), "beta": None}
    _write("holdout.json", {"window": [ds.HOLDOUT.start, ds.HOLDOUT.stop - 1],
                            "rows": len(rows), "tests": tests,
                            "descriptive": _descriptive(rows),
                            "passed": sorted(k for k, m in tests.items() if m["passes"])}, inputs)


if __name__ == "__main__":
    load_env()
    steps = {"fetch": fetch, "gates": gates, "discovery": discovery, "holdout": holdout}
    if len(sys.argv) != 2 or sys.argv[1] not in steps:
        raise SystemExit(f"usage: probe_durability.py {{{'|'.join(steps)}}}")
    steps[sys.argv[1]]()

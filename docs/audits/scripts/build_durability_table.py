"""Build `src/shortlist/durability_table.json`: what the `/deep` ROIC-persistence section
ranks a name against, and the registered effects it quotes.

    uv run python docs/audits/scripts/build_durability_table.py            # write
    uv run python docs/audits/scripts/build_durability_table.py --check    # compare, exit 1 on a difference

TWO KINDS OF CONTENT, and only one can be refreshed.

- REFERENCE DISTRIBUTIONS (`universe`, `floors`, `cohort`, `completeness`): the ROIC universe
  of each history bucket as seen in the fiscal-2025 snapshot, its top-fifth floor, and the
  predictor values of the fiscal-2025 top fifth. Built here from the committed data file with
  the study's own functions (`probe_durability_equivalence.table_inputs`, whose output is
  digest-pinned). A rebuild on newer SEC data refreshes these.
- MEASURED EFFECTS (`cohorts`, `effects`, `predictors_tested`, `passed`): COPIED from the
  committed raw outputs of the pre-registered study. This script never estimates one. A
  rebuild cannot change them.

THE REFERENCE YEAR IS 2025, ONE PAST THE YEARS THE PHASE 0 GATES COVERED (2011-2024). The
reproduction gate has no target for it. `completeness` records why it is usable: the size of
the 2025 universe against 2024, the share of each year's universe that has a usable row one
year later, and the range the top-fifth floor could take if every firm of the shortfall were
at one end (docs/audits/2026-10-08-moat-durability-phase1.md).

NOT A STUDY STEP. No beta is computed and no gate is read."""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import probe_durability_equivalence as eq  # noqa: E402  (the sibling script: data path, loader)

from shortlist.backtest import durability_study as ds  # noqa: E402
from shortlist.config import load_config  # noqa: E402

ROOT, RAW = eq.ROOT, eq.RAW
TABLE = ROOT / "src/shortlist/durability_table.json"
SCHEMA = 1
TABLE_YEAR = eq.TABLE_YEAR
WINDOWS = {"discovery": [2011, 2017], "holdout": [2018, 2021]}


def _raw(name: str) -> dict:
    return json.loads((RAW / name).read_text())


def _continued(firms, year: int) -> float:
    """Share of the `year` universe (own snapshot) that has a usable row one year later."""
    members = ds.cross_section(firms, year, year)
    by_cik = {f.cik: f for f in firms}
    have = 0
    for cik in members:
        row = by_cik[cik].snaps.get(year + 1, {}).get(year + 1)
        have += row is not None and row.status != "missing"
    return have / len(members)


def _floor_bounds(roics: list[float], missing: int) -> list[float]:
    """The top-fifth floor if `missing` more firms were in the universe, all below every firm
    in it, and all above. The floor of a universe that is short of late filers lies between."""
    desc = sorted(roics, reverse=True)
    cut = (len(desc) + missing) // 5 - 1
    return [desc[cut], desc[cut - missing]]


def _reference(firms) -> dict:
    t = eq.table_inputs(firms)
    n_before = len(ds.cross_section(firms, TABLE_YEAR - 1, TABLE_YEAR - 1))
    n_now = len(t["hist"][TABLE_YEAR])
    cohort = {p: sorted(v[p] for v in t["members"].values() if v[p] is not None)
              for p in ("investment", "stability")}
    return {
        "universe": {str(y): t["hist"][y] for y in sorted(t["hist"])},
        "floors": {str(y): t["floors"][y] for y in sorted(t["floors"])},
        "cohort": {"n": len(t["members"]), **cohort},
        "completeness": {
            f"universe_{TABLE_YEAR - 1}": n_before,
            f"universe_{TABLE_YEAR}": n_now,
            # [all of the shortfall against the year before at the bottom, all at the top]
            "floor_bounds": _floor_bounds(t["hist"][TABLE_YEAR], max(n_before - n_now, 0)),
            f"continued_{TABLE_YEAR - 2}_{TABLE_YEAR - 1}": _continued(firms, TABLE_YEAR - 2),
            f"continued_{TABLE_YEAR - 1}_{TABLE_YEAR}": _continued(firms, TABLE_YEAR - 1)},
    }


def measured() -> dict:
    """The registered results, read from the committed raw outputs. Nothing is estimated."""
    out = {"discovery": _raw("discovery.json"), "holdout": _raw("holdout.json")}
    dec = _raw("decomposition.json")
    cohorts, effects = {}, {"investment": {}, "stability": {}}
    for window, run in out.items():
        shares = run["descriptive"]["state_shares"]
        cohorts[window] = {"years": WINDOWS[window], "hold_rate": run["descriptive"]["hold_rate"]["held"],
                           "not_counted": shares["exit"] + shares["gap"],
                           # Of the firms the hold rate counts, the share with too little
                           # invested capital for a ROIC at t+3. Such a firm is coded as held
                           # when its operating income is positive, so this bounds that coding.
                           "low_capital_share": shares["low_ic"] / (shares["observed"] + shares["low_ic"])}
        for p in effects:
            test = run["tests"][f"{p}/held"]
            # The start years the test has rows for: `stability` has none in 2011.
            years = sorted(int(y) for y, n in test["n_by_year"].items() if n)
            effects[p][window] = {"beta": test["beta"], "ci": [test["lo"], test["hi"]],
                                  "years": [years[0], years[-1]]}
    inv = effects["investment"]
    # The part of the predictor's association with the change in ln ROIC that runs through
    # capital (decomposition pre-registration, reading C; it decides nothing).
    inv["capital_share"] = {w: -dec[w]["investment"]["beta_capital"] / dec[w]["investment"]["beta_log"]
                            for w in WINDOWS}
    inv["profit_holdout"] = {"beta": dec["holdout"]["investment"]["beta_profit"],
                             "se": dec["holdout"]["investment"]["se_profit"]}
    effects["stability"]["holdout_beta_by_year"] = out["holdout"]["tests"]["stability/held"]["beta_by_year"]
    passed = sorted(k.split("/")[0] for k, t in out["holdout"]["tests"].items() if t["passes"])
    return {"cohorts": cohorts, "effects": effects, "passed": passed,
            "tests_run": len(out["holdout"]["tests"]),
            "predictors_tested": len({k.split("/")[0] for k in out["holdout"]["tests"]})}


def source() -> dict:
    sectors = load_config(str(ROOT / "config.yaml"))["sectors"]["buckets"]
    return {"compacted_sha256": eq._sha256(eq.COMPACT), "sic_sha256": eq._sha256(eq.SIC),
            "discovery_sha256": eq._sha256(RAW / "discovery.json"),
            "holdout_sha256": eq._sha256(RAW / "holdout.json"),
            "decomposition_sha256": eq._sha256(RAW / "decomposition.json"),
            "sectors_config_sha256": hashlib.sha256(
                json.dumps(sectors, sort_keys=True).encode()).hexdigest()}


def build(firms) -> dict:
    return {"schema": SCHEMA, "table_year": TABLE_YEAR, "source": source(),
            **_reference(firms), **measured()}


def dumps(table: dict) -> str:
    """One top-level key per line: the long lists stay on one line each, and a change to a
    measured number is a one-line diff. No date inside: the same inputs give the same bytes."""
    body = ",\n".join(f" {json.dumps(k)}: {json.dumps(table[k], sort_keys=True)}"
                      for k in sorted(table))
    return "{\n" + body + "\n}\n"


def main() -> None:
    text = dumps(build(eq.load_firms()))
    if "--check" in sys.argv[1:]:
        if not TABLE.exists() or TABLE.read_text() != text:
            raise SystemExit(f"{TABLE} differs from a rebuild")
        print("table matches a rebuild")
        return
    TABLE.write_text(text)
    print(f"wrote {TABLE} ({len(text):,} bytes)")


if __name__ == "__main__":
    main()

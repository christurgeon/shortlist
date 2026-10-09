"""The two durability passes, taken apart: did operating profit hold or only the denominator,
and are `investment` and `stability` one finding or two?

Evidence for Addendum 2 of docs/audits/2026-10-04-moat-durability-verdict.md. Definitions and
reading rules are fixed by docs/audits/2026-10-07-durability-decomposition-prereg.md; this
script computes them and decides nothing. Run from the repo root:

    cp docs/audits/raw-2026-10-04-durability/companyfacts-10k.jsonl.gz .cache/durability/
    uv run python docs/audits/scripts/probe_durability_decomposition.py

A DECOMPOSITION OF A RESULT THAT HAS BEEN SEEN. It cannot add or remove a pass. It refuses to
run until its pre-registration is committed, and on any data or study code other than those of
holdout.json. No network.

THE TRAP: `investment` is last year's growth of the ROIC denominator, and capital growth
persists. A firm that adds little capital keeps a small denominator, so its ROIC "holds" with
no difference in what the business earns. The frozen-denominator outcome asks the question
without that: would the firm still clear the floor on the capital it had at `t`? It is biased
the other way (profit earned on new capital is counted over old capital), so only a pass is
informative.
"""
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path
from statistics import pstdev

sys.path.insert(0, str(Path(__file__).resolve().parent))

import probe_durability as probe  # noqa: E402  (the sibling script: its data path and guards)

from shortlist.backtest import durability_study as ds  # noqa: E402
from shortlist.backtest._ols import ols  # noqa: E402
from shortlist.durability import YearRow  # noqa: E402

PREREG = Path("docs/audits/2026-10-07-durability-decomposition-prereg.md")
SELF = Path("docs/audits/scripts/probe_durability_decomposition.py")
PASSED = ("investment", "stability")
HALF = 0.5                        # the share rule 5 of the pre-registration already uses


def outcome_values(now: YearRow, later: YearRow, floor3: float) -> dict:
    """The decomposition outcomes of one observed cohort firm. `frozen`: does the profit of
    `t+3` over the capital of `t` clear the `t+3` floor. The three logs split the change in
    ROIC exactly (log = profit - capital) and exist only with a positive profit at `t+3`."""
    frozen = float(later.nopat / now.ic >= floor3)
    if later.nopat <= 0:
        return {"frozen": frozen, "log": None, "profit": None, "capital": None}
    profit, capital = math.log(later.nopat / now.nopat), math.log(later.ic / now.ic)
    return {"frozen": frozen, "log": profit - capital, "profit": profit, "capital": capital}


def boot(samp: list, estimate, *, reps: int = ds.BOOT_REPS, seed: int = ds.BOOT_SEED) -> dict:
    """`ds.bootstrap` for any estimate: the same firms, the same draws, the same summary."""
    by_cik: dict = defaultdict(list)
    for item in samp:
        by_cik[item[0].cik].append(item)
    ciks = sorted(by_cik)
    rng = random.Random(seed)
    values, singular = [], 0
    for _ in range(reps):
        draw = [item for c in rng.choices(ciks, k=len(ciks)) for item in by_cik[c]]
        try:
            values.append(estimate(draw))
        except ValueError:
            singular += 1
    values.sort()
    return {"se": pstdev(values), "lo": values[int(0.025 * len(values))],
            "hi": values[int(0.975 * len(values)) - 1], "reps": len(values), "singular": singular}


def fit_with(samp: list, pred: str, extra: str) -> float:
    """`ds.fit` with one more covariate: the rank of `extra`, held fixed."""
    cols = [[y for _, y in samp], [r.c0 for r, _ in samp], [r.c0 ** 2 for r, _ in samp],
            [r.c2 for r, _ in samp], [r.p[extra] for r, _ in samp], [r.p[pred] for r, _ in samp]]
    y, *xs = ds._demean(samp, cols, lambda r: (r.sic2, r.year))
    return ols(y, [list(t) for t in zip(*xs, strict=True)])[-1]


def split(items: list, pred: str, *, reps: int = ds.BOOT_REPS) -> dict:
    """Quantities A, B and C of the pre-registration for one predictor on one window. `items`
    is a list of (cohort row, its YearRow at t, its YearRow at t+3, the t+3 floor) for the
    rows observed at t+3."""
    kept = [(row, outcome_values(now, later, floor3)) for row, now, later, floor3 in items
            if pred in row.p and row.sic2 != ds.NO_SIC]
    out: dict = {"n": len(kept), "n_positive_profit": sum(v["log"] is not None for _, v in kept)}
    samples = {"observed": [(row, float(row.held)) for row, _ in kept],
               "frozen": [(row, v["frozen"]) for row, v in kept]}
    for key in ("log", "profit", "capital"):
        samples[key] = [(row, v[key]) for row, v in kept if v[key] is not None]
    for key, samp in samples.items():
        out[f"beta_{key}"] = ds.fit(samp, pred)
        b = boot(samp, lambda s: ds.fit(s, pred), reps=reps)
        out[f"se_{key}"], out[f"singular_{key}"] = b["se"], b["singular"]
    return out


def joint(rows: list, pred: str, other: str, *, reps: int = ds.BOOT_REPS) -> dict:
    """Quantity D: β of `pred` on the registered primary sample where `other` is defined too,
    alone and with the rank of `other` held fixed."""
    samp = [(r, y) for r, y in ds.sample(rows, pred, "held") if other in r.p]
    alone = boot(samp, lambda s: ds.fit(s, pred), reps=reps)
    held_fixed = boot(samp, lambda s: fit_with(s, pred, other), reps=reps)
    return {"n": len(samp), "beta_alone": ds.fit(samp, pred), "se_alone": alone["se"],
            "beta_joint": fit_with(samp, pred, other), "se_joint": held_fixed["se"],
            "singular": alone["singular"] + held_fixed["singular"]}


def observed_items(firms: list, window: range) -> tuple[list, list]:
    """(cohort rows of the window, the observed ones with their year rows and the t+3 floor).
    The floor is the one `ds.build_cohort` holds each firm to."""
    by_cik = {f.cik: f for f in firms}
    rows, items = [], []
    for year in window:
        cohort = ds.build_cohort(firms, year)
        later = year + ds.HORIZON
        floor3 = ds.quintile_floor(ds.cross_section(firms, later, later).values())
        rows += cohort
        for row in cohort:
            if row.state == "observed":
                snaps = by_cik[row.cik].snaps
                items.append((row, snaps[year][year], snaps[later][later], floor3))
    return rows, items


def readings(result: dict) -> dict:
    """The two reading rules of the pre-registration, applied to both windows."""
    windows = ("discovery", "holdout")
    r1 = {p: all(result[w][p]["beta_observed"] > 0
                 and result[w][p]["beta_frozen"] >= HALF * result[w][p]["beta_observed"]
                 for w in windows) for p in PASSED}
    r2 = all(result[w]["joint"][p]["beta_joint"] >= HALF * result[w]["joint"][p]["beta_alone"]
             for w in windows for p in PASSED)
    return {"R1_operating_profit_held": r1, "R2_two_findings": r2}


def main() -> None:
    for path in (PREREG, SELF, probe.VERDICT):
        if not probe._committed(path):
            raise SystemExit(f"{path} is not committed (or has uncommitted edits). The "
                             "decomposition is registered and committed before it is run.")
    inputs = probe._inputs()
    probe._require_same_inputs(json.loads((probe.RAW / "holdout.json").read_text()), inputs,
                               "holdout.json")
    probe._keep_on_record("decomposition.json")
    firms = probe.load_firms()
    result: dict = {}
    for name, window in (("discovery", ds.DISCOVERY), ("holdout", ds.HOLDOUT)):
        rows, items = observed_items(firms, window)
        result[name] = {"rows": len(rows), "observed": len(items)}
        for pred in PASSED:
            probe._log(f"  {name}: {pred}")
            result[name][pred] = split(items, pred)
        result[name]["joint"] = {"investment": joint(rows, "investment", "stability"),
                                 "stability": joint(rows, "stability", "investment")}
    result["readings"] = readings(result)
    probe._write("decomposition.json", result, inputs)
    print(json.dumps(result["readings"], indent=1))


if __name__ == "__main__":
    probe.load_env()
    main()

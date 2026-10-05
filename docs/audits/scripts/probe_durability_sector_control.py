"""Does the sector control hold sector fixed? SYNTHETIC DATA ONLY (2026-10-05).

Evidence for both amendments in docs/audits/2026-10-04-moat-durability-prereg.md (§Amendments):
tables 1-10 for the sector control, tables 11-26 for pass rule 5. It reads no SEC data and no
file: every row is drawn from `random.Random(seed)`. Run from the repo root (about 8 minutes,
pure Python; the second form runs tables 11-26 only, about 4 minutes):

    uv run python docs/audits/scripts/probe_durability_sector_control.py
    uv run python docs/audits/scripts/probe_durability_sector_control.py 200 rule5

THE QUESTION. The note's original sector control C1 was a leave-out SIC-2 mean of the outcome,
with sectors under 20 rows pooled. Its purpose: no predictor may pass by being a sector label.
Tables 1-7 plant a predictor with a TRUE WITHIN-SECTOR EFFECT OF ZERO whose group component
lines up with the group's hold rate, and ask what beta each candidate control reports. Tables
8-9 plant a real within-sector effect of 0.20, to check that a control does not remove it.

THE ESTIMATORS ARE COPIES, ON PURPOSE. `registered_c1` freezes the original definition so this
file keeps reproducing the defect after `durability_study.fit` is changed. Do not "refactor" it
to call the live module: it would then test the fix against itself.

READ THIS BEFORE QUOTING A NUMBER. "Fully aligned" is a worst case, not an estimate of real
data: it makes the predictor's sector component a perfect copy of the sector's hold rate. The
real size of a leak depends on real sector dispersion, which was not measured.
"""
import random
import statistics
import sys
import time
from collections import defaultdict

from shortlist.backtest._ols import ols
from shortlist.backtest.durability_study import avg_ranks

YEARS = list(range(2011, 2018))
_CDF = statistics.NormalDist().cdf


def world(seed, *, label="sector", align=1.0, between=0.5, within=0.0, firms=1000, sectors=65,
          none_share=0.0, years=YEARS, subs=4):
    """One synthetic discovery window: rows of dict(cik, year, sec, sec3, y, c0, c2, p).

    The outcome depends on the SIC-2 sector, the ROIC level, a firm-persistent part and — only
    when `within` > 0 — the predictor's WITHIN-sector part. `between` is the share of the
    predictor's variance carried by its group component; `align` is that component's correlation
    with the group's hold-rate shift. `label` picks the group: a SIC-2 sector ("sector"), a
    sector-by-start-year cell ("sector_year", with a sector-by-year shock in the outcome), or a
    sub-industry inside the sector ("sic3", with a sub-industry shift in the outcome)."""
    rng = random.Random(seed)
    sd = 0.10
    a = [rng.gauss(0, sd) for _ in range(sectors)]                       # SIC-2 hold-rate shift
    b = [rng.gauss(0, 1) for _ in range(sectors)]
    g = {(s, y): rng.gauss(0, sd) for s in range(sectors) for y in years}    # sector x year shock
    b2 = {k: rng.gauss(0, 1) for k in g}
    c3 = {(s, k): rng.gauss(0, sd) for s in range(sectors) for k in range(subs)}  # sub-industry shift
    b3 = {k: rng.gauss(0, 1) for k in c3}
    weights = [1 / (k + 1) for k in range(sectors)]      # a few large sectors, a long thin tail
    rows = []
    for i in range(firms):
        s = rng.choices(range(sectors), weights)[0]
        k = rng.randrange(subs)
        u = rng.gauss(0, 0.15)                           # firm-persistent part of the outcome
        e = rng.gauss(0, 1)                              # firm-persistent part of the predictor
        n_years = rng.choice((2, 3, 3, 4))
        start = rng.randrange(len(years) - n_years + 1)
        no_sic = rng.random() < none_share
        for y in years[start:start + n_years]:
            w = 0.8 * e + 0.6 * rng.gauss(0, 1)          # within-group part, variance 1
            if label == "sector":
                comp, shift = align * a[s] / sd + (1 - align ** 2) ** 0.5 * b[s], 0.0
            elif label == "sector_year":
                comp, shift = align * g[s, y] / sd + (1 - align ** 2) ** 0.5 * b2[s, y], g[s, y]
            else:
                comp, shift = align * c3[s, k] / sd + (1 - align ** 2) ** 0.5 * b3[s, k], c3[s, k]
            level = rng.random()
            prob = 0.5 + a[s] + shift + 0.3 * (level - 0.5) + u + within * (_CDF(w) - 0.5)
            rows.append({"cik": i, "year": y, "sec": "none" if no_sic else f"{s:02d}",
                         "sec3": "none" if no_sic else f"{s:02d}-{k}",
                         "z": between ** 0.5 * comp + (1 - between) ** 0.5 * w, "level": level,
                         "size": rng.random(), "y": float(rng.random() < min(0.98, max(0.02, prob)))})
    for y in years:                                      # ranks within the cohort-year
        idx = [j for j, r in enumerate(rows) if r["year"] == y]
        for name, src in (("c0", "level"), ("c2", "size"), ("p", "z")):
            for j, rk in avg_ranks({j: rows[j][src] for j in idx}).items():
                rows[j][name] = rk
    return rows


def _demean(rows, cols, key):
    out = []
    for col in cols:
        tot, cnt = defaultdict(float), defaultdict(int)
        for r, v in zip(rows, col, strict=True):
            tot[key(r)] += v
            cnt[key(r)] += 1
        out.append([v - tot[key(r)] / cnt[key(r)] for r, v in zip(rows, col, strict=True)])
    return out


def _base(rows):
    return [[r["y"] for r in rows], [r["c0"] for r in rows], [r["c0"] ** 2 for r in rows],
            [r["c2"] for r in rows]]


def _beta(y, xs):
    return ols(y, [list(t) for t in zip(*xs, strict=True)])[-1]


def registered_c1(rows, min_rows=20):
    """FROZEN COPY of the note's original C1 (durability_study.sector_rates at 7a5e69f): the
    sector's mean outcome leaving out the row's own year and own firm; small sectors pooled."""
    size = defaultdict(int)
    for r in rows:
        size[r["sec"]] += 1
    sec = {s: (s if n >= min_rows else "other") for s, n in size.items()}
    n, h = defaultdict(int), defaultdict(float)
    for r in rows:
        s = sec[r["sec"]]
        for k in ((s,), (s, "y", r["year"]), (s, "c", r["cik"]), (s, r["year"], r["cik"])):
            n[k] += 1
            h[k] += r["y"]
    overall = sum(r["y"] for r in rows) / len(rows)
    out = []
    for r in rows:
        s = sec[r["sec"]]
        ks = ((s,), (s, "y", r["year"]), (s, "c", r["cik"]), (s, r["year"], r["cik"]))
        cnt = n[ks[0]] - n[ks[1]] - n[ks[2]] + n[ks[3]]
        tot = h[ks[0]] - h[ks[1]] - h[ks[2]] + h[ks[3]]
        out.append(tot / cnt if cnt > 0 else overall)
    return out


def fit_registered(rows):
    """The ORIGINAL model: outcome ~ C0 + C0^2 + C1 + C2 + P, demeaned within start year."""
    cols = _base(rows) + [registered_c1(rows), [r["p"] for r in rows]]
    y, *xs = _demean(rows, cols, lambda r: r["year"])
    return _beta(y, xs)


def fit_two_way(rows, pool=0):
    """Sector + start-year fixed effects, exact on an unbalanced panel: year dummies (all but
    one) as regressors, every column demeaned within sector. `pool` merges small sectors."""
    size = defaultdict(int)
    for r in rows:
        size[r["sec"]] += 1
    years = sorted({r["year"] for r in rows})[1:]
    cols = _base(rows) + [[float(r["year"] == y) for r in rows] for y in years] + [[r["p"] for r in rows]]
    y, *xs = _demean(rows, cols, lambda r: r["sec"] if size[r["sec"]] >= pool else "other")
    return _beta(y, xs)


def fit_cells(rows, key="sec", keep_no_sic=False):
    """THE AMENDED MODEL (key="sec", keep_no_sic=False): outcome ~ C0 + C0^2 + C2 + P, every
    column demeaned within its (sector, start year) cell; rows with no SIC code left out."""
    rows = rows if keep_no_sic else [r for r in rows if r[key] != "none"]
    cols = _base(rows) + [[r["p"] for r in rows]]
    y, *xs = _demean(rows, cols, lambda r: (r[key], r["year"]))
    return _beta(y, xs)


FITS = {
    "original C1 (leave-out mean)": fit_registered,
    "sector + year FE": fit_two_way,
    "sector + year FE, pool<20": lambda rows: fit_two_way(rows, 20),
    "cells, no-SIC as one group": lambda rows: fit_cells(rows, keep_no_sic=True),
    "AMENDED: SIC-2 x year cells": fit_cells,
    "SIC-3 x year cells": lambda rows: fit_cells(rows, key="sec3"),
}


def bootstrap_se(rows, fit, reps, seed):
    """Firm bootstrap, the cell means recomputed inside each resample."""
    by = defaultdict(list)
    for r in rows:
        by[r["cik"]].append(r)
    ciks = sorted(by)
    rng = random.Random(seed)
    return statistics.pstdev(
        fit([r for c in rng.choices(ciks, k=len(ciks)) for r in by[c]]) for _ in range(reps))


def table(title, worlds, **kw):
    print(f"\n== {title}")
    betas, n = defaultdict(list), 0
    for seed in range(worlds):
        rows = world(seed, **kw)
        n = len(rows)
        for name, f in FITS.items():
            betas[name].append(f(rows))
    print(f"   {worlds} worlds, about {n} rows each. Columns: mean beta | sd across worlds | "
          "share with beta >= max(0.10, 2 sd)")
    for name, b in betas.items():
        sd = statistics.pstdev(b)
        print(f"   {name:30s} {statistics.mean(b):+.3f} | {sd:.3f} | "
              f"{sum(x >= max(0.10, 2 * sd) for x in b) / len(b):5.1%}")
    return betas


ALL_YEARS = list(range(2011, 2022))      # discovery start years 2011-2017, holdout 2018-2021
SUB_FLOOR = 0.03                         # a fixed floor, measured and not adopted


def rule5_table(title, worlds, **kw):
    """Does a sub-industry floor earn its place as a pass rule, and which floor? One world spans
    both windows, so a label and its group's hold rate persist from discovery into the holdout,
    as they would in real data. BASE is rules 1-2: beta >= max(0.10, 2 sd) on discovery and
    beta >= max(0.06, 1.64 sd) on holdout, `sd` being the spread across worlds (it stands in for
    the bootstrap SE). Each variant adds a condition on b3, the beta under SIC-3 x year cells:

      half       b3 >= half of beta, in both windows     (ADOPTED as rule 5)
      third, two-thirds   the same with 1/3 and 2/3: how much the result leans on "half"
      floor      b3 >= 0.03 in both windows (the floor the note uses for the bounds rule)
      disc-only  b3 >= 0.03 on discovery only
      bars       b3 >= 0.10 on discovery and >= 0.06 on holdout (the floors of rules 1-2)"""
    res, alone = [], []
    for seed in range(worlds):
        rows = world(seed, years=ALL_YEARS, firms=1600, **kw)
        d, h = [r for r in rows if r["year"] <= 2017], [r for r in rows if r["year"] >= 2018]
        res.append((fit_cells(d), fit_cells(h), fit_cells(d, key="sec3"), fit_cells(h, key="sec3")))
        cells = defaultdict(int)
        for r in d:
            cells[r["sec3"], r["year"]] += 1
        alone.append(sum(cells[r["sec3"], r["year"]] == 1 for r in d) / len(d))
    sd = [statistics.pstdev(x[i] for x in res) for i in range(4)]
    def share(k):
        return lambda x: x[2] >= k * x[0] and x[3] >= k * x[1]

    variants = {
        "rules 1-2": lambda x: True,
        "HALF": share(1 / 2),
        "third": share(1 / 3),
        "two-thirds": share(2 / 3),
        "floor": lambda x: x[2] >= SUB_FLOOR and x[3] >= SUB_FLOOR,
        "disc-only": lambda x: x[2] >= SUB_FLOOR,
        "bars": lambda x: x[2] >= 0.10 and x[3] >= 0.06,
    }
    base = [x[0] >= max(0.10, 2 * sd[0]) and x[1] >= max(0.06, 1.64 * sd[1]) for x in res]
    print(f"\n== {title}")
    print(f"   mean beta: discovery {statistics.mean(x[0] for x in res):+.3f}, holdout "
          f"{statistics.mean(x[1] for x in res):+.3f} | under SIC-3 cells: "
          f"{statistics.mean(x[2] for x in res):+.3f} (sd {sd[2]:.3f}), "
          f"{statistics.mean(x[3] for x in res):+.3f} (sd {sd[3]:.3f}) | "
          f"discovery rows alone in their SIC-3 cell: {statistics.mean(alone):.0%}")
    print("   joint pass: " + " | ".join(
        f"{name} {sum(b and ok(x) for b, x in zip(base, res, strict=True)) / worlds:.1%}"
        for name, ok in variants.items()))


def rule5(worlds) -> None:
    print("\n\nRULE 5 — how much of beta must survive SIC-3 x year cells.")
    print(f"{worlds} worlds per table, 1,600 firms over 11 start years (about 3,050 discovery and "
          "1,750 holdout rows).")
    null = dict(align=0.0, between=0.0)
    rule5_table("11. NULL, no group structure in the predictor", worlds, **null)
    rule5_table("12. NULL, SIC-2 label, fully aligned", worlds)
    rule5_table("13. NULL, sub-industry label, fully aligned", worlds, label="sic3")
    rule5_table("14. NULL, sub-industry label, half aligned", worlds, label="sic3", align=0.5)
    rule5_table("15. NULL, sub-industry label, fully aligned, 12 sub-industries per sector", worlds,
                label="sic3", subs=12)
    rule5_table("16. NULL, sub-industry label, fully aligned, 40 sub-industries per sector", worlds,
                label="sic3", subs=40)
    rule5_table("17. NULL, sub-industry label, fully aligned, 120 sub-industries per sector", worlds,
                label="sic3", subs=120)
    rule5_table("18. REAL within-sector effect 0.12, predictor all within-sector", worlds, within=0.12,
                **null)
    rule5_table("19. REAL 0.12, all within-sector, 12 sub-industries per sector", worlds, within=0.12,
                subs=12, **null)
    rule5_table("20. REAL 0.12, all within-sector, 40 sub-industries per sector", worlds, within=0.12,
                subs=40, **null)
    rule5_table("21. REAL 0.12, all within-sector, 120 sub-industries per sector", worlds, within=0.12,
                subs=120, **null)
    rule5_table("22. REAL within-sector effect 0.20, predictor all within-sector", worlds, within=0.20,
                **null)
    rule5_table("23. REAL 0.20, all within-sector, 120 sub-industries per sector", worlds, within=0.20,
                subs=120, **null)
    rule5_table("24. REAL within-sector effect 0.12, predictor half between-sector", worlds, align=0.0,
                within=0.12)
    rule5_table("25. REAL 0.12, half between-sector, 40 sub-industries per sector", worlds, align=0.0,
                within=0.12, subs=40)
    rule5_table("26. MIXED: real within-sector effect 0.12 PLUS a fully aligned sub-industry label",
                worlds, label="sic3", within=0.12)


def main() -> None:
    worlds = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    t0 = time.time()
    if sys.argv[2:] == ["rule5"]:        # tables 11-26 only
        rule5(worlds)
        print(f"\n[{time.time() - t0:.0f}s]")
        return
    print("TRUE WITHIN-SECTOR EFFECT = 0 in every table marked NULL.")
    null = table("1. NULL, SIC-2 label, fully aligned (the defect)", worlds)
    table("2. NULL, SIC-2 label, half aligned", worlds, align=0.5)
    table("3. NULL, SIC-2 label, not aligned", worlds, align=0.0)
    table("4. NULL, sector-by-year label, fully aligned (a sector cycle)", worlds, label="sector_year")
    table("5. NULL, sub-industry (SIC-3) label, fully aligned", worlds, label="sic3")
    table("6. NULL, SIC-2 label, fully aligned, 5% of firms have no SIC", worlds, none_share=0.05)
    table("7. NULL, SIC-2 label, fully aligned, holdout scale (4 years, 600 firms)", worlds,
          firms=600, years=list(range(2018, 2022)))
    table("8. PLANTED within-sector effect 0.20, predictor all within-sector", worlds, align=0.0,
          between=0.0, within=0.20)
    table("9. PLANTED within-sector effect 0.20, predictor half between-sector, not aligned", worlds,
          align=0.0, within=0.20)

    print("\n== 10. firm-bootstrap SE against the true spread (table 1's worlds)")
    for name in ("AMENDED: SIC-2 x year cells",):
        ses = [bootstrap_se(world(seed), FITS[name], 200, seed) for seed in range(20)]
        truth = statistics.pstdev(null[name])
        print(f"   {name:30s} mean bootstrap SE {statistics.mean(ses):.3f} (20 worlds, 200 reps) | "
              f"sd across worlds {truth:.3f} | ratio {statistics.mean(ses) / truth:.2f}")
    rule5(worlds)
    print(f"\n[{time.time() - t0:.0f}s]")


if __name__ == "__main__":
    main()

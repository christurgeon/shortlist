"""Print the tables of the moat-durability verdict note from the committed raw outputs.

Formatting only: it reads docs/audits/raw-2026-10-04-durability/*.json and computes nothing that
decides a result. Written and committed BEFORE any real number was seen, so no column was
chosen after one. Run from the repo root:

    uv run python docs/audits/scripts/print_durability_tables.py gates
    uv run python docs/audits/scripts/print_durability_tables.py discovery
    uv run python docs/audits/scripts/print_durability_tables.py holdout
"""
import json
import sys
from pathlib import Path

RAW = Path(sys.argv[2] if len(sys.argv) > 2 else "docs/audits/raw-2026-10-04-durability")
TARGETS = {2011: 2114, 2012: 2102, 2013: 2052, 2014: 2066, 2015: 1995, 2016: 2161, 2017: 2254,
           2018: 2214, 2019: 2190, 2020: 2207, 2021: 2359, 2022: 2309, 2023: 2245, 2024: 2179}
ORDER = [f"{p}/{o}" for o in ("held", "compounded") for p in
         ("track", "stability", "investment", "share_stability", "gross_margin", "incremental_roic")
         if not (p == "investment" and o == "compounded")]


def f(x, nd=3, sign=True):
    if x is None:
        return "—"
    return f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"


def yn(v):
    return "Y" if v else "n"


def gates():
    g = json.loads((RAW / "gates.json").read_text())
    fj = json.loads((RAW / "fetch.json").read_text())
    print(f"passed={g['passed']} completeness_ok={g['completeness_ok']} "
          f"reproduction_failures={g['reproduction_failures']} gap_spikes={g['gap_spikes']}")
    print(f"level_slope_discovery={g['level_slope_discovery']:.4f}")
    print(f"fetch: filers={fj['filers']} foreign_only={fj['foreign_only_filers']} sic_found={fj['sic_found']} "
          f"unreadable={len(fj['unreadable_members'])}")
    print(f"compacted_sha256={g['compacted_sha256']}\nsic_sha256={g['sic_sha256']}\n"
          f"code_commit={g['code_commit']} code_sha256={g['code_sha256'][:16]}… python={g['python_version']}")
    print("\ncompleteness:")
    for cik, d in g["completeness"].items():
        print(f"  {cik} {d['name']:20s} need {d['need']} got {d['got']}")
    print("\n| year | target (frames) | comparison count | ratio | study universe | unmasked | "
          "no revenue tag (assets ≥ $500M) | zero-debt share |")
    print("|---|---|---|---|---|---|---|---|")
    for y, tgt in TARGETS.items():
        c, u, m, nr, nb = (g[k][str(y)] for k in (
            "comparison_counts", "universe_sizes", "universe_sizes_unmasked",
            "universe_no_revenue_tag", "universe_no_revenue_tag_assets_500m"))
        z = g["zero_debt_share"]["universe"][str(y)]
        print(f"| {y} | {tgt} | {c} | {c / tgt:.3f} | {u} | {m} | {nr} ({nb}) | {f(z, 3, False)} |")
    print("\nfacts filed before their period ended (never read):", g["facts_filed_before_period_end"])
    print("\ngap rate by outcome year:", {k: round(v, 4) for k, v in g["gap_rate_by_outcome_year"].items()})
    print("discovery cohort n by year:", g["discovery_cohort_n_by_year"])
    print("discovery state shares:", {k: round(v, 4) for k, v in g["discovery_state_shares"].items()})
    print("discovery share alone in cell:", {k: round(v, 4) for k, v in g["discovery_share_alone_in_cell"].items()})
    print("zero-debt share, discovery cohort:",
          {k: round(v, 3) for k, v in g["zero_debt_share"]["discovery_cohort"].items()})


def _table(d, window):
    disc = window == "discovery"
    lo, k = (0.10, 2.0) if disc else (0.06, 1.64)
    head = ["test", "n", "β", "SE", "bar", "95% interval", "β SIC-3", "raw spread (wrong metric)"]
    head += (["bound: exits held", "bound: exits not", "rank β", "rules mag/bnd/sgn/sub"] if disc
             else ["holdout rule", "new-firms β (n)", "PASSES ALL FIVE"])
    print("| " + " | ".join(head) + " |")
    print("|" + "---|" * len(head))
    for key in ORDER:
        m = d["tests"][key]
        if "beta" not in m:
            print(f"| `{key}` | {m['n']} | ERROR: {m['error']} |" + " |" * (len(head) - 3))
            continue
        se = m.get("se")
        bar = None if se is None else max(lo, k * se)
        ci = "—" if "lo" not in m else f"{m['lo']:+.3f} to {m['hi']:+.3f}"
        row = [f"`{key}`", str(m["n"]), f(m["beta"]), f(se, 3, False), f(bar, 3, False), ci,
               f(m.get("beta_sic3_cells")), f(m.get("raw_tercile_spread"))]
        if disc:
            r = m["rules"]
            row += [f(m.get("bound_held")), f(m.get("bound_not")), f(m.get("rank_beta")),
                    "/".join(yn(r[x]) for x in ("magnitude", "bounds", "continuous_sign", "sub_industry"))]
        else:
            nf = m["new_firms_only"]
            row += [yn(m["holdout_rule"]), f"{f(nf['beta'])} ({nf['n']})", "**YES**" if m["passes"] else "no"]
        if "error" in m:
            row[-1] += f" — ERROR: {m['error']}"
        print("| " + " | ".join(row) + " |")


def _aux(d):
    rates = {k: None if v is None else round(v, 4) for k, v in d["descriptive"]["hold_rate"].items()}
    print(f"\nrows={d['rows']} window={d['window']} hold_rate={rates}")
    print("state shares:", {k: round(v, 4) for k, v in d["descriptive"]["state_shares"].items()})
    def thirds(xs):
        return "/".join("—" if x is None else f"{x:.3f}" for x in xs)

    print("\n| test | n | no SIC | no SIC, exit | alone SIC-2 | alone SIC-3 | singular | firms | "
          "exit rate by tercile (worst/mid/best) | gap rate | low_ic rate | ex energy+mining β |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for key in ORDER:
        m = d["tests"][key]
        print(f"| `{key}` | {m['n']} | {m['n_no_sic']} | {m['n_no_sic_exit']} | {m['n_alone_in_cell']} | "
              f"{m['n_alone_in_sic3_cell']} | {m.get('singular', '—')} | {m.get('firms', '—')} | "
              f"{thirds(m['exit_rate_by_tercile'])} | {thirds(m['gap_rate_by_tercile'])} | "
              f"{thirds(m['low_ic_rate_by_tercile'])} | {f(d['descriptive']['ex_energy_mining'].get(key))} |")
    years = sorted(next(iter(d["tests"].values()))["beta_by_year"])
    print("\n| test | " + " | ".join(f"{y} β (n)" for y in years) + " |")
    print("|---|" + "---|" * len(years))
    for key in ORDER:
        m = d["tests"][key]
        print(f"| `{key}` | " + " | ".join(f"{f(m['beta_by_year'][y])} ({m['n_by_year'][y]})" for y in years) + " |")
    print("\ninvestment on compounded (reported only):", f(d["descriptive"]["investment_on_compounded"]))


def discovery():
    d = json.loads((RAW / "discovery.json").read_text())
    _table(d, "discovery")
    _aux(d)
    print("\ndiscovery survivors:", [k for k in ORDER if "error" not in d["tests"][k] and all(d["tests"][k]["rules"].values())])


def holdout():
    d = json.loads((RAW / "holdout.json").read_text())
    _table(d, "holdout")
    _aux(d)
    print("\nPASSED:", d["passed"])


if __name__ == "__main__":
    {"gates": gates, "discovery": discovery, "holdout": holdout}[sys.argv[1]]()

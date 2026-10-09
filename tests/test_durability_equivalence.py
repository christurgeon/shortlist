"""The Phase 1 refactor moved the shared basis into `shortlist/durability.py`. These tests
recompute, on the CURRENT code and the committed data file, the digests that
`docs/audits/scripts/probe_durability_equivalence.py` wrote BEFORE the refactor.

About 20 s: the firms are built once for the module."""
import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

from shortlist import durability

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "docs/audits/scripts"
RAW = ROOT / "docs/audits/raw-2026-10-04-durability"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def probe():
    return _load("probe_durability_equivalence")


@pytest.fixture(scope="module")
def builder():
    return _load("build_durability_table")


@pytest.fixture(scope="module")
def firms(probe):
    return probe.load_firms()


@pytest.fixture(scope="module")
def table():
    return durability.load_table()


def test_the_refactor_moved_no_row_of_the_study(probe, firms):
    committed = json.loads((RAW / "equivalence.json").read_text())
    now = probe.digests(firms)
    assert committed["n_firms"] == len(firms)
    for key in ("year_rows", "cohort_rows", "table_inputs", "universe_sizes"):
        assert now[key] == committed[key], key


# ---------------------------------------------------------------- the reference table

def test_the_committed_table_is_a_rebuild_from_the_committed_data(builder, firms):
    # Covers every number in the file: the reference distributions are recomputed, and the
    # measured effects and the source hashes are read again from the raw outputs. A hand edit
    # of the table fails here.
    assert builder.TABLE.read_text() == builder.dumps(builder.build(firms))


def test_the_measured_numbers_are_the_verdicts(table):
    # Pinned to the numbers PRINTED in docs/audits/2026-10-04-moat-durability-verdict.md, so
    # that a joint edit of a raw output and the table is caught too.
    inv, stab = table["effects"]["investment"], table["effects"]["stability"]
    got = {
        "hold": [round(table["cohorts"][w]["hold_rate"], 3) for w in ("discovery", "holdout")],
        "investment": [round(inv[w]["beta"], 3) for w in ("discovery", "holdout")],
        "investment_ci": [round(x, 3) for w in ("discovery", "holdout") for x in inv[w]["ci"]],
        "stability": [round(stab[w]["beta"], 3) for w in ("discovery", "holdout")],
        "stability_ci": [round(x, 3) for w in ("discovery", "holdout") for x in stab[w]["ci"]],
        "by_year": [round(stab["holdout_beta_by_year"][y], 3) for y in ("2018", "2019", "2020", "2021")],
        "capital_share": [round(inv["capital_share"][w], 2) for w in ("discovery", "holdout")],
        "profit": [round(inv["profit_holdout"]["beta"], 3), round(inv["profit_holdout"]["se"], 3)],
    }
    assert got == {
        "hold": [0.586, 0.539],
        "investment": [0.149, 0.201], "investment_ci": [0.060, 0.245, 0.090, 0.306],
        "stability": [0.150, 0.216], "stability_ci": [0.029, 0.264, 0.089, 0.348],
        "by_year": [-0.037, -0.084, 0.347, 0.617],
        "capital_share": [0.54, 0.59],
        "profit": [0.091, 0.108],
    }
    # exit + gap: 12.9% + 2.9% on discovery, 7.8% + 3.4% on the holdout.
    assert [round(table["cohorts"][w]["not_counted"], 3) for w in ("discovery", "holdout")] == [0.158, 0.111]
    assert table["passed"] == ["investment", "stability"] and table["predictors_tested"] == 6


def test_the_fixed_wording_of_the_section_still_matches_the_table(table):
    # research/durability.py prints these words without reading a number. If a later table
    # breaks one, the wording must be rewritten against a new verdict.
    inv, stab = table["effects"]["investment"], table["effects"]["stability"]
    # "a little over half ... is capital that kept growing slowly"
    assert all(0.50 < inv["capital_share"][w] < 0.65 for w in ("discovery", "holdout"))
    # "profit, which is positive but not distinguishable from zero in 2018-2021"
    assert 0 < inv["profit_holdout"]["beta"] < 1.64 * inv["profit_holdout"]["se"]
    # "the later result comes from the 2020 and 2021 cohorts; 2018 and 2019 went the other way"
    by_year = stab["holdout_beta_by_year"]
    assert by_year["2018"] < 0 and by_year["2019"] < 0 and by_year["2020"] > 0 and by_year["2021"] > 0
    assert sorted(by_year) == ["2018", "2019", "2020", "2021"]
    # "Six predictors were tested. These two passed"
    assert table["predictors_tested"] == 6 and len(table["passed"]) == 2
    assert [table["cohorts"][w]["years"] for w in ("discovery", "holdout")] == [[2011, 2017], [2018, 2021]]


def test_the_sector_mask_is_the_one_the_gates_ran_on(table):
    gates = json.loads((RAW / "gates.json").read_text())
    assert table["source"]["sectors_config_sha256"] == gates["sectors_config_sha256"]
    assert table["source"]["compacted_sha256"] == gates["compacted_sha256"]


def test_the_reference_year_is_as_complete_as_the_year_before_it(table):
    # Fiscal 2025 is one past the years the reproduction gate covered. These two counts are why
    # it is usable; a table rebuilt on a half-filed year would fail here.
    c = table["completeness"]
    assert c["universe_2025"] >= 0.95 * c["universe_2024"]
    assert c["continued_2024_2025"] >= c["continued_2023_2024"] - 0.02
    assert table["table_year"] == 2025 and sorted(table["universe"]) == [2022, 2023, 2024, 2025]
    assert table["cohort"]["n"] >= 300


def test_the_table_lists_are_what_the_ranking_code_expects(table):
    for values in (*table["universe"].values(), table["cohort"]["investment"], table["cohort"]["stability"]):
        assert values == sorted(values) and len(values) >= 300
    for y, floor in table["floors"].items():
        assert floor == durability.quintile_floor(table["universe"][y])
    assert all(v <= 0 for v in table["cohort"]["stability"])        # minus a spread


def test_no_reference_firm_has_a_capital_growth_that_spans_two_years(probe, firms):
    # The live profile drops `investment` when the two year ends are not about a year apart (a
    # fiscal-year change puts two ends in one bucket). That guard is not in the study. It must
    # be a no-op on the cohort the live name is ranked against.
    year = probe.TABLE_YEAR
    members = probe.table_inputs(firms)["members"]
    by_cik = {f.cik: f for f in firms}
    gaps = []
    for cik, preds in members.items():
        if preds["investment"] is None:
            continue
        snap = by_cik[cik].snaps[year]
        gaps.append((date.fromisoformat(snap[year].end) - date.fromisoformat(snap[year - 1].end)).days)
    assert len(gaps) >= 300
    assert all(durability.YEAR_GAP_DAYS[0] <= g <= durability.YEAR_GAP_DAYS[1] for g in gaps)


def test_a_table_that_is_not_this_schema_is_no_table():
    text = durability.table_source()
    assert durability.parse_table(text) is not None
    assert durability.parse_table("") is None
    assert durability.parse_table(text[: len(text) // 2]) is None
    assert durability.parse_table("[]") is None
    assert durability.parse_table(text.replace('"schema": 1', '"schema": 2')) is None
    assert durability.parse_table(text.replace('"floors"', '"floor"')) is None

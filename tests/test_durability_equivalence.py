"""The Phase 1 refactor moved the shared basis into `shortlist/durability.py`. These tests
recompute, on the CURRENT code and the committed data file, the digests that
`docs/audits/scripts/probe_durability_equivalence.py` wrote BEFORE the refactor.

About 20 s: the firms are built once for the module."""
import importlib.util
import json
from pathlib import Path

import pytest

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
def firms(probe):
    return probe.load_firms()


def test_the_refactor_moved_no_row_of_the_study(probe, firms):
    committed = json.loads((RAW / "equivalence.json").read_text())
    now = probe.digests(firms)
    assert committed["n_firms"] == len(firms)
    for key in ("year_rows", "cohort_rows", "table_inputs", "universe_sizes"):
        assert now[key] == committed[key], key

"""docs/audits/scripts/probe_durability_decomposition.py — the follow-up to the durability
verdict: did operating profit hold or only the denominator, and are the two passes one?"""
import importlib.util
import math
import random
import subprocess
from pathlib import Path

import pytest

from shortlist.backtest import durability_study as ds
from shortlist.backtest.durability_study import Row
from shortlist.durability import TAX, YearRow

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "docs/audits/scripts/probe_durability_decomposition.py"


@pytest.fixture(scope="module")
def decomp():
    spec = importlib.util.spec_from_file_location("probe_durability_decomposition", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _yr(nopat, ic):
    return YearRow(end="2015-12-31", revenue=1e9, op_income=nopat / (1 - TAX), equity=ic,
                   debt=0.0, assets=2 * ic, gross_profit=None)


def _items(n=400, seed=5):
    """Cohort rows with the year rows at `t` and `t+3` behind them. `x` is a random rank."""
    rng = random.Random(seed)
    items = []
    for i in range(n):
        now = _yr(rng.uniform(200, 400), 1000.0)
        later = _yr(rng.uniform(-50, 600), rng.uniform(600, 2500))
        floor3 = 0.25
        row = Row(cik=f"f{i % 150}", year=2011 + i % 3, sic2=f"{i % 4}", roic=now.roic, revenue=1e9,
                  preds={}, state="observed", held=later.roic >= floor3, rank_t3=rng.random(),
                  rev_ratio=None, c0=rng.random(), c2=rng.random(), p={"x": rng.random()})
        items.append((row, now, later, floor3))
    return items


def test_frozen_denominator_asks_whether_profit_alone_still_clears_the_floor(decomp):
    now, later = _yr(300.0, 1000.0), _yr(450.0, 2000.0)       # profit +50%, capital doubled
    assert later.roic == pytest.approx(0.225)                  # under a floor of 0.30: not held
    v = decomp.outcome_values(now, later, 0.30)
    assert v["frozen"] == 1.0                                  # 450 / 1000 = 0.45 clears it
    assert v["profit"] == pytest.approx(math.log(1.5)) and v["capital"] == pytest.approx(math.log(2))
    assert v["log"] == pytest.approx(v["profit"] - v["capital"])
    assert decomp.outcome_values(now, _yr(250.0, 500.0), 0.30)["frozen"] == 0.0   # held, on less capital


def test_a_loss_at_t3_has_no_log_split_and_does_not_clear_the_floor(decomp):
    v = decomp.outcome_values(_yr(300.0, 1000.0), _yr(-10.0, 900.0), 0.30)
    assert v == {"frozen": 0.0, "log": None, "profit": None, "capital": None}


def test_the_split_is_exact_and_the_reference_is_the_registered_fit(decomp):
    items = _items()
    out = decomp.split(items, "x", reps=10)
    assert out["beta_log"] == pytest.approx(out["beta_profit"] - out["beta_capital"], abs=1e-9)
    held = [(row, float(row.held)) for row, *_ in items]
    assert out["beta_observed"] == pytest.approx(ds.fit(held, "x"))
    losses = sum(later.nopat <= 0 for _, _, later, _ in items)
    assert out["n"] == 400 and out["n_positive_profit"] == 400 - losses and losses > 0
    assert set(out) >= {"beta_frozen", "se_observed", "se_frozen", "se_log", "se_profit", "se_capital"}


def test_the_split_leaves_out_what_the_study_left_out(decomp):
    items = _items(60)
    items[0][0].sic2 = ds.NO_SIC                 # no sector to be held to
    del items[1][0].p["x"]                       # predictor not defined
    assert decomp.split(items, "x", reps=5)["n"] == 58


def test_the_bootstrap_is_the_studys_bootstrap(decomp):
    samp = [(row, float(row.held)) for row, *_ in _items(200)]
    mine = decomp.boot(samp, lambda s: ds.fit(s, "x"), reps=30)
    theirs = ds.bootstrap(samp, "x", reps=30)
    assert (mine["se"], mine["lo"], mine["hi"]) == (theirs["se"], theirs["lo"], theirs["hi"])
    assert mine["singular"] == 0


def test_joint_fit_gives_a_copy_of_the_other_predictor_no_credit(decomp):
    rng = random.Random(8)
    rows = []
    for i in range(1500):
        other = rng.random()
        rows.append(Row(cik=f"f{i}", year=2011 + i % 3, sic2=f"{i % 4}", roic=0.3, revenue=1e9,
                        preds={}, state="observed", held=rng.random() < 0.2 + 0.6 * other,
                        rank_t3=None, rev_ratio=None, c0=rng.random(), c2=rng.random(),
                        p={"other": other, "copy": min(1.0, max(0.0, other + rng.gauss(0, 0.15)))}))
    out = decomp.joint(rows, "copy", "other", reps=10)
    assert out["beta_alone"] > 0.35 and abs(out["beta_joint"]) < 0.12 and out["n"] == 1500
    rows[0].p.pop("other")
    assert decomp.joint(rows, "copy", "other", reps=5)["n"] == 1499     # both must be defined


def test_it_refuses_to_run_before_its_pre_registration_is_committed(decomp, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    decomp.PREREG.parent.mkdir(parents=True)
    decomp.PREREG.write_text("# drafted, not committed\n")
    with pytest.raises(SystemExit, match="not committed"):
        decomp.main()


def test_the_reading_rules_need_half_of_beta_in_both_windows(decomp):
    def window(frozen, joint_inv):
        one = {"beta_observed": 0.20, "beta_frozen": frozen}
        return {"investment": dict(one), "stability": {"beta_observed": 0.20, "beta_frozen": 0.10},
                "joint": {"investment": {"beta_alone": 0.20, "beta_joint": joint_inv},
                          "stability": {"beta_alone": 0.20, "beta_joint": 0.10}}}
    both = decomp.readings({"discovery": window(0.10, 0.10), "holdout": window(0.10, 0.10)})
    assert both == {"R1_operating_profit_held": {"investment": True, "stability": True},
                    "R2_two_findings": True}
    one = decomp.readings({"discovery": window(0.10, 0.10), "holdout": window(0.099, 0.099)})
    assert one == {"R1_operating_profit_held": {"investment": False, "stability": True},
                   "R2_two_findings": False}
    # a negative reference cannot be "held" by a frozen beta that is merely less negative
    neg = {"discovery": window(0.10, 0.10), "holdout": window(0.10, 0.10)}
    neg["holdout"]["investment"] = {"beta_observed": -0.20, "beta_frozen": -0.05}
    assert decomp.readings(neg)["R1_operating_profit_held"]["investment"] is False

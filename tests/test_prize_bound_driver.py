import yaml
from pathlib import Path

from shortlist.backtest.prize_bound import prize_bound
from shortlist.providers.mock import MockProvider, _SAMPLE
from shortlist.scoring import score

CONFIG = yaml.safe_load((Path(__file__).resolve().parents[1] / "config.yaml").read_text())


def _cards():
    prov = MockProvider()
    return [score(prov.fetch(t), CONFIG) for t in _SAMPLE]


def test_prize_bound_reports_weight_bound_and_candidate_churn():
    cards = _cards()
    # candidate values keyed by ticker; use each card's own momentum as a trivial
    # "candidate" so its churn must be ~zero, while the weight bound shows the ceiling.
    cand = {"mom_id": {c.ticker: (c.momentum or 0.0) for c in cards}}
    result = prize_bound(cards, cand, CONFIG["weights"], CONFIG, top_ns=(5, 10), seed=0)
    assert "weight_bound" in result and "candidates" in result and "verdict" in result
    assert set(result["candidates"]) == {"mom_id"}
    # a candidate equal to the incumbent momentum cannot churn the ranking
    assert result["candidates"]["mom_id"]["kendall_tau"] == 1.0
    assert result["weight_bound"]["kendall_tau"] <= 1.0
    assert result["verdict"] in {"STOP_WEIGHT_INERT", "STOP_COLLINEAR", "PROCEED"}


def test_prize_bound_effective_weight_distribution_reported():
    cards = _cards()
    result = prize_bound(cards, {}, CONFIG["weights"], CONFIG, top_ns=(5,), seed=0)
    ew = result["effective_momentum_weight"]
    assert ew["min"] <= ew["median"] <= ew["max"]
    assert ew["max"] >= ew["min"]

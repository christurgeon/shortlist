import copy
import dataclasses
from pathlib import Path

import yaml

from shortlist.backtest.prize_bound import composite_with, to_rank_scores
from shortlist.providers.mock import MockProvider, _SAMPLE
from shortlist.scoring import score

CONFIG = yaml.safe_load((Path(__file__).resolve().parents[1] / "config.yaml").read_text())


def _cards():
    prov = MockProvider()
    return [score(prov.fetch(t), CONFIG) for t in _SAMPLE]


def test_composite_with_reproduces_real_composite_when_momentum_unchanged():
    w = CONFIG["weights"]
    for card in _cards():
        again = composite_with(card, card.momentum, w, CONFIG)
        assert again == card.composite, f"{card.ticker}: {again} != {card.composite}"


def test_composite_with_moves_when_momentum_swapped():
    w = CONFIG["weights"]
    card = _cards()[0]
    base = composite_with(card, card.momentum, w, CONFIG)
    bumped = composite_with(card, 100.0, w, CONFIG)
    floored = composite_with(card, 0.0, w, CONFIG)
    assert floored <= base <= bumped
    assert bumped != floored   # momentum has SOME leverage on this name


def test_composite_with_honours_the_value_knobs_the_real_scorer_reads():
    # A non-default value block (upside_to_target off, shareholder_yield on) must still
    # reproduce the real composite: the value sub-score is recomputed with the config.
    cfg = copy.deepcopy(CONFIG)
    cfg["value"] = {"upside_to_target": False, "shareholder_yield": {"enabled": True}}
    cfg["thresholds"]["shareholder_yield"] = [-0.02, 0.10]
    for t in _SAMPLE:
        m = dataclasses.replace(MockProvider().fetch(t), shareholder_yield=0.08)
        card = score(m, cfg)
        assert composite_with(card, card.momentum, cfg["weights"], cfg) == card.composite, t


def test_to_rank_scores_maps_to_percentile_0_100():
    out = to_rank_scores([10.0, 20.0, 30.0, 40.0])
    assert out[0] == 0.0 and out[-1] == 100.0
    assert out == sorted(out)


def test_to_rank_scores_averages_ties():
    out = to_rank_scores([5.0, 5.0, 9.0])
    assert out[0] == out[1]            # tied inputs -> tied scores
    assert out[2] == 100.0


def test_to_rank_scores_single_element():
    assert to_rank_scores([7.0]) == [50.0]

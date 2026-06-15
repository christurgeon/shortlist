"""Memory-bounded XBRL backtest: lazy fact-loading + ticker-major iteration must
produce IDENTICAL results to the eager in-memory path, with bounded resident facts."""
import json
from datetime import date
from pathlib import Path

import yaml

from shortlist.backtest.signals import XbrlSignalSource
from shortlist.backtest.engine import collect_observations, observation_grid
from shortlist.backtest.xbrl import read_companyfacts_cache, _facts_cache_path
from shortlist.backtest.prices import PriceHistory

# Reuse the established XBRL fixtures (sibling test module; pytest prepend-import).
from test_xbrl_signal import _facts_for, _price_history

CONFIG = yaml.safe_load((Path(__file__).parents[1] / "config.yaml").read_text())
THRESH = CONFIG["thresholds"]


def _grid():
    return observation_grid(date(2023, 3, 1), date(2023, 12, 1), 3)


def _universe_facts(n):
    return {f"T{i}": _facts_for() for i in range(n)}


def _universe_hist(n):
    return {f"T{i}": _price_history() for i in range(n)}


def test_lazy_equals_eager_observations():
    facts = _universe_facts(4)
    hists = _universe_hist(4)
    grid = _grid()
    universe = sorted(facts)

    eager = XbrlSignalSource(facts, hists, THRESH)
    loader = lambda tk: facts.get(tk)          # noqa: E731 — TICKER(upper) -> facts
    lazy = XbrlSignalSource(None, hists, THRESH, fact_loader=loader, lru_size=2)

    def key(o):
        return (o.as_of, o.ticker, tuple(sorted(o.signals.items())))

    eager_obs = sorted(collect_observations(eager, universe, grid), key=key)
    lazy_obs = sorted(collect_observations(lazy, universe, grid), key=key)
    assert eager_obs and len(eager_obs) == len(lazy_obs)
    assert [key(o) for o in eager_obs] == [key(o) for o in lazy_obs]


def test_lru_bounds_resident_facts_and_loads_once_per_ticker():
    n = 5
    facts = _universe_facts(n)
    hists = _universe_hist(n)
    loads = []
    def loader(tk):
        loads.append(tk)
        return facts.get(tk)
    src = XbrlSignalSource(None, hists, THRESH, fact_loader=loader, lru_size=2)

    max_resident = 0
    # Drive it ticker-major, exactly as the engine does, recording LRU size.
    for tk in sorted(facts):
        for t in _grid():
            src.observe(tk, t)
            max_resident = max(max_resident, len(src._lru))
    assert max_resident <= 2                    # never holds more than lru_size
    # Ticker-major -> each ticker's facts load exactly once (served from LRU across dates).
    assert sorted(loads) == sorted(facts)
    assert len(loads) == n


def test_negative_load_is_cached_not_retried():
    misses = []
    def loader(tk):
        misses.append(tk)
        return None                             # ticker has no facts
    src = XbrlSignalSource(None, {}, THRESH, fact_loader=loader, lru_size=4)
    for t in _grid():
        assert src.observe("GHOST", t) is None
    assert misses == ["GHOST"]                  # loaded once, negative cached


def test_read_companyfacts_cache_roundtrip(tmp_path):
    cik = "0000000123"
    month = "2026-06"
    payload = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": []}}}}}
    cp = _facts_cache_path(str(tmp_path), cik, month)
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps(payload))
    assert read_companyfacts_cache(cik, cache_dir=str(tmp_path), month=month) == payload


def test_read_companyfacts_cache_marker_and_missing(tmp_path):
    month = "2026-06"
    cik = "0000000999"
    _facts_cache_path(str(tmp_path), cik, month).write_text(
        json.dumps({"_shortlist_no_us_gaap": True}))
    assert read_companyfacts_cache(cik, cache_dir=str(tmp_path), month=month) is None  # marker
    assert read_companyfacts_cache("0000000000", cache_dir=str(tmp_path), month=month) is None  # missing
    assert read_companyfacts_cache(None, cache_dir=str(tmp_path), month=month) is None  # no cik

from datetime import date, timedelta

from shortlist.backtest.prices import PriceHistory
from shortlist.backtest.signals import MomentumSignalSource, SnapshotSignalSource
from shortlist.data.sources import snapshot_from_closes
from shortlist.data.bridge import snapshot_to_metrics
from shortlist.data.models import TickerSnapshot, Profile, Fundamentals, Price
from shortlist.data.store import save
from shortlist import scoring

THRESH = {"price_vs_200dma": [-0.10, 0.30], "rel_strength_6m": [-0.15, 0.25],
          "eps_revision": [-0.05, 0.10]}


def _ramp_hist(ticker, n=260, step=1.0, start_px=100.0):
    d0 = date(2020, 1, 1)
    dates = [d0 + timedelta(days=i) for i in range(n)]
    closes = [start_px + i * step for i in range(n)]
    return PriceHistory(ticker, dates, closes)


def test_momentum_observation_matches_production_chain():
    hist = _ramp_hist("AAA")
    spy = _ramp_hist("SPY", step=0.2)
    src = MomentumSignalSource({"AAA": hist}, spy, THRESH, min_history=200)
    T = hist.dates[230]
    obs = src.observe("AAA", T)
    snap = snapshot_from_closes("AAA", hist.closes_through(T), spy.closes_through(T))
    expected = scoring.momentum_score(snapshot_to_metrics(snap), THRESH)
    assert obs is not None
    assert obs.signals["momentum"] == expected      # rides real scoring, no reimpl
    assert obs.as_of == T and obs.ticker == "AAA"


def test_momentum_dropped_when_insufficient_history():
    hist = _ramp_hist("AAA", n=260)
    spy = _ramp_hist("SPY", n=260)
    src = MomentumSignalSource({"AAA": hist}, spy, THRESH, min_history=200)
    early = hist.dates[50]            # only 51 closes <= T, < 200
    assert src.observe("AAA", early) is None


def test_momentum_unknown_ticker_none():
    src = MomentumSignalSource({}, _ramp_hist("SPY"), THRESH)
    assert src.observe("ZZZ", date(2020, 6, 1)) is None


def test_snapshot_source_roundtrips_store_and_scores(tmp_path):
    # Build a real snapshot, persist it, then re-score via the snapshot source.
    snap = TickerSnapshot(ticker="AAA", as_of="2026-01-15T00:00:00+00:00")
    snap.profile = Profile(name="A", sector="Tech", market_cap=50e9)
    snap.fundamentals = Fundamentals(roe=0.30, net_margin=0.25,
                                     interest_coverage=10.0, debt_to_equity=0.5)
    snap.price = Price(price=120.0, ma200=100.0, rel_strength_6m=0.1)
    save(snap, str(tmp_path))

    config = {
        "thresholds": THRESH | {"roe": [0.10, 0.35], "net_margin": [0.0, 0.30],
                                "interest_coverage": [2.0, 15.0], "debt_to_equity": [3.0, 0.0],
                                "gross_margin": [0.20, 0.70], "gross_margin_stability": [0.5, 1.0],
                                "roic": [0.05, 0.30], "revenue_cagr": [0.0, 0.20],
                                "fcf_cagr": [0.0, 0.20], "eps_cagr": [0.0, 0.20],
                                "revenue_growth_persistence": [0.5, 1.0],
                                "upside_to_target": [0.0, 0.40], "fcf_yield": [0.02, 0.08],
                                "pe_vs_history": [-0.20, 0.30], "peg": [3.0, 0.5],
                                "insider_sentiment": [-0.30, 0.30],
                                "insider_net_ratio": [-0.0005, 0.0005]},
        "weights": {"quality": 0.2, "moat": 0.2, "growth": 0.15,
                    "value": 0.22, "momentum": 0.08, "insider": 0.15},
        "gates": {"min_market_cap": 2e9, "max_debt_to_equity": 5.0,
                  "min_insider_sentiment": -0.60},
    }
    src = SnapshotSignalSource(str(tmp_path), config)
    obs = src.observe("AAA", date(2026, 1, 15))
    assert obs is not None
    assert "composite" in obs.signals
    assert obs.signals["composite"] > 0              # quality + momentum present
    assert obs.ticker == "AAA"


def test_snapshot_source_missing_day_none(tmp_path):
    src = SnapshotSignalSource(str(tmp_path), {"thresholds": {}, "weights": {}, "gates": {}})
    assert src.observe("ZZZ", date(2026, 1, 15)) is None

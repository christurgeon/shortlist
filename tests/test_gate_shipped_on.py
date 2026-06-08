"""Proves the SHIPPED config.yaml (blocks ON) activates the new gate logic — the
production-facing behavioral change that the local-literal CONFIG in test_scoring.py
cannot exercise."""
import yaml
from pathlib import Path

from shortlist.models import StockMetrics
from shortlist.scoring import score

CFG = yaml.safe_load((Path(__file__).resolve().parents[1] / "config.yaml").read_text())


def _m(**kw):
    # sic="3571" (electronic computers) -> unknown bucket -> no masking, always scored.
    return StockMetrics(ticker="X", sic="3571", market_cap=5e9, **kw)


def test_shipped_config_trips_over_leveraged_on_net_debt():
    card = score(_m(revenue=100.0, ebitda=10.0, net_debt_to_ebitda=5.0), CFG)
    assert "over_leveraged" in card.gates


def test_shipped_config_spares_buyback_compounder_artifact():
    # Thin-positive-equity artifact (explosive D/E), no EBITDA -> abstain under shipped config.
    card = score(_m(debt_to_equity=55.0), CFG)
    assert "over_leveraged" not in card.gates


def test_shipped_config_spares_strong_coverage_levered_name():
    card = score(_m(debt_to_equity=8.0, interest_coverage=6.0), CFG)
    assert "over_leveraged" not in card.gates


def test_shipped_config_trips_levered_weak_coverage_name():
    card = score(_m(debt_to_equity=8.0, interest_coverage=1.2), CFG)
    assert "over_leveraged" in card.gates


def test_shipped_config_excuses_hyper_grower_negative_fcf():
    card = score(_m(fcf_positive=False, revenue_cagr=0.30,
                    revenue_growth_persistence=0.80), CFG)
    assert "negative_fcf" not in card.gates
    assert "cash_burn" in card.flags   # burn still surfaced


def test_shipped_config_gates_stagnant_burner():
    card = score(_m(fcf_positive=False, revenue_cagr=0.04,
                    revenue_growth_persistence=0.80), CFG)
    assert "negative_fcf" in card.gates

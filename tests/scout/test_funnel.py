from datetime import date
from shortlist.scout.models import Emission
from shortlist.scout.funnel import aggregate, prefilter


def test_aggregate_merges_per_ticker_and_weights():
    ems = [Emission("AAPL", "yahoo:day_gainers", 0.8, "", True),
           Emission("AAPL", "edgar:form4_cluster_buy", 0.9, "", True),
           Emission("MSFT", "yahoo:most_actives", 0.5, "", True)]
    weights = {"yahoo:day_gainers": 1.0, "edgar:form4_cluster_buy": 1.5, "yahoo:most_actives": 1.0}
    cands = aggregate(ems, weights)
    by = {c.ticker: c for c in cands}
    assert by["AAPL"].interest == 0.8 * 1.0 + 0.9 * 1.5
    assert by["AAPL"].interest > by["MSFT"].interest


def test_prefilter_drops_cooldown_held_and_non_discovery_only():
    from shortlist.scout.models import Candidate
    booster_only = Candidate(ticker="NEWS")
    booster_only.add(Emission("NEWS", "finnhub:news_volume", 0.9, "", is_discovery=False), 0.5)
    real = Candidate(ticker="AAPL")
    real.add(Emission("AAPL", "yahoo:day_gainers", 0.8, "", is_discovery=True), 1.0)
    held = Candidate(ticker="TSLA")
    held.add(Emission("TSLA", "yahoo:day_gainers", 0.8, "", is_discovery=True), 1.0)

    kept = prefilter([booster_only, real, held],
                     in_cooldown=lambda t: False,
                     is_held=lambda t: t == "TSLA")
    assert [c.ticker for c in kept] == ["AAPL"]   # booster-only and held dropped

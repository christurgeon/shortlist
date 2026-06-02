from shortlist.data.models import TickerSnapshot, Insider
from shortlist.data.bridge import snapshot_to_metrics


def test_bridge_maps_conviction_fields():
    snap = TickerSnapshot(ticker="X", insider=Insider(
        net_value_6m=1e6, sentiment_mspr=0.2,
        distinct_buyers=4, role_weighted_buy_value=3e6, planned_sell_value=1e5))
    m = snapshot_to_metrics(snap)
    assert m.insider_distinct_buyers == 4
    assert m.insider_role_weighted_buy_value == 3e6
    assert m.insider_planned_sell_value == 1e5


def test_harness_coverage_unchanged_with_present_insider():
    # Insider PRESENT: the 3 new fields are non-signal -> coverage() denominator
    # unchanged vs an Insider carrying only the legacy txn fields.
    legacy = Insider(net_value_6m=1e6, buy_count=2, sell_count=0, sentiment_mspr=0.1)
    enriched = Insider(net_value_6m=1e6, buy_count=2, sell_count=0, sentiment_mspr=0.1,
                       distinct_buyers=2, role_weighted_buy_value=5e5, planned_sell_value=0.0)
    snap_a = TickerSnapshot(ticker="X", insider=legacy)
    snap_b = TickerSnapshot(ticker="X", insider=enriched)
    assert snap_a.coverage() == snap_b.coverage()


def test_conviction_fields_excluded_from_coverage_both_branches():
    # The 3 conviction fields are non-signal -> _signal_fields(Insider) must exclude
    # them (and recent). This pins the "zero coverage effect" guarantee that the
    # absent-object coverage() branch now relies on.
    from shortlist.data.models import Insider, _signal_fields, _NON_SIGNAL_FIELDS
    names = {f.name for f in _signal_fields(Insider)}
    for nm in ("distinct_buyers", "role_weighted_buy_value", "planned_sell_value", "recent"):
        assert nm not in names
        assert nm in _NON_SIGNAL_FIELDS or nm == "recent"

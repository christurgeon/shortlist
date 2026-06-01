from datetime import date
from shortlist.scout.edgar_index import cluster_buys_from_records


def test_cluster_detection_groups_buys_by_issuer():
    # Two distinct insiders buying the same issuer same day = a cluster.
    records = [
        {"ticker": "ABC", "insider": "Jane", "code": "P", "value": 250_000},
        {"ticker": "ABC", "insider": "John", "code": "P", "value": 120_000},
        {"ticker": "XYZ", "insider": "Sue",  "code": "P", "value": 90_000},   # lone buy
        {"ticker": "ABC", "insider": "Jane", "code": "S", "value": 999_999},  # sale ignored
    ]
    ems = cluster_buys_from_records(records, min_buyers=2)
    syms = {e.ticker for e in ems}
    assert syms == {"ABC"}            # only ABC has >=2 distinct buyers
    e = next(iter(ems))
    assert e.is_discovery is True
    assert "2 insiders" in e.evidence and "370" in e.evidence  # $370k total

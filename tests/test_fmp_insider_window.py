"""_normalize_fmp insider aggregation: net_value_6m must actually be 6m-windowed.

The FMP insider-trading/search payload is just "the most recent N transactions" —
for a low-velocity name that can span years. Netting it un-windowed and storing it
as net_value_6m mislabels the figure, and (worse) an all-stale list would still
build an Insider(net=0, buys=0, ...) whose present-but-zero fields claim the
coupled transaction group in _merge_insider AHEAD of EDGAR's properly-windowed
aggregate (fmp precedes edgar in harness_sources). Latent on the free tier (the
endpoint 402s) but live on any paid key.
"""
from datetime import date, timedelta

from shortlist.data.sources import _normalize_fmp


def _tx(days_ago: int, ttype: str, shares: float, price: float) -> dict:
    return {
        "transactionDate": (date.today() - timedelta(days=days_ago)).isoformat(),
        "transactionType": ttype,
        "securitiesTransacted": shares,
        "price": price,
        "reportingName": "A Insider",
        "typeOfOwner": "officer",
    }


def test_insider_net_is_windowed_to_6m():
    raw = {"insider": [
        _tx(10, "P-Purchase", 100, 10.0),    # in window: +1000
        _tx(400, "S-Sale", 1000, 50.0),      # ~13 months old: excluded
    ]}
    ins = _normalize_fmp("TEST", raw).insider
    assert ins is not None
    assert ins.net_value_6m == 1000.0
    assert ins.buy_count == 1
    assert ins.sell_count == 0
    assert len(ins.recent) == 1


def test_all_stale_insider_rows_leave_section_absent():
    # An Insider(net=0, buy_count=0, ...) built from zero in-window trades would
    # block EDGAR's real aggregate in _merge_insider — the section must stay None.
    raw = {"insider": [_tx(400, "S-Sale", 10, 5.0), _tx(500, "P-Purchase", 10, 5.0)]}
    assert _normalize_fmp("TEST", raw).insider is None


def test_undated_insider_rows_are_dropped_from_the_windowed_net():
    raw = {"insider": [
        _tx(10, "P-Purchase", 100, 10.0),
        {"transactionType": "S-Sale", "securitiesTransacted": 50, "price": 20.0},
    ]}
    ins = _normalize_fmp("TEST", raw).insider
    assert ins is not None
    assert ins.net_value_6m == 1000.0    # the undated sale can't be confirmed in-window
    assert ins.sell_count == 0


def test_other_coded_rows_dont_reach_recent_or_counts():
    # An award/exercise mixed in with a real purchase must not be counted as a
    # sale, must not appear in `recent`, and must not move the net.
    raw = {"insider": [
        _tx(10, "P-Purchase", 100, 10.0),   # +1000
        _tx(5, "A-Award", 500, 10.0),       # other: ignored entirely
        _tx(3, "M-Exercise", 200, 10.0),    # other: ignored entirely
    ]}
    ins = _normalize_fmp("TEST", raw).insider
    assert ins is not None
    assert ins.net_value_6m == 1000.0
    assert ins.buy_count == 1
    assert ins.sell_count == 0
    assert len(ins.recent) == 1
    assert all(t.kind == "buy" for t in ins.recent)


def test_all_other_coded_rows_leave_section_absent():
    # A batch of ONLY awards/exercises (no real P/S trade) must NOT build an
    # Insider(net_value_6m=0, buy_count=0, sell_count=0, recent=[]) — that
    # all-zero-but-present record would win _merge_insider wholesale (fmp
    # precedes edgar in harness_sources) and silently discard EDGAR's real
    # insider aggregate, since `_is_present(0)` is True.
    raw = {"insider": [
        _tx(10, "A-Award", 500, 10.0),
        _tx(5, "M-Exercise", 200, 10.0),
        _tx(2, "G-Gift", 50, 10.0),
    ]}
    assert _normalize_fmp("TEST", raw).insider is None

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


def test_non_trades_are_dropped_before_the_60_row_window():
    # Window starvation: awards/exercises carry no insider signal, so they must be
    # filtered BEFORE the 60-row slice. Left inside it, a burst of RSU vesting pushes
    # real purchases out of the window and silently understates the net.
    raw = {"insider": [_tx(5, "A-Award", 500, 10.0) for _ in range(59)]
                      + [_tx(6, "P-Purchase", 100, 10.0) for _ in range(5)]}
    ins = _normalize_fmp("TEST", raw).insider
    assert ins is not None
    assert ins.buy_count == 5                 # all five survive, not just the one
    assert ins.net_value_6m == 5000.0
    assert ins.sell_count == 0


def test_unpriced_trades_alone_leave_section_absent():
    # A real P/S row with no usable price cannot vouch for the section on its own:
    # tx_value is 0, so the record would carry a FABRICATED net_value_6m == 0, and
    # `_is_present(0)` is True — it would win _merge_insider wholesale and discard
    # EDGAR's real aggregate. Abstain instead, exactly as for an all-award batch.
    raw = {"insider": [
        _tx(5, "S-Sale", 1000, None),
        _tx(6, "P-Purchase", 500, None),
    ]}
    assert _normalize_fmp("TEST", raw).insider is None


def test_a_priced_trade_does_not_admit_its_unpriced_siblings():
    # REPLACES an earlier test that asserted the opposite ("the unpriced one still
    # counts toward the price-free buy/sell counts"). That test passed both before and
    # after the guard it was written for, so it bound nothing — and what it pinned as
    # intended was the small version of the partial-pricing clobber above: an unpriced
    # sale silently valued at $0 while still inflating sell_count. The record must
    # describe exactly the trades it valued, so the unpriced sibling is dropped.
    raw = {"insider": [
        _tx(5, "P-Purchase", 100, 10.0),   # +1000, valued
        _tx(6, "S-Sale", 1000, None),      # unpriced: dropped, not counted at zero
    ]}
    ins = _normalize_fmp("TEST", raw).insider
    assert ins is not None
    assert ins.net_value_6m == 1000.0
    assert ins.buy_count == 1 and ins.sell_count == 0


def test_partial_pricing_does_not_let_one_priced_row_vouch_for_the_rest():
    # THE ADJACENT CASE to test_unpriced_trades_alone_leave_section_absent. Presence was
    # batch-level, so ONE priced row vouched for arbitrarily many unpriced ones, and the
    # emitted record was internally incoherent: sell_count counted 59 transactions whose
    # value was deliberately excluded from net_value_6m, and `recent` (which feeds the
    # research.insider_detail line) carried nine value=0 rows. Unvalued rows are now
    # dropped, so the record describes exactly the trades it valued.
    # NOTE the scored net is +1.0 either way, and FMP still wins the merge here — that
    # is the separate fmp-vs-edgar priority question (TODO.md), not this test's subject.
    raw = {"insider": [_tx(5, "P-Purchase", 1, 1.0)]
                      + [_tx(6, "S-Sale", 1000, None) for _ in range(59)]}
    ins = _normalize_fmp("TEST", raw).insider
    assert ins is not None
    assert ins.net_value_6m == 1.0
    assert ins.buy_count == 1
    assert ins.sell_count == 0          # the 59 unvalued sales are NOT counted
    assert len(ins.recent) == 1         # nor do they pollute the research context line
    assert all(t.value for t in ins.recent)


def test_negative_transaction_values_never_invert_the_net():
    # tx_value is shares*price with no sign handling, so a negative securitiesTransacted
    # made `val` negative: it failed the `val > 0` presence check yet was STILL netted
    # with the buy/sell sign applied, so a SALE increased net insider buying by $10,000.
    # Unvaluable rows are dropped, so the sign can no longer invert.
    raw = {"insider": [
        _tx(5, "P-Purchase", 100, 10.0),      # a real +1000 buy
        _tx(6, "S-Sale", -1000, 10.0),        # negative shares: unvaluable, dropped
    ]}
    ins = _normalize_fmp("TEST", raw).insider
    assert ins is not None
    assert ins.net_value_6m == 1000.0     # was 11000.0 — a sale ADDING to net buying
    assert ins.buy_count == 1 and ins.sell_count == 0


def test_all_unvaluable_rows_abstain_even_when_codes_are_real():
    # REGRESSION GUARD, not a binding test — stated plainly because the previous round
    # shipped a test that looked binding and was not. This passes both before and after
    # the change; it exists so a future refactor of the valued-trade filter cannot
    # quietly start emitting a fabricated zero for a batch with no usable row at all.
    # (Generalises the all-unpriced case to negative and zero-share rows.)
    raw = {"insider": [
        _tx(5, "S-Sale", -1000, 10.0),
        _tx(6, "P-Purchase", 0, 10.0),
        _tx(7, "S-Sale", 1000, None),
    ]}
    assert _normalize_fmp("TEST", raw).insider is None


def test_one_malformed_row_never_costs_the_whole_fmp_snapshot():
    # End-to-end companion to test_tx_value_is_total_and_never_raises_on_junk: a junk
    # row must be dropped as unvaluable, NOT propagate out of _normalize_fmp (which the
    # collector would turn into an errored-empty SourceResult for all of FMP).
    raw = {"insider": [
        _tx(5, "P-Purchase", 100, 10.0),        # a real +1000 buy
        _tx(6, "S-Sale", "not-a-number", 10.0),  # junk: dropped, must not raise
    ]}
    ins = _normalize_fmp("TEST", raw).insider    # must not raise
    assert ins is not None
    assert ins.net_value_6m == 1000.0
    assert ins.buy_count == 1 and ins.sell_count == 0

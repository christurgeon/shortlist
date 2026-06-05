"""Unit tests for the shared FMP insider-transaction primitives."""

from shortlist.providers._fmp_insider import is_buy, net_value, tx_value


def _tx(shares, price, kind):
    return {"securitiesTransacted": shares, "price": price, "transactionType": kind}


def test_tx_value_is_shares_times_price():
    assert tx_value(_tx(100, 12.5, "P-Purchase")) == 1250.0


def test_tx_value_tolerates_missing_fields():
    assert tx_value({}) == 0
    assert tx_value({"securitiesTransacted": 10}) == 0  # no price


def test_is_buy_only_for_p_prefixed_codes():
    assert is_buy(_tx(1, 1, "P-Purchase")) is True
    assert is_buy(_tx(1, 1, "p-purchase")) is True  # case-insensitive
    assert is_buy(_tx(1, 1, "S-Sale")) is False
    assert is_buy(_tx(1, 1, "")) is False
    assert is_buy({}) is False


def test_net_value_signs_purchases_positive_sales_negative():
    txns = [_tx(100, 10, "P-Purchase"), _tx(40, 10, "S-Sale")]
    assert net_value(txns) == 1000.0 - 400.0


def test_net_value_caps_at_trailing_window():
    txns = [_tx(1, 1, "P-Purchase")] * 100
    assert net_value(txns, limit=60) == 60.0

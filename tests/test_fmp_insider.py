"""Unit tests for the shared FMP insider-transaction primitives."""

from shortlist.providers._fmp_insider import classify_tx, is_buy, net_value, tx_value


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


def test_classify_tx_buy_and_sell():
    assert classify_tx(_tx(1, 1, "P-Purchase")) == "buy"
    assert classify_tx(_tx(1, 1, "p-purchase")) == "buy"  # case-insensitive
    assert classify_tx(_tx(1, 1, "P")) == "buy"
    assert classify_tx(_tx(1, 1, "S-Sale")) == "sell"
    assert classify_tx(_tx(1, 1, "S")) == "sell"


def test_classify_tx_other_for_non_trade_codes():
    for code in ("A-Award", "M-Exercise", "G-Gift", "F-TaxWithholding", "C-Conversion", ""):
        assert classify_tx(_tx(1, 1, code)) == "other", code
    assert classify_tx({}) == "other"  # missing transactionType key


def test_net_value_ignores_other_codes_entirely():
    # A 1000 buy plus a 5000 "award" must net to +1000, NOT -4000 (the award is
    # not a sale and must not subtract from the net).
    txns = [_tx(100, 10, "P-Purchase"), _tx(500, 10, "A-Award")]
    assert net_value(txns) == 1000.0

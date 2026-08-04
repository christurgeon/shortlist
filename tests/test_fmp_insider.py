"""Unit tests for the shared FMP insider-transaction primitives.

The `"P-Purchase"` / `"S-Sale"` strings below are the REAL-FORMAT CONTRACT: FMP's
`transactionType` is an enriched `<CODE>-<Description>` string, not edgartools' bare
single-letter code. Do not "simplify" these fixtures to bare letters to make some
other classifier pass — that is the exact confusion `classify_tx` exists to prevent.
(They assert the assumption rather than evidencing it; see `classify_tx`'s docstring.)
"""

from shortlist.providers._fmp_insider import classify_tx, tx_value


def _tx(shares, price, kind):
    return {"securitiesTransacted": shares, "price": price, "transactionType": kind}


def test_tx_value_is_shares_times_price():
    assert tx_value(_tx(100, 12.5, "P-Purchase")) == 1250.0


def test_tx_value_tolerates_missing_fields():
    assert tx_value({}) == 0
    assert tx_value({"securitiesTransacted": 10}) == 0  # no price


def test_classify_tx_buy_and_sell():
    assert classify_tx(_tx(1, 1, "P-Purchase")) == "buy"
    assert classify_tx(_tx(1, 1, "p-purchase")) == "buy"  # case-insensitive
    assert classify_tx(_tx(1, 1, "P")) == "buy"
    assert classify_tx(_tx(1, 1, "S-Sale")) == "sell"
    assert classify_tx(_tx(1, 1, "S")) == "sell"


def test_classify_tx_other_for_non_trade_codes():
    # Awards, option exercises, gifts, tax-withholding and conversions are NOT sales.
    # Treating them as such is the bug this module was rewritten to fix.
    for code in ("A-Award", "M-Exercise", "G-Gift", "F-TaxWithholding", "C-Conversion", ""):
        assert classify_tx(_tx(1, 1, code)) == "other", code
    assert classify_tx({}) == "other"  # missing transactionType key


def test_classify_tx_splits_only_on_the_first_dash():
    # A hyphenated description must not confuse the leading-code match.
    assert classify_tx(_tx(1, 1, "S-Sale-Multiple")) == "sell"
    assert classify_tx(_tx(1, 1, "P - Purchase")) == "buy"  # spaces tolerated


def test_tx_value_is_total_and_never_raises_on_junk():
    # tx_value must be TOTAL. `_normalize_fmp` has no try/except of its own, and the
    # collector degrades a raising source to an errored-empty SourceResult — so one
    # malformed insider row would otherwise cost the ticker its ENTIRE FMP snapshot
    # (profile, quote, statements), not just its insider section.
    for junk in ({"securitiesTransacted": object(), "price": 10.0},
                 {"securitiesTransacted": 100, "price": object()},
                 {"securitiesTransacted": "not-a-number", "price": 10.0},
                 {"securitiesTransacted": [1, 2], "price": 10.0}):
        assert tx_value(junk) == 0.0, junk        # 0.0 => the caller's `> 0` drops it


def test_tx_value_coerces_string_encoded_numerics():
    # JSON APIs commonly emit numbers as strings. Coercing recovers a REAL trade
    # instead of discarding it; genuinely non-numeric text still falls through to 0.0
    # above. (Pre-fix this multiplied str*int into a garbage string, or raised.)
    assert tx_value({"securitiesTransacted": "100", "price": "12.5"}) == 1250.0
    assert tx_value({"securitiesTransacted": 100, "price": "12.5"}) == 1250.0

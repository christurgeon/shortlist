from shortlist.scout.bot import parse_add, parse_thesis, parse_ticker_note


# --- /add ---
def test_add_bare_ticker():
    assert parse_add("/add NVDA") == (["NVDA"], None, None)

def test_add_ticker_and_int_shares():
    assert parse_add("/add NVDA 12") == (["NVDA"], 12.0, None)

def test_add_fractional_shares():
    assert parse_add("/add NVDA 12.5") == (["NVDA"], 12.5, None)

def test_add_lowercase_ticker_uppercased():
    assert parse_add("/add nvda 5") == (["NVDA"], 5.0, None)

def test_add_dotted_ticker():
    assert parse_add("/add BRK.B") == (["BRK.B"], None, None)

def test_add_bulk_comma():
    assert parse_add("/add NVDA, MSFT, LMT") == (["NVDA", "MSFT", "LMT"], None, None)

def test_add_bulk_dedups_and_uppercases():
    assert parse_add("/add nvda, NVDA, msft") == (["NVDA", "MSFT"], None, None)

def test_add_non_numeric_second_token_is_error():
    # "2 years of runway" -> the old ambiguity; now rejected, not silently eaten
    tickers, shares, err = parse_add("/add NVDA years of runway")
    assert tickers == [] and shares is None and err is not None

def test_add_invalid_ticker_is_error():
    tickers, _, err = parse_add("/add 123$$")
    assert tickers == [] and err is not None

def test_add_empty_is_error():
    tickers, _, err = parse_add("/add")
    assert tickers == [] and err is not None


# --- /thesis (prose, case preserved) ---
def test_thesis_preserves_case_and_ticker_upper():
    assert parse_thesis("/thesis nvda Azure Capex Cycle") == ("NVDA", "Azure Capex Cycle", None)

def test_thesis_missing_text_is_error():
    tk, txt, err = parse_thesis("/thesis NVDA")
    assert tk == "NVDA" and txt is None and err is not None

def test_thesis_missing_ticker_is_error():
    tk, txt, err = parse_thesis("/thesis")
    assert tk is None and err is not None


# --- /hold, /remove (ticker + optional note) ---
def test_ticker_note_bare():
    assert parse_ticker_note("/hold NVDA") == ("NVDA", None, None)

def test_ticker_note_with_note_preserves_case():
    assert parse_ticker_note("/remove NVDA thesis broke") == ("NVDA", "thesis broke", None)

def test_ticker_note_missing_ticker_is_error():
    tk, _, err = parse_ticker_note("/hold")
    assert tk is None and err is not None

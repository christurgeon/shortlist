from pathlib import Path

from shortlist.portfolio import Holding, load_holdings


def _write(tmp_path, text):
    p = tmp_path / "portfolio.csv"
    p.write_text(text)
    return p


def test_load_clean_file(tmp_path):
    p = _write(tmp_path, "ticker,shares\nAAPL,40\nLMT,15\n")
    holdings, warnings = load_holdings(p)
    assert holdings == [Holding("AAPL", 40.0), Holding("LMT", 15.0)]
    assert warnings == []


def test_header_optional_and_case_whitespace_normalized(tmp_path):
    p = _write(tmp_path, "  aapl , 40 \nlmt,15\n")   # no header row
    holdings, _ = load_holdings(p)
    assert holdings == [Holding("AAPL", 40.0), Holding("LMT", 15.0)]


def test_extra_columns_ignored(tmp_path):
    p = _write(tmp_path, "ticker,shares,cost_basis\nAAPL,40,180.25\n")
    holdings, warnings = load_holdings(p)
    assert holdings == [Holding("AAPL", 40.0)]
    assert warnings == []


def test_malformed_rows_skipped_and_warned(tmp_path):
    p = _write(tmp_path, "AAPL,40\n,5\nLMT,notanumber\n\nMSFT,10\n")
    holdings, warnings = load_holdings(p)
    assert holdings == [Holding("AAPL", 40.0), Holding("MSFT", 10.0)]
    assert len(warnings) == 2   # missing ticker, non-numeric shares


def test_duplicate_tickers_summed_and_warned(tmp_path):
    p = _write(tmp_path, "AAPL,40\nAAPL,10\n")
    holdings, warnings = load_holdings(p)
    assert holdings == [Holding("AAPL", 50.0)]
    assert len(warnings) == 1


def test_missing_file_returns_empty_and_warning(tmp_path):
    holdings, warnings = load_holdings(tmp_path / "nope.csv")
    assert holdings == []
    assert warnings and "not found" in warnings[0].lower()

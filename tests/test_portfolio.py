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


def test_ticker_only_row_skipped_and_warned(tmp_path):
    p = _write(tmp_path, "AAPL,40\nLMT\nMSFT,10\n")
    holdings, warnings = load_holdings(p)
    assert holdings == [Holding("AAPL", 40.0), Holding("MSFT", 10.0)]
    assert len(warnings) == 1


def test_utf8_bom_handled(tmp_path):
    p = tmp_path / "portfolio.csv"
    p.write_bytes(b"\xef\xbb\xbfticker,shares\nAAPL,40\n")
    holdings, warnings = load_holdings(p)
    assert holdings == [Holding("AAPL", 40.0)]
    assert warnings == []


from shortlist.portfolio import Position, PortfolioSummary, summarize


class _Card:
    """Minimal ScoreCard stand-in: only the fields summarize/no_data read."""
    def __init__(self, ticker, *, composite=50.0, price=100.0, mcap=1e9,
                 sic_bucket="unknown", gates=None, flags=None, scored=True,
                 has_data=True):
        self.ticker = ticker
        self.composite = composite
        self.sic_bucket = sic_bucket
        self.gates = gates or []
        self.flags = flags or []
        self.scored = scored
        # no_data() reads these sub-scores + metrics.market_cap:
        v = 50.0 if has_data else None
        self.quality = self.moat = self.growth = self.momentum = v
        self.value = self.insider = self.risk = v
        self.metrics = type("M", (), {"price": price, "market_cap": mcap})()


def test_weights_sum_to_one_over_priced():
    holdings = [Holding("AAPL", 10), Holding("LMT", 10)]
    cards = [_Card("AAPL", price=100.0), _Card("LMT", price=300.0)]  # 1000 vs 3000
    s = summarize(holdings, cards)
    assert s.total_value == 4000.0
    by = {p.ticker: p for p in s.positions}
    assert abs(by["AAPL"].weight - 0.25) < 1e-9
    assert abs(by["LMT"].weight - 0.75) < 1e-9
    assert abs(sum(p.weight for p in s.positions) - 1.0) < 1e-9
    assert s.positions[0].ticker == "LMT"   # ordered weight-desc


def test_unpriced_excluded_from_denominator():
    holdings = [Holding("AAPL", 10), Holding("XXX", 10)]
    cards = [_Card("AAPL", price=100.0), _Card("XXX", price=None)]
    s = summarize(holdings, cards)
    assert s.total_value == 1000.0          # XXX excluded
    by = {p.ticker: p for p in s.positions}
    assert by["AAPL"].weight == 1.0
    assert by["XXX"].weight is None
    assert "XXX" in s.unpriced
    assert s.positions[-1].ticker == "XXX"  # priceless sorts last


def test_alerts_capture_gates_flags_notscored_nodata():
    holdings = [Holding("A", 1), Holding("B", 1), Holding("C", 1),
                Holding("D", 1), Holding("E", 1)]
    cards = [_Card("A", gates=["negative_fcf"]),
             _Card("B", flags=["value_trap"]),
             _Card("C", scored=False),
             _Card("D"),                                  # clean -> not an alert
             _Card("E", has_data=False, mcap=None)]       # no-data typo
    s = summarize(holdings, cards)
    alert_tickers = {p.ticker for p in s.alerts}
    assert alert_tickers == {"A", "B", "C", "E"}
    assert "E" in s.no_data_tickers


def test_sector_weights_grouped_by_bucket():
    holdings = [Holding("JPM", 10), Holding("BAC", 10), Holding("AAPL", 10)]
    cards = [_Card("JPM", price=100, sic_bucket="financials"),
             _Card("BAC", price=100, sic_bucket="financials"),
             _Card("AAPL", price=100, sic_bucket="unknown")]
    s = summarize(holdings, cards)
    sectors = dict(s.sector_weights)
    assert abs(sectors["financials"] - 2/3) < 1e-9
    assert s.sector_weights[0][0] == "financials"   # desc


def test_holding_with_no_matching_card_is_no_data():
    s = summarize([Holding("ZZZ", 5)], [])
    pos = s.positions[0]
    assert pos.card is None and pos.no_data is True
    assert "ZZZ" in s.no_data_tickers and pos in s.alerts


def test_all_unpriced_total_value_none():
    s = summarize([Holding("A", 1)], [_Card("A", price=None)])
    assert s.total_value is None
    assert s.positions[0].weight is None
    assert s.weighted_composite is None

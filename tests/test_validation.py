from types import SimpleNamespace

import pytest

from shortlist.validation import valid_format, partition_format, no_data


@pytest.mark.parametrize("sym", ["NVDA", "A", "BRK.B", "BF-B", "MSFT", "GOOGL"])
def test_valid_format_accepts_real_symbols(sym):
    assert valid_format(sym) is True


@pytest.mark.parametrize("sym", ["HELLOWORLD", "123", "", "$$", "TOOLONGX"])
def test_valid_format_rejects_junk(sym):
    assert valid_format(sym) is False


def test_valid_format_length_boundary():
    # 1 leading letter + up to 5 more = max 6 chars.
    assert valid_format("ABCDEF") is True     # exactly 6 — boundary pass
    assert valid_format("ABCDEFG") is False   # 7 — boundary fail
    assert valid_format("1ABCDE") is False    # must lead with a letter


def test_partition_format_splits_and_preserves_order():
    good, bad = partition_format(["NVDA", "HELLOWORLD", "BRK.B", "123"])
    assert good == ["NVDA", "BRK.B"]
    assert bad == ["HELLOWORLD", "123"]


def test_partition_format_empty_input():
    assert partition_format([]) == ([], [])


def _card(**kw):
    # SimpleNamespace stands in for a ScoreCard; defaults make a "no-data" card.
    base = dict(quality=None, moat=None, growth=None, momentum=None,
                value=None, insider=None, risk=None, metrics=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_no_data_true_for_empty_card():
    assert no_data(_card()) is True


def test_no_data_false_for_fmp_gated_card():
    # FMP gated: fundamentals None, but Finnhub backfilled market_cap + insider.
    card = _card(insider=40.0, metrics=SimpleNamespace(market_cap=1.2e9))
    assert no_data(card) is False


def test_no_data_false_for_price_only_card():
    # Real ticker, only price-derived risk present, no market_cap yet.
    assert no_data(_card(risk=55.0)) is False


def test_no_data_false_for_healthy_card():
    card = _card(quality=70.0, moat=60.0, metrics=SimpleNamespace(market_cap=5e9))
    assert no_data(card) is False

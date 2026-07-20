"""Pure stake-% parser tests — fixtures are real (trimmed) SEC cover pages."""
from pathlib import Path

from shortlist.scout.stake import MIN_INCREASE_PP, extract_stake_pct, pair_key

FIX = Path(__file__).parent / "fixtures" / "stake"


def _read(name: str) -> str:
    return (FIX / name).read_text()


def test_modern_xml_amendment():
    pct = extract_stake_pct(_read("modern_amendment.xml"))
    assert pct is not None and 0 < pct <= 100


def test_legacy_html_amendment():
    pct = extract_stake_pct(_read("legacy_amendment_html.txt"))
    assert pct is not None and 0 < pct <= 100


def test_legacy_text_amendment():
    pct = extract_stake_pct(_read("legacy_amendment_text.txt"))
    assert pct is not None and 0 < pct <= 100


def test_legacy_sibling_div_amendment():
    # Real (trimmed) FLOTEK INDUSTRIES cover-page fragment (SC 13D/A,
    # 0001013594-22-000096, filed 2022-02-04): row 13's label and its "3.1%"
    # value sit in sibling <div>s inside the same <td> -- a shape edgartools'
    # .text() rendering drops (the label survives, the value doesn't) but the
    # tag-strip tier here recovers directly from raw HTML. Correct value
    # confirmed against the filing's own cover page (max across the 5
    # reporting-person pages in the full document is 9.4%; this trimmed
    # single-page fragment carries only the 3.1% page).
    assert extract_stake_pct(_read("legacy_amendment_sibling_div.txt")) == 3.1


def test_garbage_abstains():
    assert extract_stake_pct(_read("garbage_no_pct.txt")) is None
    assert extract_stake_pct("") is None
    assert extract_stake_pct(None) is None


def test_multi_coverpage_takes_max():
    # Two reporting persons: 3.1% and 7.2% -> the group aggregate proxy is the max.
    raw = ("13 PERCENT OF CLASS REPRESENTED BY AMOUNT IN ROW (11)\n 3.1%\n"
           "13 PERCENT OF CLASS REPRESENTED BY AMOUNT IN ROW (11)\n 7.2%\n")
    assert extract_stake_pct(raw) == 7.2


def test_out_of_range_values_dropped():
    assert extract_stake_pct("PERCENT OF CLASS ...\n 250%\n") is None
    assert extract_stake_pct("PERCENT OF CLASS ...\n 0%\n") is None


def test_pair_key():
    assert pair_key("1234", 567) == "0000001234|0000000567"
    assert pair_key(None, 567) is None
    assert pair_key("abc", 567) is None


def test_constants():
    assert MIN_INCREASE_PP == 2.0

"""Direct cover for the shared leaf the EDGAR clients depend on.

Both symbols previously lived in separate modules and were imported across module
boundaries, so retiring either module would silently have broken the other. This file pins
them at their new single source.
"""
from shortlist.edgar._ticker_rules import (
    _FIFTH_LETTER_SUFFIXES,
    junk_suffix,
    normalize_items,
)


class TestJunkSuffix:
    def test_five_letter_suffix_is_junk(self):
        # F=foreign ordinary, Y=ADR, W=warrant, U=unit, R=rights, Q=bankruptcy, X=mutual fund
        for t in ("ABCDF", "ABCDY", "ABCDW", "ABCDU", "ABCDR", "ABCDQ", "BBASX"):
            assert junk_suffix(t), t

    def test_four_letter_ending_in_suffix_is_fine(self):
        # The rule is 5-letter-only: WOOF is a real common stock.
        assert not junk_suffix("WOOF")
        assert not junk_suffix("SNDX")

    def test_dotted_share_class_is_not_dropped(self):
        assert not junk_suffix("BRK.B")

    def test_plain_common_stock(self):
        assert not junk_suffix("AAPL")
        assert not junk_suffix("GOOGL")   # 5 letters, L is not a suffix code


class TestNormalizeItems:
    def test_comma_string_from_submissions_json(self):
        assert normalize_items("1.01,3.03") == ("1.01", "3.03")

    def test_edgartools_labelled_list(self):
        got = normalize_items(["Item 1.03 Bankruptcy", "Item 2.01 Completion"])
        assert got == ("1.03", "2.01")

    def test_dedupes_preserving_first_seen_order(self):
        assert normalize_items("3.03, 1.01, 3.03") == ("3.03", "1.01")

    def test_junk_and_none_yield_empty_never_raise(self):
        assert normalize_items(None) == ()
        assert normalize_items("no codes here") == ()
        assert normalize_items(12345) == ()
        assert normalize_items(object()) == ()


def test_suffix_set_is_the_documented_seven():
    assert frozenset("FYWURQX") == _FIFTH_LETTER_SUFFIXES

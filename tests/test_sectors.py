from shortlist.sectors import (
    extract_sic,
    gate_applicable,
    leg_applicable,
    resolve_bucket,
)

CFG = {
    "sectors": {
        "buckets": [
            {"name": "reit", "sic_ranges": [[6798, 6798]]},
            {"name": "insurer", "sic_ranges": [[6300, 6399], [6411, 6411]]},
            {"name": "financials", "sic_ranges": [[6020, 6099], [6120, 6179],
                                                  [6199, 6199], [6211, 6211], [6712, 6712]]},
        ],
        "masked_legs": ["gross_margin", "gross_margin_stability", "roic",
                        "fcf_yield", "fcf_cagr", "interest_coverage", "debt_to_equity"],
        "masked_gates": ["negative_fcf", "over_leveraged"],
    },
}


def test_resolve_bucket_boundaries():
    assert resolve_bucket("6211", CFG) == "financials"   # broker-dealer (SCHW)
    assert resolve_bucket(6022, CFG) == "financials"     # state bank, int input
    assert resolve_bucket("6798", CFG) == "reit"
    assert resolve_bucket("6311", CFG) == "insurer"      # life insurance
    assert resolve_bucket("6712", CFG) == "financials"   # bank holding co


def test_reit_not_swallowed_by_financials_ranges():
    assert resolve_bucket("6798", CFG) == "reit"


def test_exchanges_and_advisers_are_unknown():
    assert resolve_bucket("6231", CFG) == "unknown"      # security/commodity exchange (ICE-like)
    assert resolve_bucket("6282", CFG) == "unknown"      # investment advice / asset manager
    assert resolve_bucket("7372", CFG) == "unknown"      # prepackaged software


def test_resolve_bucket_unknown_and_junk():
    assert resolve_bucket(None, CFG) == "unknown"
    assert resolve_bucket("", CFG) == "unknown"
    assert resolve_bucket("None", CFG) == "unknown"
    assert resolve_bucket("abc", CFG) == "unknown"


def test_leg_applicable():
    assert leg_applicable("financials", "fcf_yield", CFG) is False
    assert leg_applicable("financials", "roe", CFG) is True
    assert leg_applicable("unknown", "fcf_yield", CFG) is True   # nothing masked when unknown
    assert leg_applicable("reit", "interest_coverage", CFG) is False


def test_gate_applicable():
    assert gate_applicable("financials", "over_leveraged", CFG) is False
    assert gate_applicable("financials", "below_min_mktcap", CFG) is True
    assert gate_applicable("unknown", "over_leveraged", CFG) is True


def test_extract_sic_normalizes():
    class C:  # duck-typed edgartools Company
        sic = 6211

    assert extract_sic(C()) == "6211"

    class C2:
        sic = "0006798"

    assert extract_sic(C2()) == "6798"

    class C3:
        @property
        def sic(self):
            raise RuntimeError("boom")

    assert extract_sic(C3()) is None      # never raises
    assert extract_sic(None) is None

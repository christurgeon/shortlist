from shortlist.scout.factors import parse_ff3_monthly

_SAMPLE = """This file was created ...

,Mkt-RF,SMB,HML,RF
202501,   2.50,  -1.00,   0.50,   0.35
202502,  -3.00,   0.40,  -0.20,   0.34

 Annual Factors: January-December
,Mkt-RF,SMB,HML,RF
2025,  10.0,  1.0,  2.0,  4.0
"""


def test_parse_ff3_monthly_decimals_and_stops_at_annual():
    f = parse_ff3_monthly(_SAMPLE)
    assert set(f) == {"2025-01", "2025-02"}          # annual block ignored
    mkt, smb, hml, rf = f["2025-01"]
    assert abs(mkt - 0.025) < 1e-9 and abs(rf - 0.0035) < 1e-9   # percent -> decimal
    assert abs(f["2025-02"][0] - (-0.03)) < 1e-9


def test_parse_ff3_ignores_nonmonthly_rows():
    f = parse_ff3_monthly("garbage\n,Mkt-RF,SMB,HML,RF\n202513, 1,1,1,1\nfoo,bar\n")
    assert f == {}                                    # 202513 is not a valid month

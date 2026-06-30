from shortlist.scout import daily


def test_digest_sources_drops_fmp_when_disabled():
    base = ["yahoo", "fmp", "finnhub", "edgar", "finra", "wsb"]
    assert daily.digest_sources(base, include_fmp=False) == [
        "yahoo", "finnhub", "edgar", "finra", "wsb"
    ]


def test_digest_sources_keeps_fmp_when_enabled():
    base = ["yahoo", "fmp", "finnhub", "edgar"]
    assert daily.digest_sources(base, include_fmp=True) == base
    # returns a copy, not the same list object (no aliasing surprises)
    assert daily.digest_sources(base, include_fmp=True) is not base


def test_digest_sources_noop_when_fmp_absent():
    base = ["yahoo", "finnhub", "edgar"]
    assert daily.digest_sources(base, include_fmp=False) == base
    assert daily.digest_sources(base, include_fmp=True) == base


def test_digest_sources_preserves_order_yahoo_first():
    base = ["yahoo", "fmp", "finnhub"]
    assert daily.digest_sources(base, include_fmp=False)[0] == "yahoo"


def test_fmp_rationed_note_constant_exists():
    assert daily.FMP_RATIONED_NOTE == "Free-source screen — /deep for PEG + analyst targets."

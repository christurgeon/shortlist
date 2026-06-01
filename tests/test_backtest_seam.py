from shortlist.data.sources import snapshot_from_closes


def test_snapshot_from_closes_builds_price_pointintime():
    closes = [100.0] * 199 + [110.0]          # 200 points
    spy = [100.0] * 199 + [105.0]
    snap = snapshot_from_closes("AAA", closes, spy)
    assert snap.ticker == "AAA"
    assert snap.price is not None
    assert snap.price.price == 110.0
    assert snap.price.ma200 is not None        # 200 closes -> SMA defined


def test_snapshot_from_closes_short_series_ma200_none():
    snap = snapshot_from_closes("AAA", [100.0, 101.0, 102.0], [100.0, 100.0, 100.0])
    assert snap.price.price == 102.0
    assert snap.price.ma200 is None            # < 200 closes

from datetime import date
from shortlist.edgar.symbology import parse_cdx, nearest_snapshot_before

_HEADER = ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"]


def _row(ts, status, mimetype="application/json"):
    return ["k", ts, "https://www.sec.gov/files/company_tickers.json", mimetype, status, "d", "1"]


def test_parse_cdx_keeps_only_200_sorted():
    rows = [_HEADER,
            _row("20230628152805", "200"),
            _row("20191002224708", "200"),
            _row("20231201000000", "301"),
            _row("20240115000000", "-", "warc/revisit")]
    out = parse_cdx(rows)
    assert [ts for ts, _ in out] == ["20191002224708", "20230628152805"]  # sorted, 200-only
    assert out[0][1] == date(2019, 10, 2)


def test_nearest_snapshot_before_picks_latest_le_target():
    snaps = parse_cdx([_HEADER, _row("20220101000000", "200"),
                       _row("20221003133031", "200"), _row("20240101000000", "200")])
    # 13D event on 2022-11-15 -> nearest <= is the Oct 2022 snapshot
    assert nearest_snapshot_before(snaps, date(2022, 11, 15)) == "20221003133031"
    # target before the first snapshot -> None (no coverage)
    assert nearest_snapshot_before(snaps, date(2020, 1, 1)) is None
    assert nearest_snapshot_before(snaps, date(2019, 1, 1)) is None


def test_parse_cdx_empty_or_headeronly():
    assert parse_cdx([]) == []
    assert parse_cdx([_HEADER]) == []

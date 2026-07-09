"""CUSIP -> ticker resolver leaf (scout/cusip_map.py): FTD parse, most-recent-settlement
disambiguation, layered fallback order, exact-normalized-name match + near-miss abstention,
and the walk-back FTD fetch (offline)."""
import io
import zipfile
from datetime import date

from shortlist.scout.cusip_map import (
    CusipResolver,
    build_cusip_to_symbol,
    build_name_to_ticker,
    fetch_ftd_files,
    normalize_issuer_name,
    parse_ftd_text,
    parse_ftd_zip,
)

_FTD_HEADER = "SETTLEMENT DATE|CUSIP|SYMBOL|QUANTITY (FAILS)|DESCRIPTION|PRICE"


def _ftd(*rows: str) -> str:
    return "\n".join([_FTD_HEADER, *rows])


def _zip_bytes(text: str, name: str = "cnsfails.txt") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, text)
    return buf.getvalue()


def test_parse_ftd_skips_header_and_short_rows():
    rows = parse_ftd_text(_ftd("20260601|02005N100|ALLY|9|ALLY FINL|30.0", "", "garbage"))
    assert rows == [{"settlement": "20260601", "cusip": "02005N100", "symbol": "ALLY"}]


def test_build_cusip_to_symbol_most_recent_settlement_wins():
    # Same CUSIP maps to two symbols across rows (symbol churn) — the later settlement wins.
    rows = parse_ftd_text(_ftd("20260601|12345X678|OLD|1|X|1",
                               "20260615|12345X678|NEW|1|X|1"))
    assert build_cusip_to_symbol([rows]) == {"12345X678": "NEW"}
    # Order-independent (a stale row later in the file must not clobber the newer symbol).
    rows2 = parse_ftd_text(_ftd("20260615|12345X678|NEW|1|X|1",
                                "20260601|12345X678|OLD|1|X|1"))
    assert build_cusip_to_symbol([rows2]) == {"12345X678": "NEW"}


def test_parse_ftd_zip_roundtrip():
    raw = _zip_bytes(_ftd("20260601|02005N100|ALLY|9|ALLY FINL|30.0"))
    assert parse_ftd_zip(raw) == [{"settlement": "20260601", "cusip": "02005N100", "symbol": "ALLY"}]
    assert parse_ftd_zip(b"not a zip") == []          # corrupt archive -> [], never raises


def test_normalize_issuer_name_strips_suffixes_and_punctuation():
    assert normalize_issuer_name("Apple Inc.") == "APPLE"
    assert normalize_issuer_name("The Kraft Heinz Company") == "KRAFT HEINZ"
    assert normalize_issuer_name("Berkshire Hathaway Inc") == "BERKSHIRE HATHAWAY"
    assert normalize_issuer_name("") == ""


def test_name_to_ticker_drops_ambiguous_and_keeps_unique():
    raw = {
        "0": {"cik_str": 1, "ticker": "AAA", "title": "Alpha Inc"},
        "1": {"cik_str": 2, "ticker": "BBB", "title": "Beta Corp"},
        "2": {"cik_str": 3, "ticker": "CCC", "title": "Beta Co"},   # collides w/ Beta Corp -> BETA
    }
    idx = build_name_to_ticker(raw)
    assert idx["ALPHA"] == "AAA"
    assert "BETA" not in idx                            # ambiguous normalized name -> abstain


def test_resolver_layered_order_and_near_miss_abstention():
    cusip_idx = {"02005N100": "ALLY"}
    name_idx = build_name_to_ticker({"0": {"cik_str": 1, "ticker": "AAPL", "title": "Apple Inc"}})
    r = CusipResolver(cusip_idx, name_idx)
    # 1) CUSIP hit wins.
    assert r.resolve("02005N100", "ALLY FINL INC") == "ALLY"
    # 2) CUSIP miss -> exact normalized name fallback.
    assert r.resolve("99999X999", "Apple Inc.") == "AAPL"
    # 3) A NEAR MISS name must NOT match (conservative — abstain, never fuzzy).
    assert r.resolve("99999X999", "Apple Hospitality") is None
    assert r.resolve("99999X999", "Unknown Widgets LLC") is None


def test_fetch_ftd_walks_back_until_two_files(tmp_path):
    calls = []
    published = {
        "https://www.sec.gov/files/data/fails-deliver-data/cnsfails202606a.zip":
            _zip_bytes(_ftd("20260601|02005N100|ALLY|9|X|1")),
        "https://www.sec.gov/files/data/fails-deliver-data/cnsfails202605b.zip":
            _zip_bytes(_ftd("20260520|12345X678|OLD|1|X|1")),
    }

    def fake_get(url, identity, timeout):
        calls.append(url)
        return published.get(url)   # None (404) for the not-yet-published current period

    # today in the 202606 'b' half — 202606b 404s, walk back to 202606a then 202605b.
    files = fetch_ftd_files("me@x.com", cache_dir=str(tmp_path), today=date(2026, 6, 20),
                            _http_get=fake_get)
    assert len(files) == 2                                # stopped once two succeeded
    idx = build_cusip_to_symbol(files)
    assert idx["02005N100"] == "ALLY" and idx["12345X678"] == "OLD"
    assert calls[0].endswith("cnsfails202606b.zip")      # newest-first, 'b' before 'a'


def test_fetch_ftd_caches_forever_by_filename(tmp_path):
    hits = []

    def fake_get(url, identity, timeout):
        hits.append(url)
        return _zip_bytes(_ftd("20260601|02005N100|ALLY|9|X|1"))

    kw = dict(cache_dir=str(tmp_path), today=date(2026, 6, 20), want=1, _http_get=fake_get)
    fetch_ftd_files("me@x.com", **kw)
    n = len(hits)
    fetch_ftd_files("me@x.com", **kw)                    # second run: served from disk cache
    assert len(hits) == n                                # no additional network calls

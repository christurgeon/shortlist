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


def test_name_to_ticker_same_cik_dualclass_keeps_first_crosscik_drops():
    """A same-CIK dual-class issuer (GOOGL/GOOG, BRK-A/B) shares a normalized name but keeps
    the first-occurrence ticker; only a CROSS-CIK name collision (two issuers) abstains."""
    raw = {
        # same CIK 1652044, both normalize to ALPHABET -> keep first (GOOGL)
        "0": {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet Inc."},
        "1": {"cik_str": 1652044, "ticker": "GOOG", "title": "Alphabet Inc."},
        # same CIK 1067983, both normalize to BERKSHIRE HATHAWAY -> keep first (BRK-A)
        "2": {"cik_str": 1067983, "ticker": "BRK-A", "title": "Berkshire Hathaway Inc"},
        "3": {"cik_str": 1067983, "ticker": "BRK-B", "title": "Berkshire Hathaway Inc"},
        # DIFFERENT CIKs collapsing to DELTA -> ambiguous, dropped
        "4": {"cik_str": 27904, "ticker": "DAL", "title": "Delta Corp"},
        "5": {"cik_str": 88888, "ticker": "DEL", "title": "Delta Co"},
    }
    idx = build_name_to_ticker(raw)
    assert idx["ALPHABET"] == "GOOGL"                   # first-occurrence per CIK
    assert idx["BERKSHIRE HATHAWAY"] == "BRK-A"
    assert "DELTA" not in idx                            # cross-CIK collision abstains


def test_name_to_ticker_absent_cik_is_never_same_cik():
    """Two DIFFERENT issuers whose normalized names collide AND whose cik_str is falsy/absent
    must be AMBIGUOUS (dropped) — a falsy cik must not compare equal to another falsy cik and
    hand the first issuer's ticker to the second (a wrong-ticker guess)."""
    raw = {
        "0": {"cik_str": None, "ticker": "AAA", "title": "Omega Inc"},     # cik absent
        "1": {"cik_str": 0, "ticker": "BBB", "title": "Omega Corp"},       # cik falsy (0)
    }
    assert "OMEGA" not in build_name_to_ticker(raw)      # ambiguous -> abstain, never guess
    # a genuine same-CIK dual class (both truthy + equal) still keeps the first ticker
    raw_ok = {
        "0": {"cik_str": 5, "ticker": "AAA", "title": "Omega Inc"},
        "1": {"cik_str": 5, "ticker": "AAB", "title": "Omega Corp"},
    }
    assert build_name_to_ticker(raw_ok)["OMEGA"] == "AAA"


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


def test_fetch_ftd_empty_parse_writes_marker_and_walk_back_continues(tmp_path):
    """An empty parse (truncated zip / HTML body -> parse_ftd_zip returns []) is a fetch
    failure: a dated empty-MARKER is written (NOT the immutable rows key, so it doesn't
    poison the half-month forever) and it does NOT count toward `want` — the walk-back
    continues to the next older file."""
    import json
    empty_zip = _zip_bytes("SETTLEMENT DATE|CUSIP|SYMBOL\n")   # header only -> [] rows
    good_zip = _zip_bytes(_ftd("20260520|12345X678|OLD|1|X|1"))
    published = {
        "https://www.sec.gov/files/data/fails-deliver-data/cnsfails202606a.zip": empty_zip,
        "https://www.sec.gov/files/data/fails-deliver-data/cnsfails202605b.zip": good_zip,
    }

    def fake_get(url, identity, timeout):
        return published.get(url)

    files = fetch_ftd_files("me@x.com", cache_dir=str(tmp_path), today=date(2026, 6, 20),
                            want=1, _http_get=fake_get)
    assert len(files) == 1                               # the empty parse did not count
    assert build_cusip_to_symbol(files)["12345X678"] == "OLD"  # walked back to the good file
    # the empty-parse period's cache is a MARKER, not rows (not the immutable-rows key)
    marker = json.loads((tmp_path / "cnsfails202606a.json").read_text())
    assert marker == {"empty_on": "2026-06-20"}
    assert (tmp_path / "cnsfails202605b.json").exists()  # the good one IS cached (as rows)


def test_fetch_ftd_fresh_empty_marker_suppresses_download_then_retries(tmp_path):
    """A fresh empty-marker (< 7d) SKIPS the file with NO download (bounded backoff, counts as
    a failed attempt); a stale marker (>= 7d) refetches — the file may be re-posted intact."""
    import json
    # the walk-back's FIRST candidate for June is the second-half 'b' file.
    url_b = "https://www.sec.gov/files/data/fails-deliver-data/cnsfails202606b.zip"
    cp = tmp_path / "cnsfails202606b.json"
    cp.write_text(json.dumps({"empty_on": "2026-06-18"}))   # marker written 2 days ago
    hits = []

    def fake_get(url, identity, timeout):
        hits.append(url)
        return _zip_bytes(_ftd("20260601|02005N100|ALLY|9|X|1")) if url == url_b else None

    # today 2026-06-20: marker is 2d old (< 7) -> 202606b is skipped WITHOUT a download.
    fetch_ftd_files("me@x.com", cache_dir=str(tmp_path), today=date(2026, 6, 20),
                    want=1, max_attempts=1, _http_get=fake_get)
    assert url_b not in hits                              # fresh marker -> no download

    # 8 days later the marker is stale (>= 7) -> 202606b IS refetched (now parses to rows).
    files = fetch_ftd_files("me@x.com", cache_dir=str(tmp_path), today=date(2026, 6, 26),
                            want=1, max_attempts=1, _http_get=fake_get)
    assert url_b in hits
    assert build_cusip_to_symbol(files)["02005N100"] == "ALLY"
    assert json.loads(cp.read_text()) == [{"settlement": "20260601", "cusip": "02005N100",
                                           "symbol": "ALLY"}]  # marker overwritten by rows


def test_fetch_ftd_legacy_empty_list_cache_is_healed_by_refetch(tmp_path):
    """A pre-fix poisoned cache (a bare `[]`, no marker) is treated as a MISS and refetched —
    the legacy empty file can no longer freeze the half-month forever."""
    import json
    url_b = "https://www.sec.gov/files/data/fails-deliver-data/cnsfails202606b.zip"
    cp = tmp_path / "cnsfails202606b.json"
    cp.write_text(json.dumps([]))                        # legacy poisoned empty-list cache
    hits = []

    def fake_get(url, identity, timeout):
        hits.append(url)
        return _zip_bytes(_ftd("20260601|02005N100|ALLY|9|X|1")) if url == url_b else None

    files = fetch_ftd_files("me@x.com", cache_dir=str(tmp_path), today=date(2026, 6, 20),
                            want=1, max_attempts=1, _http_get=fake_get)
    assert url_b in hits                                 # legacy [] refetched (healed)
    assert build_cusip_to_symbol(files)["02005N100"] == "ALLY"


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

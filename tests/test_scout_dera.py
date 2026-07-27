import zipfile
from datetime import date
from pathlib import Path

from shortlist.scout import dera
from shortlist.scout.dera import dera_zip_url, load_index, parse_dera_tsvs, quarters_back
from shortlist.scout.insider import parse_form4_xml

FIX = Path(__file__).parent / "fixtures" / "form4"
SAMPLE = FIX / "dera_2025q1_sample"


def _dera():
    with (SAMPLE / "SUBMISSION.tsv").open() as s, \
         (SAMPLE / "REPORTINGOWNER.tsv").open() as o, \
         (SAMPLE / "NONDERIV_TRANS.tsv").open() as t:
        return parse_dera_tsvs(s, o, t)


def test_dera_url_shape():
    assert dera_zip_url("2025q1").endswith(
        "/insider-transactions-data-sets/2025q1_form345.zip")


def test_parses_dera_ddmonyyyy_dates_and_flags():
    buys = [t for t in _dera() if t.code == "P"]
    assert len(buys) == 1
    t = buys[0]
    assert t.date == date(2025, 3, 27)      # from "27-MAR-2025", NOT ISO
    assert t.plan_10b5_1 is False           # AFF10B5ONE is "0"
    assert "director" in t.roles            # from "Director" comma-joined string


def test_live_and_history_agree_on_the_same_filing():
    """THE guard: one real filing, both paths, same record.

    The encodings genuinely differ -- DERA has 27-MAR-2025 / '0' / 'Director', the XML
    has 2025-03-27 / '0' or 'false' / <isDirector> -- so this is not trivially true. It
    is the defence against live-vs-history definitional drift, the failure mode that
    broke the accruals leg.

    Exact equality on the CATEGORICAL fields: those are what drift corrupts (wrong
    column, wrong encoding, wrong sign). PRICE is compared with a tolerance because
    **DERA rounds TRANS_PRICEPERSHARE to 2dp while the XML carries full precision** --
    24.57 vs 24.5686 on this very filing (live-verified 2026-07-26).

    Do NOT "tighten" this to `==`: it will be permanently red. And do NOT normalise the
    XML down to 2dp to make it pass -- that discards real precision from the live path
    to satisfy a test. The rounding is immaterial to a $100k floor.
    """
    xml_t = [t for t in parse_form4_xml(
        (FIX / "oklo_0001104659-25-030072.xml").read_text(errors="replace"))
        if t.code == "P"][0]
    dera_t = [t for t in _dera() if t.code == "P"][0]

    for field in ("owner_cik", "ticker", "date", "code", "roles", "title", "plan_10b5_1"):
        assert getattr(xml_t, field) == getattr(dera_t, field), field
    # issuer_cik: both sides already come formatted zero-padded to 10 digits on this
    # fixture, but canonicalize with .strip().zfill(10) on both sides anyway -- the
    # same defensive join-key normalization build_trade_month_index/classify_tier use
    # for owner_cik -- so this guard doesn't silently pass if a future fixture (or a
    # real filing) supplies an unpadded CIK on one side only.
    assert xml_t.issuer_cik.strip().zfill(10) == dera_t.issuer_cik.strip().zfill(10)
    assert xml_t.shares == dera_t.shares
    assert abs(xml_t.price - dera_t.price) < 0.01          # DERA 2dp rounding only
    assert abs(xml_t.value - dera_t.value) / dera_t.value < 1e-3


def test_quarters_back_walks_backwards_from_the_previous_quarter():
    assert quarters_back(date(2026, 7, 26), 3) == ["2026q2", "2026q1", "2025q4"]


def _sample_zip(tmp_path: Path) -> Path:
    """Zip the committed sample TSVs into a one-quarter DERA-shaped ZIP."""
    p = tmp_path / "2025q1_form345.zip"
    with zipfile.ZipFile(p, "w") as z:
        for name in ("SUBMISSION.tsv", "REPORTINGOWNER.tsv", "NONDERIV_TRANS.tsv"):
            z.write(SAMPLE / name, arcname=name)
    return p


def test_load_index_round_trips_through_its_json_cache(tmp_path, monkeypatch):
    """Second call must hit the cache and skip re-parsing the ZIP (the expensive step).

    `ensure_quarters` is still called every time (cheap -- it's a filesystem existence
    check per quarter, no network for an already-downloaded ZIP; C-1's fix needs it to
    learn which quarters currently contribute before it can pick the right cache key), but
    `_index_from_zip` -- the actual TSV parse -- must NOT run twice."""
    zip_path = _sample_zip(tmp_path)
    parse_calls = []
    real_index_from_zip = dera._index_from_zip

    def spy_index_from_zip(path):
        parse_calls.append(path)
        return real_index_from_zip(path)

    monkeypatch.setattr("shortlist.scout.dera.ensure_quarters",
                        lambda quarters, cache_dir, identity="x": [zip_path])
    monkeypatch.setattr("shortlist.scout.dera._index_from_zip", spy_index_from_zip)

    cache_dir = str(tmp_path / "cache")
    first, n_missing = load_index(cache_dir, ["2025q1"])
    assert len(parse_calls) == 1
    assert first == {"0002021774": {(2025, 3)}}
    assert n_missing == 0

    second, n_missing2 = load_index(cache_dir, ["2025q1"])
    assert second == first
    assert n_missing2 == 0
    assert len(parse_calls) == 1  # cache hit -- ZIP not re-parsed
    assert (Path(cache_dir) / "index-2025q1.json").exists()


def test_load_index_never_caches_a_partial_result_and_retries_next_call(tmp_path, monkeypatch):
    """C-1 regression: when fewer quarters contribute than requested (a download blip, or a
    quarter simply not yet published), the index must NOT be persisted -- a permanently
    poisoned {} (or partial) cache with zero retries is exactly the bug this guards against.
    """
    calls = []

    def fake_ensure_quarters(quarters, cache_dir, identity="x"):
        calls.append(list(quarters))
        return []   # every requested quarter "failed" (blip, or unpublished)

    monkeypatch.setattr("shortlist.scout.dera.ensure_quarters", fake_ensure_quarters)
    cache_dir = str(tmp_path / "cache")

    first, n_missing = load_index(cache_dir, ["2025q1", "2025q2"])
    assert first == {}
    assert n_missing == 2
    assert list(Path(cache_dir).glob("index-*.json")) == []   # nothing written

    second, n_missing2 = load_index(cache_dir, ["2025q1", "2025q2"])
    assert n_missing2 == 2
    assert len(calls) == 2   # retried on the second call -- NOT short-circuited by a cache


def test_load_index_unlinks_a_corrupt_cache_and_rebuilds(tmp_path, monkeypatch):
    """I-3: a truncated/corrupt cache file must not be re-served forever -- a decode failure
    unlinks it and falls through to a normal rebuild."""
    import json as _json

    zip_path = _sample_zip(tmp_path)
    calls = []

    def fake_ensure_quarters(quarters, cache_dir, identity="x"):
        calls.append(list(quarters))
        return [zip_path]

    monkeypatch.setattr("shortlist.scout.dera.ensure_quarters", fake_ensure_quarters)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    corrupt = cache_dir / "index-2025q1.json"
    corrupt.write_text("{not valid json")

    idx, n_missing = load_index(str(cache_dir), ["2025q1"])
    assert n_missing == 0
    assert idx == {"0002021774": {(2025, 3)}}
    assert len(calls) == 1
    _json.loads(corrupt.read_text())   # rebuilt cache is valid JSON, not the corrupt text

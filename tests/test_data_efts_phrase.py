"""Offline tests for the EFTS exact-phrase (`q`) extension (data/efts.py) + the mandatory
byte-identical regression: the no-`q` item-query params the three existing 8-K consumers ride
must be UNCHANGED by the phrase addition. All HTTP is injected via the `_get` seam."""
import json
from datetime import date

from pathlib import Path

from shortlist.data import efts
from shortlist.data.efts import (BUYBACK_CACHE_DIR, _phrase_q, _phrase_subdir,
                                 fetch_eightk_range, fetch_eightk_window, fetch_phrase_day,
                                 fetch_phrase_window)


def _hit(adsh, cik="0000320193", items=("8.01",), file_date="2026-06-03",
         file_type="8-K", sics=("3571",), names=("Real Co (RCO)",)):
    return {"_id": f"{adsh}:doc.htm",
            "_source": {"adsh": adsh, "ciks": [cik], "items": list(items),
                        "file_date": file_date, "file_type": file_type,
                        "root_forms": ["8-K"], "sics": list(sics),
                        "display_names": list(names)}}


def _payload(hits, total=None):
    return {"hits": {"total": {"value": total if total is not None else len(hits)},
                     "hits": hits}}


class _ScriptedGet:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, params):
        self.calls.append(dict(params))
        return self.responses.pop(0)


# --- the load-bearing regression: no-q params are FROZEN byte-for-byte -------------------

_FROZEN_NO_Q_PARAMS = {"forms": "8-K", "dateRange": "custom",
                       "startdt": "2026-06-03", "enddt": "2026-06-03",
                       "from": 0, "size": 100}


def test_no_q_request_params_are_byte_identical():
    """The 8-K originator / veto sweep / 8-K backfill all ride the q=None path; its request
    params must be EXACTLY the frozen dict (no stray `q` key) after the phrase extension."""
    get = _ScriptedGet([(200, _payload([_hit("a-1")]))])
    fetch_eightk_range(date(2026, 6, 3), date(2026, 6, 3), identity="t@e.co",
                       throttle_s=0, _get=get)
    assert get.calls == [_FROZEN_NO_Q_PARAMS]
    assert "q" not in get.calls[0]


def test_phrase_q_adds_quoted_q_key_only():
    get = _ScriptedGet([(200, _payload([_hit("a-1")]))])
    fetch_eightk_range(date(2026, 6, 3), date(2026, 6, 3), identity="t@e.co",
                       throttle_s=0, q="approved a new share repurchase program", _get=get)
    assert get.calls[0]["q"] == '"approved a new share repurchase program"'
    # every other key is unchanged from the frozen item-query params
    assert {k: v for k, v in get.calls[0].items() if k != "q"} == _FROZEN_NO_Q_PARAMS


def test_phrase_q_helper():
    assert _phrase_q("share repurchase program") == '"share repurchase program"'


def test_phrase_q_threads_through_pagination_split():
    """q must ride EVERY page + every recursive split half, not just the first probe."""
    left = [_hit(f"l-{i}", file_date="2026-06-01") for i in range(2)]
    right = [_hit(f"r-{i}", file_date="2026-06-03") for i in range(3)]
    get = _ScriptedGet([(200, _payload([], total=9_900)),
                        (200, _payload(left, total=2)),
                        (200, _payload(right, total=3))])
    rows = fetch_eightk_range(date(2026, 6, 1), date(2026, 6, 4), identity="t@e.co",
                              throttle_s=0, q="buyback", _get=get)
    assert len(rows) == 5
    assert all(c["q"] == '"buyback"' for c in get.calls)


# --- phrase day cache: own namespace, complete/unfiltered, phrase-hash keyed --------------

def test_phrase_day_cache_namespace_and_complete_unfiltered(tmp_path):
    """The phrase day cache writes the COMPLETE unfiltered payload (8-K/A included) under a
    phrase-hash subdir, isolated from the item-query .cache/efts day cache."""
    hits = [_hit("a-1", file_type="8-K"), _hit("a-2", file_type="8-K/A")]
    get = _ScriptedGet([(200, _payload(hits))])
    phrase = "authorized a share repurchase program"
    rows = fetch_phrase_day(phrase, date(2026, 6, 3), identity="t@e.co",
                            cache_dir=str(tmp_path), today=date(2026, 6, 10),
                            throttle_s=0, _get=get)
    assert [r["file_type"] for r in rows] == ["8-K", "8-K/A"]   # unfiltered
    sub = _phrase_subdir(str(tmp_path), phrase)
    env = json.loads((__import__("pathlib").Path(sub) / "2026-06-03.json").read_text())
    assert [r["file_type"] for r in env["rows"]] == ["8-K", "8-K/A"]
    # a DIFFERENT phrase hashes to a different subdir (no pooling)
    assert _phrase_subdir(str(tmp_path), phrase) != _phrase_subdir(str(tmp_path), "other phrase")


def test_phrase_day_cache_idempotent_second_call_zero_fetches(tmp_path):
    get = _ScriptedGet([(200, _payload([_hit("a-1")]))])
    kw = dict(identity="t@e.co", cache_dir=str(tmp_path), today=date(2026, 6, 10), throttle_s=0)
    fetch_phrase_day("p", date(2026, 6, 3), _get=get, **kw)
    poison = _ScriptedGet([])
    rows2 = fetch_phrase_day("p", date(2026, 6, 3), _get=poison, **kw)
    assert len(rows2) == 1 and poison.calls == []


def test_window_q_redirects_to_phrase_subdir_never_shared_item_cache(tmp_path):
    """Structural guard: fetch_eightk_window with `q` set writes its day cache under the
    phrase-hash subdir, NEVER the shared item-query day cache (`cache_dir` root) — so a
    caller that forgot to namespace can't pool phrase-filtered rows into .cache/efts."""
    get = _ScriptedGet([(200, _payload([_hit("a-1")]))])
    phrase = "authorized a share repurchase program"
    rows = fetch_eightk_window(date(2026, 6, 3), date(2026, 6, 3), identity="t@e.co",
                               cache_dir=str(tmp_path), today=date(2026, 6, 10),
                               q=phrase, throttle_s=0, _get=get)
    assert [r["adsh"] for r in rows] == ["a-1"]
    sub = _phrase_subdir(str(tmp_path), phrase)
    assert (Path(sub) / "2026-06-03.json").exists()      # written under the phrase namespace
    assert not (Path(str(tmp_path)) / "2026-06-03.json").exists()  # item cache root untouched


def test_default_buyback_cache_dir_constant():
    assert BUYBACK_CACHE_DIR == ".cache/efts_buyback"


# --- phrase window (backfill): loops phrases, tags rows, merges ---------------------------

def test_phrase_window_tags_and_merges(tmp_path):
    p1_hits = [_hit("a-1", file_date="2026-06-01")]
    p2_hits = [_hit("b-1", file_date="2026-06-02")]
    get = _ScriptedGet([(200, _payload(p1_hits)), (200, _payload(p2_hits))])
    rows = fetch_phrase_window(["phrase one", "phrase two"],
                               date(2026, 6, 1), date(2026, 6, 2), identity="t@e.co",
                               cache_dir=str(tmp_path), today=date(2026, 7, 1),
                               throttle_s=0, _get=get)
    assert {r["adsh"]: r["phrase"] for r in rows} == {"a-1": "phrase one", "b-1": "phrase two"}
    assert all(c["q"].startswith('"phrase') for c in get.calls)


def test_phrase_window_failed_phrase_returns_none(tmp_path):
    get = _ScriptedGet([(500, None), (500, None), (500, None)])
    rows = fetch_phrase_window(["only"], date(2026, 6, 1), date(2026, 6, 2),
                               identity="t@e.co", cache_dir=str(tmp_path),
                               today=date(2026, 7, 1), throttle_s=0, _get=get)
    assert rows is None

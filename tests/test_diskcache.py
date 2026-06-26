"""Behavior contract for the shared JSON disk-cache helpers (data/diskcache.py).

These pin the exact semantics the five data-layer cache sites relied on before the
dedup (YahooSource._get_chart, FinraSource, GovContractsSource, LobbyingSource,
apewisdom.fetch_wsb_mentions): a missing OR unreadable file reads as a miss (None),
a falsy-but-present payload ([]/{}) reads back as itself (NOT a miss), and a write
is best-effort — it creates parent dirs and never raises.
"""

import json
from pathlib import Path

import pytest

from shortlist.data.diskcache import read_json_cache, write_json_cache


def test_round_trips_a_written_value(tmp_path):
    cp = tmp_path / "x.json"
    write_json_cache(cp, {"a": 1, "b": [2, 3]})
    assert read_json_cache(cp) == {"a": 1, "b": [2, 3]}


def test_missing_file_reads_as_none(tmp_path):
    assert read_json_cache(tmp_path / "absent.json") is None


def test_corrupt_file_reads_as_none(tmp_path):
    cp = tmp_path / "corrupt.json"
    cp.write_text("{not valid json")
    assert read_json_cache(cp) is None


def test_present_empty_collections_are_not_a_miss(tmp_path):
    """A cached [] or {} must read back as itself, not None — FinraSource caches a
    list and distinguishes 'cached empty result' from 'cache miss -> refetch'."""
    lst = tmp_path / "list.json"
    write_json_cache(lst, [])
    assert read_json_cache(lst) == []

    dct = tmp_path / "dict.json"
    write_json_cache(dct, {})
    assert read_json_cache(dct) == {}


def test_write_creates_parent_dirs(tmp_path):
    cp = tmp_path / "nested" / "deep" / "x.json"
    write_json_cache(cp, {"ok": True})
    assert cp.exists()
    assert json.loads(cp.read_text()) == {"ok": True}


def test_write_swallows_errors(tmp_path):
    """A write failure is non-fatal: parent path is a file, so mkdir(parents=True)
    raises internally — the helper must swallow it and leave nothing readable."""
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a dir")
    cp = blocker / "sub.json"
    write_json_cache(cp, {"v": 1})  # must not raise
    assert read_json_cache(cp) is None

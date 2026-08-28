"""`_fetch_sections` (data/sources/base.py): sections must fetch CONCURRENTLY, not
one-at-a-time — FMP's fetch() passes 12-13 independent sections through this per
ticker, so a sequential loop multiplies the dominant source's latency."""
import asyncio

from shortlist.data.models import SourceResult
from shortlist.data.sources.base import _fetch_sections


def test_fetch_sections_runs_concurrently():
    async def get(path, **params):
        await asyncio.sleep(0.05)
        return path

    async def run():
        res = SourceResult(source="x")
        sections = {f"s{i}": (f"path{i}", {}) for i in range(5)}
        t0 = asyncio.get_event_loop().time()
        await _fetch_sections(res, get, sections)
        return asyncio.get_event_loop().time() - t0, res

    dur, res = asyncio.run(run())
    # Sequential worst case is 5 * 0.05s = 0.25s; concurrent should land near 0.05s.
    assert dur < 0.15
    assert res.raw == {f"s{i}": f"path{i}" for i in range(5)}


def test_fetch_sections_isolates_one_failure_from_the_rest():
    async def get(path, **params):
        if path == "bad":
            raise RuntimeError("boom")
        return path

    async def run():
        res = SourceResult(source="x")
        sections = {"a": ("good", {}), "b": ("bad", {})}
        await _fetch_sections(res, get, sections)
        return res

    res = asyncio.run(run())
    assert res.raw == {"a": "good"}
    assert len(res.errors) == 1 and "boom" in res.errors[0]
    assert res.errors[0].startswith("x.b:")

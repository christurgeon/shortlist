import pytest

from shortlist import cache as cache_mod


@pytest.fixture(autouse=True)
def _isolate_http_cache(tmp_path, monkeypatch):
    """Keep the on-by-default global cache out of the real repo-root .cache/ and stop
    cached rows leaking between tests (which would break call-count assertions). The
    global is disabled before each test, and the DEFAULT path points into tmp so an
    entrypoint that re-enables the cache with the default path never writes the repo.
    Tests that want a real cache build their own HttpCache(tmp_path) or call
    configure_default_cache(..., path=tmp) explicitly."""
    monkeypatch.setattr(cache_mod, "_DEFAULT_PATH", str(tmp_path / "http.sqlite"))
    cache_mod.reset_default_cache()
    cache_mod.configure_default_cache(enabled=False)
    yield
    cache_mod.reset_default_cache()

import pytest

from shortlist import cache as cache_mod


@pytest.fixture(autouse=True)
def _isolate_http_cache(tmp_path, monkeypatch):
    """Keep the on-by-default global cache out of the real repo-root .cache/ during
    tests and prevent cached rows leaking between tests (which would break call-count
    assertions). Two defences: (1) disable the global before each test; (2) redirect
    the DEFAULT cache path into tmp, so any entrypoint under test that re-enables the
    cache with the default path (screener/scout main) writes to tmp, never the repo.
    Tests that want a real cache build their own HttpCache(tmp_path) or call
    configure_default_cache(..., path=tmp) explicitly."""
    monkeypatch.setattr(cache_mod, "_DEFAULT_PATH", str(tmp_path / "http.sqlite"))
    cache_mod.reset_default_cache()
    cache_mod.configure_default_cache(enabled=False)
    yield
    cache_mod.reset_default_cache()

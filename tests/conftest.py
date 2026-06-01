import pytest

from shortlist import cache as cache_mod


@pytest.fixture(autouse=True)
def _isolate_http_cache():
    """Keep the on-by-default global cache out of the real repo-root .cache/ during
    tests and prevent cached rows leaking between tests (which would break call-count
    assertions). Tests that want a real cache build their own HttpCache(tmp_path), or
    call configure_default_cache(..., path=tmp) explicitly for their body."""
    cache_mod.reset_default_cache()
    cache_mod.configure_default_cache(enabled=False)
    yield
    cache_mod.reset_default_cache()

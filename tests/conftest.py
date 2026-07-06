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


@pytest.fixture(scope="session", autouse=True)
def _repo_artifact_tripwire():
    """Fail the session if any test writes the REAL repo's scout/validate-latest.json.
    _run_validate_cli persists it relative to CWD; a test invoking the CLI without
    monkeypatch.chdir(tmp_path) silently clobbers the production artifact (happened
    2026-07-06 — a test fixture overwrote the real 13D verdict envelope)."""
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "scout" / "validate-latest.json"
    before = p.stat().st_mtime_ns if p.exists() else None
    yield
    after = p.stat().st_mtime_ns if p.exists() else None
    assert before == after, (
        "a test wrote the real scout/validate-latest.json — isolate its CWD "
        "(monkeypatch.chdir(tmp_path)) before calling the validate CLI")

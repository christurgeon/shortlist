import os
import subprocess
from datetime import date

from shortlist.scout.preregister import load_prereg, verify_untampered


def test_load_prereg_reads_committed_yaml():
    p = load_prereg("edgar_activist_13d", repo_root=".")
    assert p["k_months"] == 12
    assert p["factor_model"] == "ff3"
    assert 0.0 < p["min_measurable_frac"] <= 1.0


def _init_repo_with_prereg(tmp_path, slug="fake_signal", commit_date="2026-01-01T12:00:00+00:00"):
    """Self-contained scratch git repo (NOT the real shortlist repo) so the tamper-check
    tests control the commit time exactly, rather than depending on when this task's own
    files happen to land in the real repo's history."""
    repo = tmp_path / "repo"
    prereg_dir = repo / "src" / "shortlist" / "scout" / "preregister"
    prereg_dir.mkdir(parents=True)
    (prereg_dir / f"{slug}.yaml").write_text("k_months: 12\nfactor_model: ff3\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    env = dict(os.environ, GIT_AUTHOR_DATE=commit_date, GIT_COMMITTER_DATE=commit_date)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True, env=env)
    return repo


def test_verify_untampered_ok_when_run_as_of_after_commit(tmp_path):
    repo = _init_repo_with_prereg(tmp_path, commit_date="2026-01-01T12:00:00+00:00")
    ok, reason = verify_untampered("fake_signal", repo_root=str(repo), run_as_of=date(2026, 6, 1))
    assert ok is True
    assert reason == "ok"


def test_verify_untampered_flags_when_run_as_of_before_commit(tmp_path):
    repo = _init_repo_with_prereg(tmp_path, commit_date="2026-06-01T12:00:00+00:00")
    ok, reason = verify_untampered("fake_signal", repo_root=str(repo), run_as_of=date(2026, 1, 1))
    assert ok is False
    assert "after" in reason.lower()
    assert "not pre-registered" in reason.lower()


def test_verify_untampered_reports_reason_when_file_never_committed(tmp_path):
    repo = _init_repo_with_prereg(tmp_path)
    ok, reason = verify_untampered("does_not_exist_signal", repo_root=str(repo), run_as_of=date.today())
    assert ok is False
    assert "not committed" in reason.lower()

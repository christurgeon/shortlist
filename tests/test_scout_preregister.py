import os
import subprocess
from datetime import date

from shortlist.scout.preregister import load_prereg, verify_untampered


def test_load_prereg_reads_committed_yaml():
    p = load_prereg("edgar_activist_13d", repo_root=".")
    assert p["k_months"] == 12
    assert p["factor_model"] == "ff3"
    assert 0.0 < p["min_measurable_frac"] <= 1.0
    # I1 (v2 design): the canonical run-date field, re-registered alongside the as_of bump
    # in the same commit -- window_end (2025-12-31) + K=12m.
    assert str(p["verdict_as_of"]) == "2026-12-31"


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


# --- Content-based tamper gate (docs/EVALUATOR_CORRECTNESS.md §1) ------------------------
#
# Two defects motivated these: (A-1) load_prereg read the WORKING TREE while
# verify_untampered only checked the path's last commit time, so an uncommitted edit to a
# pre-registered threshold was invisible; (A-2) the age check lacked --follow, so a `git mv`
# reset the registration clock. The fix reads `git show HEAD:<path>` and dates the CONTENT.

_REL = "src/shortlist/scout/preregister/{}.yaml"


def _git(repo, *args, when=None):
    env = dict(os.environ)
    if when:
        env.update(GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True, env=env)


def _commit_all(repo, msg, when):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg, when=when)


def test_load_prereg_reads_committed_content_not_the_working_tree(tmp_path):
    """A-1: the whole anti-p-hacking guarantee. Loosening a floor on disk without committing
    must NOT change what the evaluator reads."""
    repo = _init_repo_with_prereg(tmp_path)
    p = repo / _REL.format("fake_signal")
    p.write_text("k_months: 12\nfactor_model: ff3\nmin_measurable_frac: 0.10\n")
    assert load_prereg("fake_signal", repo_root=str(repo)).get("min_measurable_frac") is None


def test_load_prereg_ignores_an_assume_unchanged_tamper(tmp_path):
    """The bypass an adversarial review found in the first design: `--assume-unchanged` makes
    `git status` report clean, so a status-based check passes while the worktree is edited.
    Reading HEAD directly has no such gap."""
    repo = _init_repo_with_prereg(tmp_path)
    rel = _REL.format("fake_signal")
    _git(repo, "update-index", "--assume-unchanged", rel)
    (repo / rel).write_text("k_months: 999\nfactor_model: ff3\n")
    assert _git(repo, "status", "--porcelain", "--", rel).stdout.strip() == ""   # gate fooled
    assert load_prereg("fake_signal", repo_root=str(repo))["k_months"] == 12     # we are not


def test_registration_date_follows_content_through_a_rename(tmp_path):
    """A-2: a pure `git mv` must not reset the clock."""
    repo = _init_repo_with_prereg(tmp_path, slug="old_name",
                                  commit_date="2026-01-01T12:00:00+00:00")
    _git(repo, "mv", _REL.format("old_name"), _REL.format("new_name"))
    _commit_all(repo, "rename", "2026-06-01T12:00:00+00:00")
    # Registered 2026-01-01, merely RENAMED on 06-01 -> a run on 03-01 is still after it.
    ok, reason = verify_untampered("new_name", repo_root=str(repo),
                                   run_as_of=date(2026, 3, 1))
    assert ok is True, reason


def test_a_comment_only_edit_does_not_reset_the_registration_date(tmp_path):
    """Registration is about the INFERENCE PARAMETERS, so compare parsed YAML, not blobs —
    otherwise a clarifying comment or a repo-wide reformat re-registers every file."""
    repo = _init_repo_with_prereg(tmp_path, commit_date="2026-01-01T12:00:00+00:00")
    p = repo / _REL.format("fake_signal")
    p.write_text("# clarifying comment added later\nk_months: 12\nfactor_model: ff3\n")
    _commit_all(repo, "comment only", "2026-06-01T12:00:00+00:00")
    ok, reason = verify_untampered("fake_signal", repo_root=str(repo),
                                   run_as_of=date(2026, 3, 1))
    assert ok is True, reason


def test_a_parameter_revert_re_registers_at_the_revert(tmp_path):
    """Contiguity: content A -> B -> A must date from the REVERT, not the original, or a
    tamper could be laundered by reverting it."""
    repo = _init_repo_with_prereg(tmp_path, commit_date="2026-01-01T12:00:00+00:00")
    p = repo / _REL.format("fake_signal")
    p.write_text("k_months: 3\nfactor_model: ff3\n")
    _commit_all(repo, "tamper", "2026-02-01T12:00:00+00:00")
    p.write_text("k_months: 12\nfactor_model: ff3\n")
    _commit_all(repo, "revert", "2026-06-01T12:00:00+00:00")
    ok, reason = verify_untampered("fake_signal", repo_root=str(repo),
                                   run_as_of=date(2026, 3, 1))
    assert ok is False
    assert "not pre-registered" in reason.lower()


def test_an_edit_reverted_on_a_merged_side_branch_does_not_reset_registration(tmp_path):
    """--first-parent: an abandoned experiment on a branch never changed the mainline, so it
    must not raise a false tamper alarm."""
    repo = _init_repo_with_prereg(tmp_path, commit_date="2026-01-01T12:00:00+00:00")
    p = repo / _REL.format("fake_signal")
    _git(repo, "checkout", "-q", "-b", "side")
    p.write_text("k_months: 3\nfactor_model: ff3\n")
    _commit_all(repo, "branch edit", "2026-02-01T12:00:00+00:00")
    p.write_text("k_months: 12\nfactor_model: ff3\n")
    _commit_all(repo, "branch revert", "2026-02-02T12:00:00+00:00")
    _git(repo, "checkout", "-q", "-")
    _git(repo, "merge", "--no-ff", "-q", "-m", "merge side", "side", when="2026-02-03T12:00:00+00:00")
    ok, reason = verify_untampered("fake_signal", repo_root=str(repo),
                                   run_as_of=date(2026, 1, 15))
    assert ok is True, reason


def test_load_prereg_falls_back_to_disk_outside_git_but_verify_refuses(tmp_path):
    """A pip-installed (non-git) deployment must still RUN, and must still tell the truth
    about what it could verify."""
    d = tmp_path / "nogit" / "src" / "shortlist" / "scout" / "preregister"
    d.mkdir(parents=True)
    (d / "fake_signal.yaml").write_text("k_months: 7\n")
    root = str(tmp_path / "nogit")
    assert load_prereg("fake_signal", repo_root=root)["k_months"] == 7
    ok, reason = verify_untampered("fake_signal", repo_root=root, run_as_of=date.today())
    assert ok is False and reason

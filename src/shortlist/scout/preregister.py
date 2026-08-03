"""Load + tamper-check the git-committed pre-registration files. Fixing the inference
parameters (K, factor model, floors, window) in a committed file -- and verifying when that
CONTENT was committed vs the run as_of -- is what closes the "edit thresholds after seeing
the plot" leak (spec §7). Uses git commit time, never filesystem mtime.

The YAML records live under `src/shortlist/scout/preregister/<slug>.yaml` (a plain data
directory, NOT a Python package -- no `__init__.py`), rather than the intuitive
`scout/preregister/` at the repo root: root `/scout/` is gitignored (see `.gitignore`), so a
file placed there could never be committed and the tamper check below would always report
"not committed to git". Living under the tracked `src/` tree is what makes the
anti-p-hacking guarantee possible at all.

**`load_prereg` parses the COMMITTED bytes (`git show HEAD:<path>`), never the working
tree.** That is the load-bearing choice, and it replaces an earlier design that read the
worktree and tried to *detect* divergence from HEAD. Detection was the wrong shape: it left
the evaluator reading unverified bytes and merely hoping to notice, and a `git status`-based
check is silently bypassed by `git update-index --assume-unchanged` (which reports a clean
tree while the file on disk is edited). Reading HEAD leaves no gap to detect, and no index
refresh -- which also avoids `index.lock` contention on a box where the scout timer and the
bot run concurrently. Design: docs/EVALUATOR_CORRECTNESS.md §1.

**What this is not:** a cryptographic guarantee. `%cI` is forgeable via `GIT_COMMITTER_DATE`
(this module's own tests do exactly that to control commit times). The threat model is the
operator editing their own thresholds after seeing a plot; against that it is effective.
"""
from __future__ import annotations

import subprocess
from datetime import date, datetime
from pathlib import Path

import yaml

_REL_DIR = ("src", "shortlist", "scout", "preregister")


def _rel_path(signal_slug: str) -> str:
    """Repo-root-RELATIVE posix path -- what `git show <rev>:<path>` needs."""
    return "/".join((*_REL_DIR, f"{signal_slug}.yaml"))


def _prereg_path(signal_slug: str, repo_root: str) -> Path:
    return Path(repo_root).joinpath(*_REL_DIR, f"{signal_slug}.yaml")


def _git(repo_root: str, *args: str) -> subprocess.CompletedProcess | None:
    """Run a git command, or None when git/the repo is unavailable. Never raises."""
    try:
        return subprocess.run(["git", "-C", repo_root, *args],
                              capture_output=True, text=True, timeout=10)
    except (subprocess.SubprocessError, OSError):
        return None


def _committed_text(signal_slug: str, repo_root: str) -> str | None:
    """The file's bytes AS COMMITTED at HEAD, or None when git is unavailable or the file is
    not committed (untracked / never committed / not a repo)."""
    out = _git(repo_root, "show", f"HEAD:{_rel_path(signal_slug)}")
    if out is None or out.returncode != 0:
        return None
    return out.stdout


def load_prereg(signal_slug: str, *, repo_root: str) -> dict:
    """Parse the pre-registration record for `signal_slug` from the COMMITTED content.

    Falls back to the working-tree file ONLY when git cannot supply it (no git binary, not a
    repo, file not committed). That fallback exists so a pip-installed, non-git deployment
    still runs -- and it is safe because `verify_untampered` independently returns False in
    exactly those cases, so any verdict built from unverifiable bytes is labelled
    NOT PRE-REGISTERED rather than silently trusted.
    """
    text = _committed_text(signal_slug, repo_root)
    if text is None:
        path = _prereg_path(signal_slug, repo_root)
        try:
            text = path.read_text()
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"pre-registration missing for '{signal_slug}' (expected {path})") from exc
    return yaml.safe_load(text)


def _params(text: str | None):
    """Parsed inference parameters, or None if unparsable.

    Comparison is on the PARSED YAML, not the blob, so cosmetic churn -- a clarifying
    comment, a whitespace tidy, a repo-wide reformat -- does not reset a file's registration
    date. A real parameter change (including an A->B->A revert) still shows up, because the
    parsed mappings differ.
    """
    if text is None:
        return None
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return None


def _history(repo_root: str, rel: str) -> list[tuple[str, str, str | None]]:
    """[(commit, iso_committer_date, historical_path)] newest-first along the FIRST-PARENT
    mainline, following renames. `historical_path` is None for a merge commit (`--name-only`
    prints no path for one).

    `--first-parent` is load-bearing: without it the walk descends into merged side branches,
    so an experiment that edited a prereg on a branch and reverted it there -- never changing
    the mainline -- would date the registration to the branch, raising a false tamper alarm.
    This repo squash-merges PRs, so first-parent IS the mainline.
    """
    out = _git(repo_root, "log", "--first-parent", "--follow",
               "--format=COMMIT %H %cI", "--name-only", "--", rel)
    if out is None or out.returncode != 0:
        return []
    entries: list[tuple[str, str, str | None]] = []
    commit = when = None
    for line in out.stdout.splitlines():
        if line.startswith("COMMIT "):
            if commit is not None:
                entries.append((commit, when, None))       # no path emitted -> merge commit
            _, commit, when = line.split()
        elif line.strip() and commit is not None:
            entries.append((commit, when, line.strip()))
            commit = when = None
    if commit is not None:
        entries.append((commit, when, None))
    return entries


def verify_untampered(signal_slug: str, *, repo_root: str, run_as_of: date) -> tuple[bool, str]:
    """(ok, reason). ok=False when the pre-registration's CURRENT inference parameters were
    committed AFTER `run_as_of` -- i.e. they may have been edited to fit results.

    Dates the CONTENT, not the path. Walking back from HEAD while the parsed parameters stay
    equal, the OLDEST CONTIGUOUS match is the registration date. Contiguity (rather than
    "earliest commit that ever had these parameters") is what stops a tamper being laundered
    by reverting it: for history A -> B -> A the answer is the revert, not the original.

    A pure `git mv` therefore no longer resets the clock (the bug this replaced: a rename is
    a commit touching the path, so a path-based check dated `edgar_buyback_auth.yaml` to its
    2026-07-12 rename instead of its real 2026-07-09 registration).

    Falls back to ok=False with a clear reason whenever git metadata is unavailable -- never
    silently trusts.
    """
    rel = _rel_path(signal_slug)
    head = _committed_text(signal_slug, repo_root)
    if head is None:
        return (False, "pre-reg file not committed to git (or git unavailable) — cannot verify")
    want = _params(head)
    entries = _history(repo_root, rel)
    if not entries:
        return (False, "cannot read git history for pre-reg")

    registered: str | None = None
    for commit, when, path in entries:
        if path is None:
            continue                       # merge commit: neither a match nor a mismatch
        blob = _git(repo_root, "show", f"{commit}:{path}")
        if blob is None or blob.returncode != 0 or _params(blob.stdout) != want:
            break                          # contiguity broken -> stop walking
        registered = when
    if registered is None:
        return (False, "cannot date the pre-reg content in git history")

    committed = datetime.fromisoformat(registered).date()
    if committed > run_as_of:
        return (False, f"pre-reg committed {committed} AFTER run as_of {run_as_of} "
                       f"— not pre-registered")
    return (True, "ok")

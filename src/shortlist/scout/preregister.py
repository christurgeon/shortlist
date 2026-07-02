"""Load + tamper-check the git-committed pre-registration files. Fixing the inference
parameters (K, factor model, floors, window) in a committed file -- and verifying its commit
time vs the run as_of -- is what closes the 'edit thresholds after seeing the plot' leak
(spec §7). Uses git commit time, never filesystem mtime.

The YAML records live under `src/shortlist/scout/preregister/<slug>.yaml` (a plain data
directory, NOT a Python package -- no `__init__.py`), rather than the intuitive
`scout/preregister/` at the repo root: root `/scout/` is gitignored (see `.gitignore`), so a
file placed there could never be committed and the git-blob tamper check below would always
report "not committed to git". Living under the tracked `src/` tree is what makes the
anti-p-hacking guarantee possible at all.
"""
from __future__ import annotations

import subprocess
from datetime import date, datetime
from pathlib import Path

import yaml


def _prereg_path(signal_slug: str, repo_root: str) -> Path:
    return Path(repo_root) / "src" / "shortlist" / "scout" / "preregister" / f"{signal_slug}.yaml"


def load_prereg(signal_slug: str, *, repo_root: str) -> dict:
    return yaml.safe_load(_prereg_path(signal_slug, repo_root).read_text())


def verify_untampered(signal_slug: str, *, repo_root: str, run_as_of: date) -> tuple[bool, str]:
    """(ok, reason). ok=False if the pre-reg file's last git commit is AFTER run_as_of
    (i.e. it may have been edited to fit results). Falls back to ok=False with a clear
    reason if git metadata is unavailable -- never silently trusts."""
    path = _prereg_path(signal_slug, repo_root)
    try:
        out = subprocess.run(
            ["git", "-C", repo_root, "log", "-1", "--format=%cI", "--", str(path)],
            capture_output=True, text=True, timeout=10, check=True).stdout.strip()
    except (subprocess.SubprocessError, OSError) as exc:
        return (False, f"cannot read git commit time for pre-reg: {exc}")
    if not out:
        return (False, "pre-reg file not committed to git")
    committed = datetime.fromisoformat(out).date()
    if committed > run_as_of:
        return (False, f"pre-reg committed {committed} AFTER run as_of {run_as_of} — not pre-registered")
    return (True, "ok")

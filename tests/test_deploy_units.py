"""Guards for the systemd units the installer generates INLINE.

`deploy/install_opt_shortlist.sh` does not read `deploy/*.service` (except the static
`shortlist-bot.service`) — it writes each unit from a heredoc, so a `[Service]` setting
must go in the heredoc to take effect. These tests pin the failure-alert wiring and
exercise the alert script itself.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
INSTALLER = REPO / "deploy" / "install_opt_shortlist.sh"
ALERT_SH = REPO / "deploy" / "shortlist-alert-failure.sh"

ONFAILURE = "OnFailure=shortlist-alert-failure@%n.service"


def _heredoc(unit_name: str) -> str:
    """The body the installer writes to $UNIT_DIR/<unit_name>."""
    text = INSTALLER.read_text()
    pat = re.compile(
        r'cat > "\$UNIT_DIR/' + re.escape(unit_name) + r'" <<\'?UNIT\'?\n(.*?)\nUNIT\n',
        re.DOTALL,
    )
    m = pat.search(text)
    assert m, f"installer no longer generates {unit_name} via a UNIT heredoc"
    return m.group(1)


def test_the_scheduled_route_wires_the_failure_alert() -> None:
    """A crash on the timer must page, not sit silently in the journal."""
    assert ONFAILURE in _heredoc("shortlist-accumulate.service")





def test_inplace_run_skips_rsync_instead_of_silent_noop() -> None:
    """SRC == DEST (e.g. running the installer FROM /opt/shortlist per the documented
    `cd /opt/shortlist && sudo git pull && sudo bash deploy/install_opt_shortlist.sh`
    recipe) must not silently rsync the tree onto itself and report success. It must also
    not hard-refuse, since that recipe is the documented happy path. The installer must
    detect the in-place case, print a loud notice, and skip the rsync call.
    """
    text = INSTALLER.read_text()
    # Canonical comparison, resolved before any mutation (mkdir/rsync/venv build).
    assert re.search(r'^SRC_REAL=.*readlink -f "\$SRC"', text, re.MULTILINE)
    assert re.search(r'^DEST_REAL=.*readlink -f "\$DEST"', text, re.MULTILINE)
    mkdir_pos = text.index('mkdir -p "$DEST"')
    real_pos = text.index("SRC_REAL=")
    assert real_pos < mkdir_pos, "SRC/DEST comparison must happen before any mutation"

    inplace_check_pos = text.index('if [[ "$SRC_REAL" == "$DEST_REAL" ]]')
    assert inplace_check_pos < mkdir_pos

    # The rsync call itself must be conditional on the in-place flag, not unconditional:
    # it must sit between the INPLACE guard and the post-sync chown that follows the fi.
    rsync_pos = text.index("rsync -a \\")
    guard_pos = text.index("if [[ $INPLACE -eq 1 ]]")
    post_sync_chown_pos = text.index('chown -R "$RUN_USER:$RUN_GROUP" "$DEST"')
    assert guard_pos < rsync_pos < post_sync_chown_pos
    assert "skipping rsync" in text


def test_smoke_test_is_read_only() -> None:
    """A smoke test that writes to state/ pollutes live data on every deploy."""
    assert "'./.venv/bin/shortlist' --demo" in INSTALLER.read_text()


def test_smoke_test_failure_aborts_with_a_labelled_block() -> None:
    """A failing smoke test must abort the deploy (never restart the bot onto code that
    cannot even run `--demo`) and print the captured output under a label, not leak bare
    stderr out of a nested `sudo bash -lc`.
    """
    text = INSTALLER.read_text()
    assert 'if ! _smoke_out="$(' in text
    assert "SMOKE TEST FAILED" in text
    smoke_pos = text.index("SMOKE TEST FAILED")
    units_pos = text.index('echo "==> 5/6  Install systemd units"')
    restart_pos = text.index("systemctl try-restart shortlist-bot.service")
    assert smoke_pos < units_pos < restart_pos


def _trap_block() -> str:
    """The failure-reporting trap the installer arms before it touches $DEST."""
    text = INSTALLER.read_text()
    start = text.index('STAGE="startup"')
    end = text.index("trap _on_exit EXIT") + len("trap _on_exit EXIT")
    return text[start:end]


def _run_trap(tmp_path: Path, tail: str) -> tuple[int, str]:
    script = tmp_path / "trap.sh"
    script.write_text(
        "set -euo pipefail\nDEST=/opt/shortlist\n" + _trap_block() + "\n" + tail
    )
    proc = subprocess.run(["bash", str(script)], capture_output=True, text=True)
    return proc.returncode, proc.stderr


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
def test_failed_deploy_names_the_stage_and_the_half_applied_state(tmp_path: Path) -> None:
    """Aborting mid-deploy is correct (the running bot keeps its old in-memory code), but
    it must not be silent: shortlist-bot.service carries Restart=on-failure, so the next
    crash or reboot starts the bot on the untested tree left on disk.
    """
    rc, err = _run_trap(
        tmp_path,
        'STAGE="3/6 build the venv (uv sync)"\nDEST_MUTATED=1\n(exit 3)\necho UNREACHABLE\n',
    )
    assert rc == 3, "the trap must preserve the failing command's exit status"
    assert "UNREACHABLE" not in err
    assert "DEPLOY FAILED" in err
    assert "3/6 build the venv" in err
    assert "WAS ALREADY MODIFIED" in err
    assert "Restart=on-failure" in err


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
def test_failure_before_any_mutation_says_so(tmp_path: Path) -> None:
    """A pre-flight failure leaves $DEST untouched — do not send the operator hunting for
    a rollback that is not needed.
    """
    rc, err = _run_trap(tmp_path, 'STAGE="1/6 create $DEST"\n(exit 7)\n')
    assert rc == 7
    assert "was not modified" in err
    assert "WAS ALREADY MODIFIED" not in err


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
def test_successful_run_prints_no_failure_banner(tmp_path: Path) -> None:
    rc, err = _run_trap(tmp_path, 'DEST_MUTATED=1\necho done\nexit 0\n')
    assert rc == 0
    assert "DEPLOY FAILED" not in err


def test_installer_generates_the_alert_template() -> None:
    body = _heredoc("shortlist-alert-failure@.service")
    # %i carries the failing unit name; the script is the deployed copy, not the source tree.
    assert "shortlist-alert-failure.sh %i" in body
    assert "/deploy/shortlist-alert-failure.sh" in body


def test_alert_template_does_not_page_itself() -> None:
    """An OnFailure= on the alert unit would recurse when Telegram is unreachable."""
    body = _heredoc("shortlist-alert-failure@.service")
    directives = [ln for ln in body.splitlines() if not ln.lstrip().startswith("#")]
    assert not any(ln.startswith("OnFailure=") for ln in directives)


def test_alert_script_is_executable() -> None:
    assert ALERT_SH.stat().st_mode & stat.S_IXUSR, "alert script must ship executable"


def _run_alert(tmp_path: Path, env_body: str, journal: str) -> tuple[int, str, str]:
    """Run the alert script with journalctl/systemctl/curl stubbed onto PATH.

    The curl stub records the payload it was handed instead of sending it.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    captured = tmp_path / "payload.txt"

    (bindir / "journalctl").write_text(f"#!/bin/sh\ncat <<'EOF'\n{journal}\nEOF\n")
    (bindir / "systemctl").write_text("#!/bin/sh\necho stub\n")
    # curl gets the text on stdin via --data-urlencode text@-
    (bindir / "curl").write_text(
        f'#!/bin/sh\ncat > "{captured}"\nfor a in "$@"; do echo "$a" >> "{captured}.args"; done\n'
    )
    for f in bindir.iterdir():
        f.chmod(0o755)

    env_file = tmp_path / "env"
    env_file.write_text(env_body)

    env = dict(os.environ)
    # Hermetic: another test (or conftest's load_env) may have put the real TELEGRAM_*
    # into os.environ, which the script would then inherit — the no-credentials case
    # would silently pass credentials through and this suite would send a live message.
    for leaked in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        env.pop(leaked, None)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env["SHORTLIST_ENV_FILE"] = str(env_file)
    proc = subprocess.run(
        ["bash", str(ALERT_SH), "shortlist-accumulate.service"],
        capture_output=True, text=True, env=env, cwd=tmp_path,
    )
    payload = captured.read_text() if captured.exists() else ""
    return proc.returncode, payload, proc.stderr


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
def test_alert_script_reads_export_prefixed_env(tmp_path: Path) -> None:
    """This repo's .env uses `export KEY=` lines — systemd EnvironmentFile= cannot parse
    them (it reads a var literally named "export KEY"), which is why the script sources it.
    """
    rc, payload, _ = _run_alert(
        tmp_path,
        "export TELEGRAM_BOT_TOKEN=123456789:AAtesttoken\nexport TELEGRAM_CHAT_ID=42\n",
        "accumulate crashed",
    )
    assert rc == 0
    assert "accumulate crashed" in payload
    assert "shortlist-accumulate.service failed" in payload


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
def test_alert_script_redacts_secrets_before_telegram(tmp_path: Path) -> None:
    """CLAUDE.md: anything that may embed a request URL is redacted before it leaves the
    box. The journal carries FMP/Finnhub URLs with the key as a query param.
    """
    journal = (
        "httpx.HTTPError: GET https://financialmodelingprep.com/stable/ratios-ttm"
        "?symbol=AAPL&apikey=SUPERSECRETKEY123 -> 429\n"
        "finnhub: https://finnhub.io/api/v1/quote?symbol=X&token=FINNHUBSECRET failed\n"
    )
    rc, payload, _ = _run_alert(
        tmp_path,
        "export TELEGRAM_BOT_TOKEN=123456789:AAtesttoken\nexport TELEGRAM_CHAT_ID=42\n",
        journal,
    )
    assert rc == 0
    assert "SUPERSECRETKEY123" not in payload
    assert "FINNHUBSECRET" not in payload
    assert "apikey=REDACTED" in payload
    assert "token=REDACTED" in payload


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
def test_alert_script_exits_zero_without_credentials(tmp_path: Path) -> None:
    """A failed alert must never cascade — it is itself invoked from an OnFailure= hook."""
    rc, payload, stderr = _run_alert(tmp_path, "# no creds here\n", "boom")
    assert rc == 0
    assert payload == ""
    assert "missing telegram env vars" in stderr


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
def test_alert_script_clamps_oversize_journal(tmp_path: Path) -> None:
    """Telegram 400-rejects payloads over 4096 chars."""
    rc, payload, _ = _run_alert(
        tmp_path,
        "export TELEGRAM_BOT_TOKEN=123456789:AAtesttoken\nexport TELEGRAM_CHAT_ID=42\n",
        "x" * 20000,
    )
    assert rc == 0
    assert 0 < len(payload.encode()) <= 3900

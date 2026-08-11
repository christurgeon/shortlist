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





def test_smoke_test_is_read_only() -> None:
    """A smoke test that writes to state/ pollutes live data on every deploy."""
    assert "'./.venv/bin/shortlist' --demo" in INSTALLER.read_text()


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

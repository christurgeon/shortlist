from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Optional

from ..env import redact_secrets


@dataclass
class CliResult:
    text: str = ""
    cost_usd: Optional[float] = None
    stop_reason: Optional[str] = None
    model: Optional[str] = None
    error: Optional[str] = None
    # True when `error` is a transient failure (timeout / API or transport hiccup) a
    # fresh retry could recover; False for permanent failures (e.g. binary missing).
    transient: bool = False


def is_available() -> bool:
    """True if the `claude` binary is on PATH."""
    return shutil.which("claude") is not None


def run(prompt: str, system: str, model: str, timeout_s: float,
        fallback_model: Optional[str] = None) -> CliResult:
    """Invoke the headless `claude` CLI for a single structured-extraction turn.

    Locked down so it behaves as a stateless model call, not an agent: no tools,
    no ambient MCP servers, a single turn, and a neutral cwd (no CLAUDE.md/hook
    discovery). `--bare` is deliberately avoided — it would force ANTHROPIC_API_KEY
    auth; the flags here preserve the user's existing CLI auth. Prompt goes on
    stdin (filing text is far too long for argv).

    Timeout kill is process-GROUP-wide: the claude CLI spawns node helpers that
    inherit the stdout/stderr pipes, so killing only the direct child (what
    subprocess.run's timeout does) leaves the pipes open and communicate() blocks
    forever. The child runs in its own session (start_new_session=True) and on
    timeout the whole group gets SIGKILL, followed by a bounded reap.
    """
    argv = [
        "claude", "-p", "--output-format", "json",
        "--model", model,
        "--system-prompt", system,
        "--tools", "",
        "--strict-mcp-config",
        "--max-turns", "1",
    ]
    # An overloaded primary otherwise fails all of assess()'s attempts identically and
    # the brief is dropped. Omitted entirely when unset, so the invocation stays
    # byte-identical to the pre-feature form.
    if fallback_model:
        argv += ["--fallback-model", fallback_model]
    try:
        proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            cwd=tempfile.gettempdir(), start_new_session=True,
        )
    except FileNotFoundError:
        return CliResult(error="claude CLI not found on PATH")  # permanent — not transient

    def _kill_group() -> None:
        with contextlib.suppress(ProcessLookupError):  # group already gone
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)

    try:
        stdout, stderr = proc.communicate(input=prompt, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill_group()
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()  # give up on output; don't block the caller further
        return CliResult(error=f"claude timed out after {timeout_s}s", transient=True)
    except BaseException:
        # Ctrl-C / any other escape: the child is in its own session, so it never
        # receives the terminal SIGINT — kill the group or it runs on orphaned.
        _kill_group()
        raise

    if proc.returncode != 0:
        return CliResult(error=redact_secrets(
            f"claude exited {proc.returncode}: {(stderr or '')[:500]}"), transient=True)
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError:
        return CliResult(error=redact_secrets(
            f"non-JSON envelope from claude: {(stdout or '')[:300]}"), transient=True)
    if envelope.get("is_error"):
        detail = envelope.get("result") or envelope.get("subtype") or "unknown"
        return CliResult(error=redact_secrets(f"claude error: {detail}"),
                         stop_reason=envelope.get("stop_reason"), transient=True)
    return CliResult(
        text=envelope.get("result", ""),
        cost_usd=envelope.get("total_cost_usd"),
        stop_reason=envelope.get("stop_reason"),
        model=_answering_model(envelope, model),
    )


def _answering_model(envelope: dict, requested: str) -> str:
    """The model that actually produced the answer. With --fallback-model that need not
    be the one requested, and this value is printed in the brief header — echoing the
    request would silently mislabel a fallback-produced brief. The CLI envelope keys
    `modelUsage` by the model that ran; when several did (primary attempted, fallback
    answered) the answering one is the one with output tokens. Falls back to the
    requested model if the envelope says nothing."""
    usage = envelope.get("modelUsage")
    if not isinstance(usage, dict) or not usage:
        return requested

    def _out(entry) -> int:
        return entry.get("outputTokens", 0) if isinstance(entry, dict) else 0

    return max(usage, key=lambda name: _out(usage[name]))

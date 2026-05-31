from __future__ import annotations

import json
import shutil
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


def is_available() -> bool:
    """True if the `claude` binary is on PATH."""
    return shutil.which("claude") is not None


def run(prompt: str, system: str, model: str, timeout_s: float) -> CliResult:
    """Invoke the headless `claude` CLI for a single structured-extraction turn.

    Locked down so it behaves as a stateless model call, not an agent: no tools,
    no ambient MCP servers, a single turn, and a neutral cwd (no CLAUDE.md/hook
    discovery). `--bare` is deliberately avoided — it would force ANTHROPIC_API_KEY
    auth; the flags here preserve the user's existing CLI auth. Prompt goes on
    stdin (filing text is far too long for argv). subprocess.run kills the process
    on timeout.
    """
    argv = [
        "claude", "-p", "--output-format", "json",
        "--model", model,
        "--system-prompt", system,
        "--tools", "",
        "--strict-mcp-config",
        "--max-turns", "1",
    ]
    try:
        proc = subprocess.run(
            argv, input=prompt, capture_output=True, text=True, encoding="utf-8",
            timeout=timeout_s, cwd=tempfile.gettempdir(),
        )
    except FileNotFoundError:
        return CliResult(error="claude CLI not found on PATH")
    except subprocess.TimeoutExpired:
        return CliResult(error=f"claude timed out after {timeout_s}s")

    if proc.returncode != 0:
        return CliResult(error=redact_secrets(
            f"claude exited {proc.returncode}: {(proc.stderr or '')[:500]}"))
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return CliResult(error=redact_secrets(
            f"non-JSON envelope from claude: {(proc.stdout or '')[:300]}"))
    if envelope.get("is_error"):
        detail = envelope.get("result") or envelope.get("subtype") or "unknown"
        return CliResult(error=redact_secrets(f"claude error: {detail}"),
                         stop_reason=envelope.get("stop_reason"))
    return CliResult(
        text=envelope.get("result", ""),
        cost_usd=envelope.get("total_cost_usd"),
        stop_reason=envelope.get("stop_reason"),
        model=model,
    )

import json
import os
import subprocess

import pytest

from shortlist.research import claude_cli


class _FakePopen:
    """Stands in for subprocess.Popen: returns canned stdout/stderr/returncode, or
    raises TimeoutExpired from communicate() to exercise the group-kill path."""

    def __init__(self, stdout="", returncode=0, stderr="", timeout_forever=False):
        self.stdout_text = stdout
        self.stderr_text = stderr
        self.returncode = returncode
        self.timeout_forever = timeout_forever
        self.pid = 999_999_999  # never a real pid — os.getpgid raises ProcessLookupError
        self.killed = False
        self.communicate_calls = []

    def communicate(self, input=None, timeout=None):
        self.communicate_calls.append({"input": input, "timeout": timeout})
        if self.timeout_forever:
            raise subprocess.TimeoutExpired(cmd="claude", timeout=timeout)
        return self.stdout_text, self.stderr_text

    def kill(self):
        self.killed = True


def _patch_popen(monkeypatch, proc, captured=None):
    def fake_popen(argv, **kwargs):
        if captured is not None:
            captured["argv"] = argv
            captured["kwargs"] = kwargs
        return proc
    monkeypatch.setattr(subprocess, "Popen", fake_popen)


def _ok_envelope(result_text):
    return json.dumps({"is_error": False, "result": result_text,
                       "stop_reason": "end_turn", "total_cost_usd": 0.02})


def test_run_success_extracts_result_and_cost(monkeypatch):
    _patch_popen(monkeypatch, _FakePopen(_ok_envelope('{"x":1}')))
    res = claude_cli.run(prompt="hi", system="sys", model="claude-sonnet-5", timeout_s=5)
    assert res.error is None
    assert res.text == '{"x":1}'
    assert res.cost_usd == 0.02
    assert res.stop_reason == "end_turn"
    assert res.model == "claude-sonnet-5"


def test_run_locks_down_invocation(monkeypatch):
    captured = {}
    proc = _FakePopen(_ok_envelope("{}"))
    _patch_popen(monkeypatch, proc, captured)
    claude_cli.run(prompt="P", system="S", model="M", timeout_s=9)
    argv = captured["argv"]
    assert argv[0] == "claude" and "-p" in argv
    assert "--tools" in argv and argv[argv.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in argv
    assert "--max-turns" in argv and argv[argv.index("--max-turns") + 1] == "1"
    assert "--bare" not in argv                      # must NOT force API-key auth
    assert proc.communicate_calls[0]["input"] == "P"  # prompt via stdin
    assert proc.communicate_calls[0]["timeout"] == 9
    assert os.path.isabs(captured["kwargs"]["cwd"])   # neutral, absolute cwd
    # own session so a timeout can SIGKILL the whole process group (node helpers
    # holding the pipes included), not just the direct child
    assert captured["kwargs"]["start_new_session"] is True


def test_run_is_error_envelope(monkeypatch):
    env = json.dumps({"is_error": True, "result": "model refused", "stop_reason": "end_turn"})
    _patch_popen(monkeypatch, _FakePopen(env))
    res = claude_cli.run(prompt="p", system="s", model="m", timeout_s=5)
    assert res.error and "model refused" in res.error
    assert res.text == ""


def test_run_non_json_stdout(monkeypatch):
    _patch_popen(monkeypatch, _FakePopen("not json at all"))
    res = claude_cli.run(prompt="p", system="s", model="m", timeout_s=5)
    assert res.error and "non-JSON" in res.error


def test_run_binary_missing(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError()
    monkeypatch.setattr(subprocess, "Popen", boom)
    res = claude_cli.run(prompt="p", system="s", model="m", timeout_s=5)
    assert res.error and "not found" in res.error.lower()


def test_run_timeout(monkeypatch):
    _patch_popen(monkeypatch, _FakePopen(timeout_forever=True))
    res = claude_cli.run(prompt="p", system="s", model="m", timeout_s=5)
    assert res.error and "timed out" in res.error.lower()


def test_run_timeout_is_transient(monkeypatch):
    # A timeout is a transient failure — a fresh retry can succeed (the call was
    # slow/stuck, not broken). assess() relies on this flag to decide whether to retry.
    _patch_popen(monkeypatch, _FakePopen(timeout_forever=True))
    res = claude_cli.run(prompt="p", system="s", model="m", timeout_s=5)
    assert res.transient is True


def test_run_timeout_kills_process_group_then_reaps_bounded(monkeypatch):
    """On timeout: SIGKILL the whole process group (guarded), then a BOUNDED
    reaping communicate; if that also times out, plain kill() and give up on
    output — never an untimed communicate that can block forever."""
    proc = _FakePopen(timeout_forever=True)
    _patch_popen(monkeypatch, proc)
    killed_groups = []
    monkeypatch.setattr(claude_cli.os, "getpgid", lambda pid: 4242)
    monkeypatch.setattr(claude_cli.os, "killpg",
                        lambda pgid, sig: killed_groups.append((pgid, sig)))
    res = claude_cli.run(prompt="p", system="s", model="m", timeout_s=5)
    assert res.error and "timed out" in res.error.lower() and res.transient
    assert killed_groups == [(4242, claude_cli.signal.SIGKILL)]
    # first communicate = the real call; second = the bounded post-kill reap
    assert len(proc.communicate_calls) == 2
    assert proc.communicate_calls[1]["timeout"] == 5
    assert proc.killed  # the reap also timed out -> plain kill(), no third wait


def test_run_ctrl_c_kills_detached_group_and_reraises(monkeypatch):
    """The child runs in its own session (start_new_session=True), so it never
    receives the terminal SIGINT — a KeyboardInterrupt (or any escape) out of
    communicate() must SIGKILL the group before propagating, or an orphaned
    claude CLI keeps running to completion in the background."""
    class _InterruptedPopen(_FakePopen):
        def communicate(self, input=None, timeout=None):
            raise KeyboardInterrupt

    proc = _InterruptedPopen()
    _patch_popen(monkeypatch, proc)
    killed_groups = []
    monkeypatch.setattr(claude_cli.os, "getpgid", lambda pid: 4242)
    monkeypatch.setattr(claude_cli.os, "killpg",
                        lambda pgid, sig: killed_groups.append((pgid, sig)))
    with pytest.raises(KeyboardInterrupt):
        claude_cli.run(prompt="p", system="s", model="m", timeout_s=5)
    assert killed_groups == [(4242, claude_cli.signal.SIGKILL)]


def test_run_timeout_survives_already_dead_group(monkeypatch):
    # The fake pid is not a real process: os.getpgid raises ProcessLookupError,
    # which the kill path must swallow (the group already exited).
    proc = _FakePopen(timeout_forever=True)
    _patch_popen(monkeypatch, proc)
    res = claude_cli.run(prompt="p", system="s", model="m", timeout_s=5)
    assert res.error and "timed out" in res.error.lower()
    assert res.transient is True


def test_run_binary_missing_is_not_transient(monkeypatch):
    # A missing binary is permanent — retrying is pointless, so it must NOT be transient.
    def boom(*a, **k):
        raise FileNotFoundError()
    monkeypatch.setattr(subprocess, "Popen", boom)
    res = claude_cli.run(prompt="p", system="s", model="m", timeout_s=5)
    assert res.transient is False


def test_run_nonzero_exit_is_transient(monkeypatch):
    # A non-zero exit is usually an API/transport hiccup, not a code bug — retryable.
    _patch_popen(monkeypatch, _FakePopen(returncode=1, stderr="overloaded"))
    res = claude_cli.run(prompt="p", system="s", model="m", timeout_s=5)
    assert res.error and res.transient is True


def test_run_nonzero_exit_redacts_stderr(monkeypatch):
    _patch_popen(monkeypatch, _FakePopen(returncode=1,
                                         stderr="Error: sk-ant-abc123 unauthorized"))
    res = claude_cli.run(prompt="p", system="s", model="m", timeout_s=5)
    assert res.error and "exited 1" in res.error
    assert "sk-ant-abc123" not in res.error   # redaction verified


def test_is_available(monkeypatch):
    monkeypatch.setattr(claude_cli.shutil, "which", lambda _: "/usr/bin/claude")
    assert claude_cli.is_available() is True
    monkeypatch.setattr(claude_cli.shutil, "which", lambda _: None)
    assert claude_cli.is_available() is False


def test_fallback_model_is_passed_when_configured(monkeypatch):
    """D10: assess() retries 3x on transient failure, but with no fallback model an
    overloaded primary fails all three the same way and the brief is dropped."""
    captured = {}
    proc = _FakePopen(stdout=json.dumps({"result": "{}", "total_cost_usd": 0.01}))
    _patch_popen(monkeypatch, proc, captured)
    claude_cli.run(prompt="p", system="s", model="claude-sonnet-5", timeout_s=5,
                   fallback_model="claude-opus-5")
    argv = captured["argv"]
    assert "--fallback-model" in argv
    assert argv[argv.index("--fallback-model") + 1] == "claude-opus-5"


def test_fallback_model_omitted_when_not_configured(monkeypatch):
    """Absent config must leave the invocation byte-identical to pre-feature."""
    captured = {}
    proc = _FakePopen(stdout=json.dumps({"result": "{}", "total_cost_usd": 0.01}))
    _patch_popen(monkeypatch, proc, captured)
    claude_cli.run(prompt="p", system="s", model="claude-opus-5", timeout_s=5)
    assert "--fallback-model" not in captured["argv"]


def test_result_reports_the_model_that_actually_ran(monkeypatch):
    """With --fallback-model, the model that answers may not be the one requested. The
    brief header prints this value, so echoing the request would mislabel the brief.
    The CLI envelope keys `modelUsage` by the model that actually ran."""
    envelope = {"result": "{}", "total_cost_usd": 0.01, "stop_reason": "end_turn",
                "modelUsage": {"claude-opus-5": {"outputTokens": 900}}}
    _patch_popen(monkeypatch, _FakePopen(stdout=json.dumps(envelope)))
    res = claude_cli.run(prompt="p", system="s", model="claude-sonnet-5", timeout_s=5,
                         fallback_model="claude-opus-5")
    assert res.model == "claude-opus-5"


def test_result_picks_the_answering_model_when_several_ran(monkeypatch):
    """A fallback run reports usage for both models; the one that produced the answer
    is the one with output tokens."""
    envelope = {"result": "{}", "total_cost_usd": 0.01, "stop_reason": "end_turn",
                "modelUsage": {"claude-sonnet-5": {"outputTokens": 0},
                               "claude-opus-5": {"outputTokens": 1200}}}
    _patch_popen(monkeypatch, _FakePopen(stdout=json.dumps(envelope)))
    res = claude_cli.run(prompt="p", system="s", model="claude-sonnet-5", timeout_s=5)
    assert res.model == "claude-opus-5"


def test_result_falls_back_to_requested_model_when_envelope_is_silent(monkeypatch):
    """No modelUsage (older CLI / odd envelope) must not blank the label."""
    _patch_popen(monkeypatch, _FakePopen(
        stdout=json.dumps({"result": "{}", "total_cost_usd": 0.01})))
    res = claude_cli.run(prompt="p", system="s", model="claude-opus-5", timeout_s=5)
    assert res.model == "claude-opus-5"

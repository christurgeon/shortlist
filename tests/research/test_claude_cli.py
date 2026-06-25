import json
import os
import subprocess

from shortlist.research import claude_cli


def _completed(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=["claude"], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


def _ok_envelope(result_text):
    return json.dumps({"is_error": False, "result": result_text,
                       "stop_reason": "end_turn", "total_cost_usd": 0.02})


def test_run_success_extracts_result_and_cost(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(_ok_envelope('{"x":1}')))
    res = claude_cli.run(prompt="hi", system="sys", model="claude-sonnet-4-6", timeout_s=5)
    assert res.error is None
    assert res.text == '{"x":1}'
    assert res.cost_usd == 0.02
    assert res.stop_reason == "end_turn"
    assert res.model == "claude-sonnet-4-6"


def test_run_locks_down_invocation(monkeypatch):
    captured = {}
    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _completed(_ok_envelope("{}"))
    monkeypatch.setattr(subprocess, "run", fake_run)
    claude_cli.run(prompt="P", system="S", model="M", timeout_s=9)
    argv = captured["argv"]
    assert argv[0] == "claude" and "-p" in argv
    assert "--tools" in argv and argv[argv.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in argv
    assert "--max-turns" in argv and argv[argv.index("--max-turns") + 1] == "1"
    assert "--bare" not in argv                      # must NOT force API-key auth
    assert captured["kwargs"]["input"] == "P"         # prompt via stdin
    assert captured["kwargs"]["timeout"] == 9
    assert os.path.isabs(captured["kwargs"]["cwd"])   # neutral, absolute cwd


def test_run_is_error_envelope(monkeypatch):
    env = json.dumps({"is_error": True, "result": "model refused", "stop_reason": "end_turn"})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(env))
    res = claude_cli.run(prompt="p", system="s", model="m", timeout_s=5)
    assert res.error and "model refused" in res.error
    assert res.text == ""


def test_run_non_json_stdout(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed("not json at all"))
    res = claude_cli.run(prompt="p", system="s", model="m", timeout_s=5)
    assert res.error and "non-JSON" in res.error


def test_run_binary_missing(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError()
    monkeypatch.setattr(subprocess, "run", boom)
    res = claude_cli.run(prompt="p", system="s", model="m", timeout_s=5)
    assert res.error and "not found" in res.error.lower()


def test_run_timeout(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=5)
    monkeypatch.setattr(subprocess, "run", boom)
    res = claude_cli.run(prompt="p", system="s", model="m", timeout_s=5)
    assert res.error and "timed out" in res.error.lower()


def test_run_timeout_is_transient(monkeypatch):
    # A timeout is a transient failure — a fresh retry can succeed (the call was
    # slow/stuck, not broken). assess() relies on this flag to decide whether to retry.
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=5)
    monkeypatch.setattr(subprocess, "run", boom)
    res = claude_cli.run(prompt="p", system="s", model="m", timeout_s=5)
    assert res.transient is True


def test_run_binary_missing_is_not_transient(monkeypatch):
    # A missing binary is permanent — retrying is pointless, so it must NOT be transient.
    def boom(*a, **k):
        raise FileNotFoundError()
    monkeypatch.setattr(subprocess, "run", boom)
    res = claude_cli.run(prompt="p", system="s", model="m", timeout_s=5)
    assert res.transient is False


def test_run_nonzero_exit_is_transient(monkeypatch):
    # A non-zero exit is usually an API/transport hiccup, not a code bug — retryable.
    monkeypatch.setattr(subprocess, "run", lambda *a, **k:
        _completed(returncode=1, stderr="overloaded"))
    res = claude_cli.run(prompt="p", system="s", model="m", timeout_s=5)
    assert res.error and res.transient is True


def test_run_nonzero_exit_redacts_stderr(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k:
        _completed(returncode=1, stderr="Error: sk-ant-abc123 unauthorized"))
    res = claude_cli.run(prompt="p", system="s", model="m", timeout_s=5)
    assert res.error and "exited 1" in res.error
    assert "sk-ant-abc123" not in res.error   # redaction verified


def test_is_available(monkeypatch):
    monkeypatch.setattr(claude_cli.shutil, "which", lambda _: "/usr/bin/claude")
    assert claude_cli.is_available() is True
    monkeypatch.setattr(claude_cli.shutil, "which", lambda _: None)
    assert claude_cli.is_available() is False

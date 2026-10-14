"""ClaudeCode.py: the `claude -p` command line and how its JSON result is read, with a fake subprocess."""
import json
import subprocess

import pytest

import ClaudeCode


class FakeRun:
    def __init__(self, *outputs):
        self.outputs, self.calls = list(outputs), []

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        out = self.outputs.pop(0)
        return subprocess.CompletedProcess(args, 0 if isinstance(out, dict) else 1,
                                           stdout=json.dumps(out) if isinstance(out, dict) else "",
                                           stderr="" if isinstance(out, dict) else out)


@pytest.fixture
def fake(monkeypatch):
    def install(*outputs):
        run = FakeRun(*outputs)
        monkeypatch.setattr(ClaudeCode.subprocess, "run", run)
        monkeypatch.setattr(ClaudeCode.time, "sleep", lambda s: None)
        return run
    return install


def test_text_call_has_no_tools_and_reads_result(fake):
    run = fake({"subtype": "success", "is_error": False, "result": "  Sonogenetic Toolkits \n"})
    assert ClaudeCode.ask_text("Name it", "haiku") == "Sonogenetic Toolkits"
    args, kwargs = run.calls[0]
    assert args[:2] == ["claude", "-p"] and args[args.index("--model") + 1] == "haiku"
    assert args[args.index("--tools") + 1] == ""
    assert kwargs["input"] == "Name it"


def test_json_call_passes_schema_and_tools(fake):
    run = fake({"subtype": "success", "result": "{}", "structured_output": {"name": "x"}})
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    out = ClaudeCode.ask_json("p", schema, "sonnet", tools=["Bash(python -m trends probe:*)", "WebSearch"])
    assert out == {"name": "x"}
    args, _ = run.calls[0]
    assert json.loads(args[args.index("--json-schema") + 1]) == schema
    assert args[args.index("--tools") + 1] == "Bash,WebSearch"
    assert "Bash(python -m trends probe:*)" in args


def test_failed_run_is_retried_then_raises(fake):
    run = fake("not logged in", {"subtype": "error_max_turns", "is_error": True, "result": "too many turns"})
    with pytest.raises(ClaudeCode.ClaudeCodeError, match="too many turns"):
        ClaudeCode.ask_text("p", "haiku")
    assert len(run.calls) == 2


def test_missing_structured_output_is_an_error(fake):
    fake({"subtype": "success", "result": "sure!"}, {"subtype": "success", "result": "sure!"})
    with pytest.raises(ClaudeCode.ClaudeCodeError):
        ClaudeCode.ask_json("p", {"type": "object"}, "haiku")

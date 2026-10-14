"""
LLM calls through headless Claude Code (`claude -p`), on the Claude subscription: no API key, usage counts
against the plan's limits. On a logged-in machine the CLI uses that login; in a container it reads the
long-lived token from `claude setup-token` in CLAUDE_CODE_OAUTH_TOKEN.

Used for the cluster names and descriptions (TopicModeling.py) and the topics of the week (trends/).
The CLI runs in a temporary directory, so no project CLAUDE.md ends up in the prompt; tools are off unless a
call allows some.
"""

import json
import logging
import os
import subprocess
import tempfile
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

COMMAND = "claude"


class ClaudeCodeError(RuntimeError):
    pass


def _args(model: str, schema: Optional[Dict], system: Optional[str], tools: Optional[List[str]],
          max_turns: Optional[int]) -> List[str]:
    args = [COMMAND, "-p", "--output-format", "json", "--model", model, "--no-session-persistence"]
    if tools:
        # --tools limits the built-in tools to these; --allowedTools lets them run without a permission prompt
        # (Bash patterns like "Bash(python -m trends probe:*)" go to --allowedTools, the tool name to --tools)
        names = sorted({t.split("(", 1)[0] for t in tools})
        args += ["--tools", ",".join(names), "--allowedTools", *tools]
    else:
        args += ["--tools", ""]
    if schema:
        args += ["--json-schema", json.dumps(schema)]
    if system:
        args += ["--append-system-prompt", system]
    if max_turns:
        args += ["--max-turns", str(max_turns)]
    return args


def run(prompt: str, model: str, schema: Optional[Dict] = None, system: Optional[str] = None,
        tools: Optional[List[str]] = None, max_turns: Optional[int] = None, cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None, timeout: int = 600, attempts: int = 2) -> Dict:
    """
    One `claude -p` session; returns the CLI's JSON result (`result` is the text, `structured_output` the
    object when a schema was given). `env` adds to the environment the CLI and its tools run in. Retries once on
    a failed run.
    """
    args = _args(model, schema, system, tools, max_turns)
    for attempt in range(1, attempts + 1):
        try:
            done = subprocess.run(args, input=prompt, capture_output=True, text=True, timeout=timeout,
                                  cwd=cwd or tempfile.gettempdir(), env={**os.environ, **(env or {})})
            if done.returncode != 0:
                raise ClaudeCodeError(f"claude -p exited {done.returncode}: {(done.stderr or done.stdout).strip()[:500]}")
            out = json.loads(done.stdout)
            if out.get("is_error") or out.get("subtype", "success") != "success":
                raise ClaudeCodeError(f"claude -p failed: {str(out.get('result') or out.get('subtype'))[:500]}")
            if schema and out.get("structured_output") is None:
                raise ClaudeCodeError("claude -p returned no structured output")
            return out
        except (ClaudeCodeError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            logger.warning(f"Claude Code call failed (attempt {attempt}/{attempts}): {e}")
            if attempt == attempts:
                raise ClaudeCodeError(str(e)) from e
            time.sleep(10)


def ask_text(prompt: str, model: str, **kwargs) -> str:
    """The answer as plain text."""
    return run(prompt, model, **kwargs)["result"].strip()


def ask_json(prompt: str, schema: Dict, model: str, **kwargs) -> Dict:
    """The answer as an object that follows `schema` (a JSON Schema)."""
    return run(prompt, model, schema=schema, **kwargs)["structured_output"]

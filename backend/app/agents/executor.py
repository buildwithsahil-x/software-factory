"""Runs one agent task with the Claude Agent SDK and turns everything it does
into factory events.

The executor sits behind a small interface so the orchestrator and tests can
swap it out (see `AgentExecutor`).
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

from app.agents.factory_tools import build_factory_server
from app.agents.guardrails import GuardContext, check_tool_call
from app.agents.registry import FACTORY_MCP_SERVER, AgentSpec

PLUGIN_NAME = "factory"
_MAX_FIELD_CHARS = 4000

Emit = Callable[[str, dict[str, Any]], Any]


@dataclass
class RunRequest:
    run_id: int
    spec: AgentSpec
    prompt: str
    cwd: Path
    plugin_dir: Path | None = None
    resume_session_id: str | None = None
    bash_sandbox: bool = False
    readable_roots: tuple[Path, ...] = ()


@dataclass
class RunOutcome:
    ok: bool
    session_id: str | None = None
    num_turns: int | None = None
    cost_usd: float | None = None
    result: str | None = None
    error: str | None = None


class AgentExecutor(Protocol):
    async def run(self, request: RunRequest, emit: Emit) -> RunOutcome: ...


# --------------------------------------------------------------------------- helpers


def _clip(value: Any) -> Any:
    """Keep stored events small: long strings and big inputs are truncated."""
    if isinstance(value, str):
        return value if len(value) <= _MAX_FIELD_CHARS else value[:_MAX_FIELD_CHARS] + " …[truncated]"
    text = json.dumps(value, default=str)
    if len(text) <= _MAX_FIELD_CHARS:
        return value
    return text[:_MAX_FIELD_CHARS] + " …[truncated]"


def _tool_result_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
        else:
            parts.append(json.dumps(item, default=str))
    return "\n".join(parts)


def message_to_events(message: Any) -> list[tuple[str, dict[str, Any]]]:
    """Convert one SDK message into zero or more (type, data) factory events."""
    events: list[tuple[str, dict[str, Any]]] = []
    if isinstance(message, SystemMessage):
        if message.subtype == "init":
            data = message.data
            events.append((
                "session_started",
                {
                    "session_id": data.get("session_id"),
                    "model": data.get("model"),
                    "tools": data.get("tools"),
                    "skills": data.get("skills"),
                    "plugins": data.get("plugins"),
                },
            ))
    elif isinstance(message, AssistantMessage):
        if message.error:
            events.append(("error", {"message": f"Model error: {message.error}"}))
        for block in message.content:
            if isinstance(block, TextBlock) and block.text.strip():
                events.append(("assistant_text", {"text": _clip(block.text)}))
            elif isinstance(block, ThinkingBlock) and block.thinking.strip():
                events.append(("thinking", {"text": _clip(block.thinking)}))
            elif isinstance(block, ToolUseBlock):
                events.append(("tool_use", {"id": block.id, "name": block.name, "input": _clip(block.input)}))
    elif isinstance(message, UserMessage) and isinstance(message.content, list):
        for block in message.content:
            if isinstance(block, ToolResultBlock):
                events.append((
                    "tool_result",
                    {
                        "tool_use_id": block.tool_use_id,
                        "is_error": bool(block.is_error),
                        "output": _clip(_tool_result_text(block.content)),
                    },
                ))
    elif isinstance(message, ResultMessage):
        events.append((
            "result",
            {
                "subtype": message.subtype,
                "is_error": message.is_error,
                "num_turns": message.num_turns,
                "cost_usd": message.total_cost_usd,
                "duration_ms": message.duration_ms,
                "result": _clip(message.result or ""),
                "errors": message.errors,
            },
        ))
    return events


def current_git_branch(cwd: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    branch = out.stdout.strip()
    return branch if out.returncode == 0 and branch else None


# --------------------------------------------------------------------------- executor


class SdkExecutor:
    """Runs agents through the Claude Agent SDK (Claude Code as a library)."""

    def build_options(self, request: RunRequest, emit: Emit) -> ClaudeAgentOptions:
        spec = request.spec
        guard = GuardContext(
            cwd=request.cwd,
            readable_roots=list(request.readable_roots),
            allowed_push_branches=spec.allowed_push_branches,
            current_branch=lambda: current_git_branch(request.cwd),
        )

        async def guardrail_hook(input_data: dict, _tool_use_id: str | None, _context: Any) -> dict:
            tool_name = input_data.get("tool_name", "")
            reason = check_tool_call(tool_name, input_data.get("tool_input") or {}, guard)
            if reason is None:
                return {}
            emit("guardrail_block", {"tool": tool_name, "input": _clip(input_data.get("tool_input")), "reason": reason})
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }

        plugins = []
        skills = None
        if request.plugin_dir and request.plugin_dir.is_dir():
            plugins = [{"type": "local", "path": str(request.plugin_dir)}]
            skills = [f"{PLUGIN_NAME}:{s}" for s in spec.skills] or None

        return ClaudeAgentOptions(
            model=spec.model,
            effort=spec.effort,
            system_prompt=spec.system_prompt,
            cwd=str(request.cwd),
            # Only the tools this role needs are available at all...
            tools=list(spec.tools),
            # ...and they are pre-approved, so agents run unattended.
            allowed_tools=spec.allowed_tools,
            permission_mode="dontAsk",
            mcp_servers={FACTORY_MCP_SERVER: build_factory_server(emit, spec.factory_tools)},
            hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[guardrail_hook])]},
            max_turns=spec.max_turns,
            max_budget_usd=spec.max_budget_usd,
            plugins=plugins,
            skills=skills,
            # Keep the operator's personal ~/.claude settings out of agent runs.
            setting_sources=[],
            resume=request.resume_session_id,
            sandbox={"enabled": True, "autoAllowBashIfSandboxed": True} if request.bash_sandbox else None,
        )

    async def run(self, request: RunRequest, emit: Emit) -> RunOutcome:
        options = self.build_options(request, emit)
        outcome = RunOutcome(ok=False, error="Agent ended without a result.")
        async for message in query(prompt=request.prompt, options=options):
            for type_, data in message_to_events(message):
                emit(type_, data)
            if isinstance(message, SystemMessage) and message.subtype == "init":
                outcome.session_id = message.data.get("session_id")
            if isinstance(message, ResultMessage):
                outcome = RunOutcome(
                    ok=not message.is_error,
                    session_id=message.session_id,
                    num_turns=message.num_turns,
                    cost_usd=message.total_cost_usd,
                    result=message.result,
                    error=None if not message.is_error else "; ".join(message.errors or []) or message.subtype,
                )
        return outcome

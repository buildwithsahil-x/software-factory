from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from app.agents.executor import RunRequest, SdkExecutor, message_to_events
from app.agents.registry import AGENTS


def test_message_to_events():
    init = SystemMessage(subtype="init", data={"session_id": "s1", "model": "claude-haiku-5-5", "tools": ["Read"]})
    assert message_to_events(init)[0] == (
        "session_started",
        {"session_id": "s1", "model": "claude-haiku-5-5", "tools": ["Read"], "skills": None, "plugins": None},
    )

    assistant = AssistantMessage(
        content=[TextBlock(text="Working on it"), ToolUseBlock(id="t1", name="Write", input={"file_path": "a.txt"})],
        model="claude-haiku-5-5",
    )
    assert [t for t, _ in message_to_events(assistant)] == ["assistant_text", "tool_use"]

    user = UserMessage(content=[ToolResultBlock(tool_use_id="t1", content=[{"type": "text", "text": "ok"}], is_error=False)])
    assert message_to_events(user) == [("tool_result", {"tool_use_id": "t1", "is_error": False, "output": "ok"})]

    result = ResultMessage(
        subtype="success", duration_ms=10, duration_api_ms=5, is_error=False, num_turns=3,
        session_id="s1", total_cost_usd=0.02, result="done",
    )
    (type_, data), = message_to_events(result)
    assert type_ == "result" and data["cost_usd"] == 0.02 and data["num_turns"] == 3


def test_long_values_are_truncated():
    big = "x" * 10_000
    (_, data), = message_to_events(AssistantMessage(content=[TextBlock(text=big)], model="m"))
    assert len(data["text"]) < 5000 and data["text"].endswith("[truncated]")


def test_build_options(tmp_path: Path):
    plugin_dir = Path(__file__).resolve().parents[2] / "factory-plugin"
    request = RunRequest(run_id=1, spec=AGENTS["smoke-test"], prompt="hi", cwd=tmp_path, plugin_dir=plugin_dir)
    options = SdkExecutor().build_options(request, emit=lambda *_: None)
    assert options.model == "claude-haiku-5-5"
    assert options.cwd == str(tmp_path)
    assert options.permission_mode == "dontAsk"
    assert options.setting_sources == []
    assert "Bash" in options.tools
    assert "mcp__factory__report_progress" in options.allowed_tools
    assert options.skills == ["factory:factory-conventions"]
    assert options.plugins == [{"type": "local", "path": str(plugin_dir)}]
    assert "PreToolUse" in options.hooks
    assert options.max_budget_usd == 0.50


async def test_guardrail_hook_denies_and_emits(tmp_path: Path):
    events = []
    request = RunRequest(run_id=1, spec=AGENTS["smoke-test"], prompt="hi", cwd=tmp_path)
    options = SdkExecutor().build_options(request, emit=lambda t, d: events.append((t, d)))
    hook = options.hooks["PreToolUse"][0].hooks[0]

    denied = await hook({"tool_name": "Bash", "tool_input": {"command": "terraform apply"}}, "t1", {"signal": None})
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert events and events[0][0] == "guardrail_block"

    allowed = await hook({"tool_name": "Bash", "tool_input": {"command": "ls"}}, "t2", {"signal": None})
    assert allowed == {}

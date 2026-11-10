"""Custom factory tools: how agents talk to the factory itself.

Exposed to agents as an in-process MCP server named `factory`, so tool names
appear to Claude as `mcp__factory__<tool>`.

Phase 0: `report_progress`. Later phases add `ask_user`,
`submit_for_approval`, `record_features` and `record_plan`.
"""

from collections.abc import Callable
from typing import Annotated, Any

from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool

from app.agents.registry import FACTORY_MCP_SERVER

Emit = Callable[[str, dict[str, Any]], Any]


def build_factory_server(emit: Emit, enabled: list[str]) -> McpSdkServerConfig:
    """Build the per-run tool server. `emit(type, data)` records an event."""

    @tool(
        "report_progress",
        "Report a milestone or status update to the factory dashboard. One short sentence.",
        {"message": Annotated[str, "What you just did or are about to do"]},
    )
    async def report_progress(args: dict[str, Any]) -> dict[str, Any]:
        message = str(args.get("message", "")).strip()[:500]
        emit("progress", {"message": message})
        return {"content": [{"type": "text", "text": "Recorded."}]}

    available = {"report_progress": report_progress}
    return create_sdk_mcp_server(
        name=FACTORY_MCP_SERVER,
        tools=[available[name] for name in enabled if name in available],
    )

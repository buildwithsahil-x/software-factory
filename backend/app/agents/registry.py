"""Agent definitions: role prompt, model, tools, skills and limits.

Each factory role from the plan (Product Analyst, Architect, Developer, ...) is
added here as its stage is built. Phase 0 ships one smoke-test agent to prove
the runtime, guardrails and live dashboard end to end.
"""

from dataclasses import dataclass, field

# Exact model IDs. Chosen per agent (plan §5.1), not the strongest everywhere.
OPUS = "claude-opus-5-5"
SONNET = "claude-sonnet-5-5"
HAIKU = "claude-haiku-5-5"

FACTORY_MCP_SERVER = "factory"

# Built-in Claude Code tools most agents need.
FILE_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep"]


@dataclass(frozen=True)
class AgentSpec:
    key: str
    name: str
    description: str
    model: str
    system_prompt: str
    tools: list[str] = field(default_factory=list)
    factory_tools: list[str] = field(default_factory=lambda: ["report_progress"])
    skills: list[str] = field(default_factory=list)
    effort: str | None = None
    max_turns: int = 30
    max_budget_usd: float = 1.0
    allowed_push_branches: frozenset[str] = frozenset()

    @property
    def allowed_tools(self) -> list[str]:
        return [*self.tools, *(f"mcp__{FACTORY_MCP_SERVER}__{t}" for t in self.factory_tools)]


_COMMON_RULES = """
Factory rules (always apply):
- Work only inside your current working directory (your workspace).
- Never create, change or delete Azure resources; never run terraform apply/plan.
- Never push to or merge into the dev, main or infra branches.
- Use the report_progress tool to report each meaningful milestone in one short sentence.
- If a tool call is blocked by a guardrail, do not try to work around it; explain and continue
  with what you are allowed to do.
""".strip()


SMOKE_TEST = AgentSpec(
    key="smoke-test",
    name="Smoke Test",
    description="Phase 0 test agent: does a small task in a scratch workspace so you can watch the runtime work.",
    model=HAIKU,
    effort="low",
    system_prompt=(
        "You are the Smoke Test agent of an AI software factory. You run in a scratch workspace "
        "and carry out small tasks the operator gives you, using files and shell commands.\n\n"
        + _COMMON_RULES
    ),
    tools=[*FILE_TOOLS, "Bash"],
    skills=["factory-conventions"],
    max_turns=20,
    max_budget_usd=0.50,
)


AGENTS: dict[str, AgentSpec] = {a.key: a for a in [SMOKE_TEST]}

# Software Factory

An AI agent software factory, run by one person in Claude Code. You describe an app, and the factory proposes features, writes specs and wireframes, prepares Azure infrastructure inputs, and builds, tests and security-scans each feature. A human approves every important step.

The full design is in [`docs/PLAN.md`](docs/PLAN.md).

## Status

The factory is being built as a **Claude Code plugin** (slash commands, skills, subagents and hooks) in `factory-plugin/`. Phase 1 starts that work.

The `backend/` and `frontend/` folders are the **phase 0 app skeleton**: a dashboard and agent runtime built on the Claude Agent SDK. They are kept for an optional dashboard later (plan §10, phase 9). Their guardrail rules are reused by the plugin's hooks.

What the phase 0 skeleton includes:

| Piece | Where |
|---|---|
| FastAPI backend: API, SQLite state, run manager with a concurrency cap | `backend/app/` |
| Agent runtime on the Claude Agent SDK (Claude Code as a library) | `backend/app/agents/executor.py` |
| Agent registry: role prompt, model, tools, skills and limits per agent | `backend/app/agents/registry.py` |
| Guardrail hook, checked before every tool call | `backend/app/agents/guardrails.py` |
| Custom factory tools (in-process MCP server `factory`): `report_progress` | `backend/app/agents/factory_tools.py` |
| Event bus: every agent action is stored (audit log) and streamed live (SSE) | `backend/app/events.py` |
| Factory plugin with skills (one per stage task as stages are built) | `factory-plugin/` |
| React dashboard: start runs, runs list, live activity feed | `frontend/` |

## Running the phase 0 skeleton

Requirements: Python 3.11+, Node 20+, git. Docker is needed from phase 5.

1. **Configure secrets.** Copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY`.

2. **Backend:**
   ```bash
   cd backend
   python3 -m venv .venv
   .venv/bin/pip install -e ".[dev]"
   .venv/bin/uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
   ```

3. **Frontend** (in another terminal):
   ```bash
   cd frontend
   npm install
   npm run dev
   ```

4. Open http://localhost:5173. Keep both servers on `localhost`; don't expose them to your network, because the backend holds your API key.

5. Pick the **Smoke Test** agent, give it a small task (for example *"Create hello.md with a short note, then try `terraform apply`"*), and watch the live activity feed. The `terraform apply` attempt shows up as a blocked guardrail event.

Factory data (database and agent workspaces) lives in `~/.software-factory` by default (`FACTORY_DATA_DIR`).

## Tests

```bash
cd backend && .venv/bin/python -m pytest -q
cd frontend && npm run lint && npm run build
```

Backend tests use a fake executor, so they never call the Claude API.

## How agents are constrained

The **real** enforcement is identity level, set up in later phases:
- an Azure identity with only the Reader role,
- a GitHub token that cannot bypass branch protection on `dev`/`main`,
- read-only access to the Terraform modules repo.

On top of that, every agent run gets:
- **Only its role's tools.** No other tools exist in the session, and the allowed ones are pre-approved so agents run unattended.
- **A guardrail hook** that blocks:
  - Azure write commands; only show/list/get-style `az` commands are allowed,
  - `terraform apply/plan/destroy/import/state`,
  - `kubectl`/`helm` changes,
  - pushes to `dev`/`main`/`infra` (the future Infra agent may push to `infra` only),
  - force pushes, branch deletion, `gh pr merge`, and non-GET `gh api` calls,
  - file access outside the agent's workspace.

  Blocked attempts appear on the dashboard.
- **Turn and dollar budgets** per agent.
- **No personal settings:** the operator's `~/.claude` settings are not loaded into agent sessions.

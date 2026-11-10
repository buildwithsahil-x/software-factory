import asyncio
from pathlib import Path


async def _wait_done(client, run_id: int) -> dict:
    await client.app.state.runs.wait(run_id)
    return (await client.get(f"/api/runs/{run_id}")).json()


async def test_health_and_agents(client):
    health = (await client.get("/api/health")).json()
    assert health["status"] == "ok"
    assert health["max_concurrent_agents"] == 3

    agents = (await client.get("/api/agents")).json()
    smoke = next(a for a in agents if a["key"] == "smoke-test")
    assert smoke["model"] == "claude-haiku-5-5"
    assert "mcp__factory__report_progress" in smoke["tools"]


async def test_run_lifecycle_records_status_cost_and_events(client, fake_executor):
    resp = await client.post("/api/runs", json={"agent": "smoke-test", "prompt": "write hello.txt"})
    assert resp.status_code == 201
    run_id = resp.json()["id"]

    run = await _wait_done(client, run_id)
    assert run["status"] == "succeeded"
    assert run["cost_usd"] == 0.01
    assert run["session_id"] == "sess-1"
    assert Path(run["workspace"]).is_dir()

    request = fake_executor.requests[0]
    assert request.cwd == Path(run["workspace"])
    assert request.spec.key == "smoke-test"

    events = (await client.get(f"/api/runs/{run_id}/events")).json()
    types = [e["type"] for e in events]
    assert types[0] == "status"  # queued
    assert {"session_started", "tool_use", "progress", "result"} <= set(types)
    assert [e["data"]["status"] for e in events if e["type"] == "status"] == ["queued", "running", "succeeded"]

    after = events[2]["id"]
    later = (await client.get(f"/api/runs/{run_id}/events", params={"after": after})).json()
    assert all(e["id"] > after for e in later)


async def test_failed_run_is_recorded(client, fake_executor):
    fake_executor.fail_with = RuntimeError("boom")
    run_id = (await client.post("/api/runs", json={"agent": "smoke-test", "prompt": "x"})).json()["id"]
    run = await _wait_done(client, run_id)
    assert run["status"] == "failed"
    assert "boom" in run["error"]


async def test_cancel_run(client, fake_executor):
    fake_executor.block.clear()
    run_id = (await client.post("/api/runs", json={"agent": "smoke-test", "prompt": "x"})).json()["id"]
    for _ in range(50):
        if (await client.get(f"/api/runs/{run_id}")).json()["status"] == "running":
            break
        await asyncio.sleep(0.01)
    assert (await client.post(f"/api/runs/{run_id}/cancel")).json() == {"cancelled": True}
    run = await _wait_done(client, run_id)
    assert run["status"] == "cancelled"


async def test_concurrency_limit(client, fake_executor):
    fake_executor.block.clear()
    ids = [(await client.post("/api/runs", json={"agent": "smoke-test", "prompt": str(i)})).json()["id"] for i in range(5)]
    await asyncio.sleep(0.05)
    statuses = [(await client.get(f"/api/runs/{i}")).json()["status"] for i in ids]
    assert statuses.count("running") == 3
    assert statuses.count("queued") == 2
    fake_executor.block.set()
    for i in ids:
        assert (await _wait_done(client, i))["status"] == "succeeded"


async def test_validation(client):
    assert (await client.post("/api/runs", json={"agent": "nope", "prompt": "x"})).status_code == 400
    assert (await client.post("/api/runs", json={"agent": "smoke-test", "prompt": ""})).status_code == 422
    assert (await client.post("/api/runs", json={"agent": "smoke-test", "prompt": "x", "project_id": 99})).status_code == 404
    assert (await client.get("/api/runs/999")).status_code == 404


async def test_projects_and_project_workspace(client):
    resp = await client.post("/api/projects", json={"name": "Expense tracker", "repo": "acme/expenses"})
    assert resp.status_code == 201
    project = resp.json()
    assert (await client.post("/api/projects", json={"name": "x", "repo": "not a repo"})).status_code == 422

    run_id = (await client.post("/api/runs", json={"agent": "smoke-test", "prompt": "x", "project_id": project["id"]})).json()["id"]
    run = await _wait_done(client, run_id)
    assert run["workspace"].endswith(f"project-{project['id']}")
    listed = (await client.get("/api/runs", params={"project_id": project["id"]})).json()
    assert [r["id"] for r in listed] == [run_id]


async def test_interrupted_runs_marked_failed_on_restart(tmp_path):
    from app.config import Settings
    from app.main import create_app
    from app.models import AgentRun, RunStatus
    from sqlmodel import Session

    settings = Settings(data_dir=tmp_path / "data", _env_file=None)
    app = create_app(settings)
    with Session(app.state.engine) as s:
        s.add(AgentRun(agent="smoke-test", model="m", prompt="x", status=RunStatus.RUNNING))
        s.commit()
    async with app.router.lifespan_context(app):
        with Session(app.state.engine) as s:
            run = s.get(AgentRun, 1)
            assert run.status == RunStatus.FAILED
            assert "restarted" in run.error

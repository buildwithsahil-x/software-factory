import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest

from app.agents.executor import RunOutcome, RunRequest
from app.config import Settings
from app.main import create_app


class FakeExecutor:
    """Stands in for the Claude Agent SDK: emits a scripted set of events."""

    def __init__(self) -> None:
        self.requests: list[RunRequest] = []
        self.block = asyncio.Event()
        self.block.set()
        self.fail_with: Exception | None = None

    async def run(self, request: RunRequest, emit) -> RunOutcome:
        self.requests.append(request)
        emit("session_started", {"session_id": "sess-1", "model": request.spec.model})
        await self.block.wait()
        if self.fail_with:
            raise self.fail_with
        emit("tool_use", {"id": "t1", "name": "Write", "input": {"file_path": "hello.txt"}})
        emit("progress", {"message": "Wrote hello.txt"})
        emit("result", {"subtype": "success", "num_turns": 2, "cost_usd": 0.01})
        return RunOutcome(ok=True, session_id="sess-1", num_turns=2, cost_usd=0.01, result="done")


@pytest.fixture
def fake_executor() -> FakeExecutor:
    return FakeExecutor()


@pytest.fixture
async def client(tmp_path, fake_executor) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(data_dir=tmp_path / "data", _env_file=None)
    app = create_app(settings, executor=fake_executor)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            c.app = app  # type: ignore[attr-defined]
            yield c

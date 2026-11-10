"""Run manager: queues agent runs, limits concurrency, records outcomes.

Phase 0 runs one agent task per request. The stage state machine and approval
gates (phase 1+) build on top of this.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import Engine
from sqlmodel import Session, select

from app.agents.executor import AgentExecutor, RunRequest
from app.agents.registry import AGENTS
from app.config import Settings
from app.events import EventBus
from app.models import AgentRun, RunStatus, utcnow

log = logging.getLogger(__name__)


class RunManager:
    def __init__(self, settings: Settings, engine: Engine, bus: EventBus, executor: AgentExecutor) -> None:
        self._settings = settings
        self._engine = engine
        self._bus = bus
        self._executor = executor
        self._slots = asyncio.Semaphore(settings.max_concurrent_agents)
        self._tasks: dict[int, asyncio.Task] = {}

    # ----------------------------------------------------------------- lifecycle

    def recover_interrupted_runs(self) -> None:
        """Runs left unfinished by a factory restart are marked failed."""
        with Session(self._engine) as session:
            stale = session.exec(
                select(AgentRun).where(AgentRun.status.in_([RunStatus.QUEUED, RunStatus.RUNNING]))
            ).all()
            for run in stale:
                run.status = RunStatus.FAILED
                run.error = "Interrupted: the factory was restarted while this run was active."
                run.finished_at = utcnow()
                session.add(run)
            session.commit()

    async def shutdown(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    # ----------------------------------------------------------------- runs

    def workspace_for(self, run: AgentRun) -> Path:
        if run.project_id is not None:
            path = self._settings.workspaces_dir / f"project-{run.project_id}"
        else:
            path = self._settings.workspaces_dir / "_scratch" / f"run-{run.id}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def create_run(self, agent_key: str, prompt: str, project_id: int | None = None) -> AgentRun:
        spec = AGENTS[agent_key]
        with Session(self._engine, expire_on_commit=False) as session:
            run = AgentRun(agent=agent_key, model=spec.model, prompt=prompt, project_id=project_id)
            session.add(run)
            session.commit()
            run.workspace = str(self.workspace_for(run))
            session.add(run)
            session.commit()
        self._publish_status(run)
        self._tasks[run.id] = asyncio.create_task(self._execute(run.id), name=f"agent-run-{run.id}")
        return run

    def cancel(self, run_id: int) -> bool:
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def wait(self, run_id: int) -> None:
        task = self._tasks.get(run_id)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    # ----------------------------------------------------------------- internals

    def _update(self, run_id: int, **fields: Any) -> AgentRun:
        with Session(self._engine, expire_on_commit=False) as session:
            run = session.get(AgentRun, run_id)
            assert run is not None
            for key, value in fields.items():
                setattr(run, key, value)
            session.add(run)
            session.commit()
        self._publish_status(run)
        return run

    def _publish_status(self, run: AgentRun) -> None:
        self._bus.publish(run.id, "status", {"status": run.status, "error": run.error})

    async def _execute(self, run_id: int) -> None:
        try:
            async with self._slots:
                run = self._update(run_id, status=RunStatus.RUNNING, started_at=utcnow())
                request = RunRequest(
                    run_id=run_id,
                    spec=AGENTS[run.agent],
                    prompt=run.prompt,
                    cwd=Path(run.workspace or self.workspace_for(run)),
                    plugin_dir=self._settings.plugin_dir,
                    bash_sandbox=self._settings.bash_sandbox,
                )

                def emit(type_: str, data: dict[str, Any]) -> None:
                    self._bus.publish(run_id, type_, data)

                outcome = await self._executor.run(request, emit)
                self._update(
                    run_id,
                    status=RunStatus.SUCCEEDED if outcome.ok else RunStatus.FAILED,
                    session_id=outcome.session_id,
                    num_turns=outcome.num_turns,
                    cost_usd=outcome.cost_usd,
                    result=outcome.result,
                    error=outcome.error,
                    finished_at=utcnow(),
                )
        except asyncio.CancelledError:
            self._update(run_id, status=RunStatus.CANCELLED, error="Cancelled by the operator.", finished_at=utcnow())
        except Exception as exc:  # noqa: BLE001 - any agent failure must be recorded, not crash the factory
            log.exception("Agent run %s failed", run_id)
            self._bus.publish(run_id, "error", {"message": f"{type(exc).__name__}: {exc}"})
            self._update(run_id, status=RunStatus.FAILED, error=f"{type(exc).__name__}: {exc}", finished_at=utcnow())
        finally:
            self._tasks.pop(run_id, None)


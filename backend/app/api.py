"""HTTP API for the factory UI."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select
from sse_starlette.sse import EventSourceResponse

from app import __version__
from app.agents.registry import AGENTS
from app.events import ALL
from app.models import AgentRun, Project

router = APIRouter(prefix="/api")


def _state(request: Request):
    return request.app.state


# --------------------------------------------------------------------------- health / agents


@router.get("/health")
def health(request: Request) -> dict:
    settings = _state(request).settings
    return {
        "status": "ok",
        "version": __version__,
        "api_key_configured": bool(settings.anthropic_api_key),
        "max_concurrent_agents": settings.max_concurrent_agents,
    }


@router.get("/agents")
def list_agents() -> list[dict]:
    return [
        {
            "key": a.key,
            "name": a.name,
            "description": a.description,
            "model": a.model,
            "tools": a.allowed_tools,
            "skills": a.skills,
            "max_turns": a.max_turns,
            "max_budget_usd": a.max_budget_usd,
        }
        for a in AGENTS.values()
    ]


# --------------------------------------------------------------------------- projects


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    repo: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


@router.post("/projects", status_code=201)
def create_project(body: ProjectCreate, request: Request) -> Project:
    with Session(_state(request).engine, expire_on_commit=False) as session:
        project = Project(name=body.name, repo=body.repo)
        session.add(project)
        session.commit()
        return project


@router.get("/projects")
def list_projects(request: Request) -> list[Project]:
    with Session(_state(request).engine) as session:
        return list(session.exec(select(Project).order_by(col(Project.id).desc())).all())


@router.get("/projects/{project_id}")
def get_project(project_id: int, request: Request) -> Project:
    with Session(_state(request).engine) as session:
        project = session.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        return project


# --------------------------------------------------------------------------- runs


class RunCreate(BaseModel):
    agent: str
    prompt: str = Field(min_length=1, max_length=20_000)
    project_id: int | None = None


@router.post("/runs", status_code=201)
async def create_run(body: RunCreate, request: Request) -> AgentRun:
    state = _state(request)
    if body.agent not in AGENTS:
        raise HTTPException(400, f"Unknown agent '{body.agent}'")
    if body.project_id is not None:
        with Session(state.engine) as session:
            if session.get(Project, body.project_id) is None:
                raise HTTPException(404, "Project not found")
    return state.runs.create_run(body.agent, body.prompt, body.project_id)


@router.get("/runs")
def list_runs(request: Request, project_id: int | None = None, limit: int = Query(50, le=200)) -> list[AgentRun]:
    with Session(_state(request).engine) as session:
        stmt = select(AgentRun).order_by(col(AgentRun.id).desc()).limit(limit)
        if project_id is not None:
            stmt = stmt.where(AgentRun.project_id == project_id)
        return list(session.exec(stmt).all())


@router.get("/runs/{run_id}")
def get_run(run_id: int, request: Request) -> AgentRun:
    with Session(_state(request).engine) as session:
        run = session.get(AgentRun, run_id)
        if run is None:
            raise HTTPException(404, "Run not found")
        return run


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: int, request: Request) -> dict:
    get_run(run_id, request)
    return {"cancelled": _state(request).runs.cancel(run_id)}


@router.get("/runs/{run_id}/events")
def run_events(run_id: int, request: Request, after: int = 0) -> list[dict]:
    get_run(run_id, request)
    return _state(request).bus.history(run_id, after_id=after)


# --------------------------------------------------------------------------- live streams (SSE)


def _sse(payload: dict) -> dict:
    return {"id": str(payload["id"]), "event": payload["type"], "data": json.dumps(payload)}


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: int, request: Request, after: int = 0) -> EventSourceResponse:
    """History after `after` (or Last-Event-ID), then live events for one run."""
    get_run(run_id, request)
    bus = _state(request).bus
    last_id = int(request.headers.get("last-event-id") or after or 0)

    async def generator() -> AsyncIterator[dict]:
        nonlocal last_id
        queue = bus.subscribe(run_id)
        try:
            for payload in bus.history(run_id, after_id=last_id):
                last_id = payload["id"]
                yield _sse(payload)
            while True:
                payload = await queue.get()
                if payload["id"] <= last_id:
                    continue
                last_id = payload["id"]
                yield _sse(payload)
        finally:
            bus.unsubscribe(run_id, queue)

    return EventSourceResponse(generator(), ping=15)


@router.get("/stream")
async def stream_all(request: Request) -> EventSourceResponse:
    """Run status changes across the factory (for the runs list)."""
    bus = _state(request).bus

    async def generator() -> AsyncIterator[dict]:
        queue = bus.subscribe(ALL)
        try:
            while True:
                payload = await queue.get()
                if payload["type"] in {"status", "progress", "guardrail_block"}:
                    yield _sse(payload)
        finally:
            bus.unsubscribe(ALL, queue)

    return EventSourceResponse(generator(), ping=15)

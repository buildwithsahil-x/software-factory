"""Database tables for factory state."""

from datetime import datetime, timezone
from enum import StrEnum

from sqlmodel import JSON, Column, Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}


class Project(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    repo: str | None = None  # GitHub "owner/name", given by the user
    stage: str = "setup"
    created_at: datetime = Field(default_factory=utcnow)


class AgentRun(SQLModel, table=True):
    """One agent session doing one task."""

    id: int | None = Field(default=None, primary_key=True)
    project_id: int | None = Field(default=None, foreign_key="project.id", index=True)
    agent: str
    model: str
    prompt: str
    status: RunStatus = RunStatus.QUEUED
    workspace: str | None = None
    session_id: str | None = None
    num_turns: int | None = None
    cost_usd: float | None = None
    result: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class Event(SQLModel, table=True):
    """Everything an agent does, for the dashboard and the audit log."""

    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="agentrun.id", index=True)
    ts: datetime = Field(default_factory=utcnow)
    type: str
    data: dict = Field(default_factory=dict, sa_column=Column(JSON))

"""FastAPI application entry point.

Run locally (from the backend/ folder):
    uvicorn app.main:create_app --factory --reload
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agents.executor import AgentExecutor, SdkExecutor
from app.api import router
from app.config import Settings
from app.db import init_db, make_engine
from app.events import EventBus
from app.runner import RunManager


def create_app(settings: Settings | None = None, executor: AgentExecutor | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.ensure_dirs()
    # Agents run as a Claude Code subprocess, which reads the key from the
    # environment; a key given only in .env is passed on here.
    if settings.anthropic_api_key and not os.environ.get("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = settings.anthropic_api_key
    logging.basicConfig(level=logging.INFO)
    engine = make_engine(settings.db_path)
    init_db(engine)
    bus = EventBus(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runs = RunManager(settings, engine, bus, executor or SdkExecutor())
        runs.recover_interrupted_runs()
        app.state.runs = runs
        yield
        await runs.shutdown()

    app = FastAPI(title="Software Factory", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.bus = bus
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app

"""Event bus: every agent action is stored (audit log) and pushed live to the
dashboard over Server-Sent Events.

Subscribers listen to one run (`run_id`) or to everything (`ALL`).
"""

import asyncio
from collections import defaultdict
from typing import Any

from sqlalchemy import Engine
from sqlmodel import Session, select

from app.models import Event

ALL = "*"
_QUEUE_SIZE = 1000


def event_to_dict(event: Event) -> dict[str, Any]:
    return {
        "id": event.id,
        "run_id": event.run_id,
        "ts": event.ts.isoformat(),
        "type": event.type,
        "data": event.data,
    }


class EventBus:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._subscribers: dict[int | str, set[asyncio.Queue]] = defaultdict(set)

    def publish(self, run_id: int, type_: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Store an event and fan it out to live subscribers."""
        with Session(self._engine, expire_on_commit=False) as session:
            event = Event(run_id=run_id, type=type_, data=data or {})
            session.add(event)
            session.commit()
        payload = event_to_dict(event)
        for key in (run_id, ALL):
            for queue in list(self._subscribers.get(key, ())):
                try:
                    queue.put_nowait(payload)
                except asyncio.QueueFull:
                    # A stalled browser tab must not block agents; it can
                    # reload history from the API.
                    pass
        return payload

    def subscribe(self, key: int | str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_SIZE)
        self._subscribers[key].add(queue)
        return queue

    def unsubscribe(self, key: int | str, queue: asyncio.Queue) -> None:
        self._subscribers[key].discard(queue)
        if not self._subscribers[key]:
            del self._subscribers[key]

    def history(self, run_id: int, after_id: int = 0) -> list[dict[str, Any]]:
        with Session(self._engine) as session:
            rows = session.exec(
                select(Event)
                .where(Event.run_id == run_id, Event.id > after_id)
                .order_by(Event.id)
            ).all()
            return [event_to_dict(e) for e in rows]

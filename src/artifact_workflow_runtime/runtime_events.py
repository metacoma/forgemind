from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from inspect import isawaitable
from typing import Any, Awaitable, Callable


EventSink = Callable[["RuntimeEvent"], Awaitable[None] | None]


def event_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class RuntimeEvent:
    kind: str
    stage: str
    message: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=event_now)


async def emit_event(event_sink: EventSink | None, kind: str, stage: str, message: str, payload: dict[str, Any] | None = None) -> None:
    if event_sink is None:
        return
    event = RuntimeEvent(kind=kind, stage=stage, message=message, payload=payload or {})
    result = event_sink(event)
    if isawaitable(result):
        await result

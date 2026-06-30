from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

JsonDict = dict[str, Any]


@dataclass(slots=True)
class AppConversationStart:
    conversation_id: str
    task_id: str | None = None
    status: str | None = None
    sandbox_id: str | None = None
    agent_server_url: str | None = None
    conversation_url: str | None = None
    session_api_key: str | None = None
    raw_task: JsonDict = field(default_factory=dict)
    raw_conversation: JsonDict | None = None


@dataclass(slots=True)
class OpenHandsRunResult:
    text: str
    status: str | None
    conversation_id: str
    start: AppConversationStart
    seen_event_ids: frozenset[str] = field(default_factory=frozenset)

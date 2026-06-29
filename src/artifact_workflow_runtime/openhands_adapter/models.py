from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

JsonDict = dict[str, Any]


class OpenHandsModel(BaseModel):
    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True, populate_by_name=True)


class AppConversationStart(OpenHandsModel):
    conversation_id: str
    task_id: str | None = None
    status: str | None = None
    sandbox_id: str | None = None
    agent_server_url: str | None = None
    conversation_url: str | None = None
    session_api_key: str | None = Field(default=None, exclude=True, repr=False)
    raw_task: JsonDict | None = Field(default=None, repr=False)
    raw_conversation: JsonDict | None = Field(default=None, repr=False)


class OpenHandsRunResult(OpenHandsModel):
    text: str
    status: str | None = None
    conversation_id: str
    start: AppConversationStart
    seen_event_ids: frozenset[str] = Field(default_factory=frozenset)

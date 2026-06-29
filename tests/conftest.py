from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from aiohttp import web

JsonDict = dict[str, Any]


def _message_event(event_id: str, text: str, *, source: str = "agent") -> JsonDict:
    role = "assistant" if source == "agent" else "user"
    return {
        "id": event_id,
        "kind": "MessageEvent",
        "source": source,
        "llm_message": {
            "role": role,
            "content": [{"type": "text", "text": text}],
        },
    }


def _status_event(event_id: str, status: str) -> JsonDict:
    return {
        "id": event_id,
        "kind": "ConversationStateUpdateEvent",
        "source": "environment",
        "key": "execution_status",
        "value": status,
    }


def main_run_events(answer: str = "main answer") -> list[JsonDict]:
    return [
        _status_event("main-running", "running"),
        _message_event("main-answer", answer),
        _status_event("main-finished", "finished"),
    ]


@dataclass
class FakeOpenHandsServer:
    main_events: list[JsonDict] = field(default_factory=main_run_events)
    conversation_id: str = "conv-1"
    sandbox_id: str = "sb-1"
    llm_model: str = "openai/executor"
    task_id: str = "task-1"
    app_runner: web.AppRunner | None = None
    site: web.TCPSite | None = None
    endpoint: str = ""
    created_payloads: list[JsonDict] = field(default_factory=list)
    patched_payloads: list[JsonDict] = field(default_factory=list)
    followup_payloads: list[JsonDict] = field(default_factory=list)
    title: str | None = None
    _conversation_counter: int = 0

    async def start(self) -> "FakeOpenHandsServer":
        app = web.Application()
        app.router.add_post("/api/v1/app-conversations", self.handle_create_app_conversation)
        app.router.add_patch("/api/v1/app-conversations/{conversation_id}", self.handle_patch_app_conversation)
        app.router.add_get("/api/v1/app-conversations/start-tasks", self.handle_start_tasks)
        app.router.add_get("/api/v1/app-conversations", self.handle_get_app_conversation)
        app.router.add_get("/api/v1/app-conversations/search", self.handle_search_app_conversations)
        app.router.add_route("*", "/api/conversations/{conversation_id}/events", self.handle_conversation_events)
        app.router.add_get("/api/conversations/{conversation_id}/messages", self.handle_conversation_messages)
        app.router.add_get("/sockets/events/{conversation_id}", self.handle_websocket)
        self.app_runner = web.AppRunner(app)
        await self.app_runner.setup()
        self.site = web.TCPSite(self.app_runner, "127.0.0.1", 0)
        await self.site.start()
        sockets = self.site._server.sockets  # type: ignore[union-attr, protected-access]
        port = sockets[0].getsockname()[1]
        self.endpoint = f"http://127.0.0.1:{port}"
        return self

    async def stop(self) -> None:
        if self.app_runner:
            await self.app_runner.cleanup()

    def conversation_record(self) -> JsonDict:
        return {
            "id": self.conversation_id,
            "sandbox_id": self.sandbox_id,
            "llm_model": self.llm_model,
            "conversation_url": f"{self.endpoint}/api/conversations/{self.conversation_id}",
            "agent_server_url": self.endpoint,
            "session_api_key": "fake-session-key",
            "execution_status": "running",
            "title": self.title or f"Conversation {self.conversation_id[:5]}",
        }

    async def handle_create_app_conversation(self, request: web.Request) -> web.Response:
        payload = await request.json()
        self.created_payloads.append(payload)
        self._conversation_counter += 1
        self.conversation_id = f"conv-{self._conversation_counter}"
        self.task_id = f"task-{self._conversation_counter}"
        return web.json_response({"id": self.task_id, "status": "WORKING"})

    async def handle_patch_app_conversation(self, request: web.Request) -> web.Response:
        payload = await request.json()
        self.patched_payloads.append(payload)
        if "title" in payload:
            self.title = str(payload["title"])
        record = self.conversation_record()
        record.update(payload)
        return web.json_response(record)

    async def handle_start_tasks(self, request: web.Request) -> web.Response:
        return web.json_response([
            {
                "id": self.task_id,
                "status": "READY",
                "app_conversation_id": self.conversation_id,
                "conversation_url": f"{self.endpoint}/api/conversations/{self.conversation_id}",
                "agent_server_url": self.endpoint,
                "session_api_key": "fake-session-key",
            }
        ])

    async def handle_get_app_conversation(self, request: web.Request) -> web.Response:
        return web.json_response([self.conversation_record()])

    async def handle_search_app_conversations(self, request: web.Request) -> web.Response:
        sandbox_eq = request.query.get("sandbox_id__eq")
        record = self.conversation_record()
        items = [record]
        if sandbox_eq and sandbox_eq != self.sandbox_id:
            items = []
        return web.json_response({"items": items})

    async def handle_conversation_events(self, request: web.Request) -> web.Response:
        if request.method == "POST":
            payload = await request.json()
            self.followup_payloads.append(payload)
            return web.json_response({"success": True})
        return web.json_response(self.main_events)

    async def handle_conversation_messages(self, request: web.Request) -> web.Response:
        messages = [event for event in self.main_events if event.get("kind") == "MessageEvent"]
        return web.json_response(messages)

    async def handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        for event in self.main_events:
            await ws.send_str(json.dumps(event))
            await asyncio.sleep(0)
        await ws.close()
        return ws


@pytest.fixture
async def fake_openhands_server() -> FakeOpenHandsServer:
    server = await FakeOpenHandsServer().start()
    try:
        yield server
    finally:
        await server.stop()

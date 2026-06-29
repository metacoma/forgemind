from __future__ import annotations

import httpx
import pytest

from artifact_workflow_runtime.openhands_adapter.client import OpenHandsClient
from artifact_workflow_runtime.openhands_adapter.models import AppConversationStart

pytestmark = pytest.mark.asyncio


async def test_request_returns_raw_text_for_non_json_success(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("GET", "http://openhands/api/conversations/conv-1/messages")
    response = httpx.Response(200, request=request, content=b"plain assistant text", headers={"content-type": "text/plain"})

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method, url, json=None, params=None, headers=None):
            return response

    monkeypatch.setattr(httpx, "AsyncClient", DummyAsyncClient)

    client = OpenHandsClient("http://openhands")
    data = await client._request("GET", "/api/conversations/conv-1/messages")
    assert data == {"raw_text": "plain assistant text", "content_type": "text/plain"}


async def test_fetch_final_text_fallback_uses_raw_text(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_request(method: str, path: str, **kwargs):
        return {"raw_text": "plain assistant text", "content_type": "text/plain"}

    client = OpenHandsClient("http://openhands")
    monkeypatch.setattr(client, "_request", fake_request)
    start = AppConversationStart(
        conversation_id="conv-1",
        task_id=None,
        status="READY",
        sandbox_id=None,
        agent_server_url=None,
        conversation_url=None,
        session_api_key="session-key",
        raw_task={},
        raw_conversation={},
    )

    text = await client.fetch_final_text_fallback(start)
    assert text == "plain assistant text"

from __future__ import annotations

import httpx
import pytest

from artifact_workflow_runtime.openhands_adapter.client import OpenHandsClient, OpenHandsError
from artifact_workflow_runtime.openhands_adapter.models import AppConversationStart

pytestmark = pytest.mark.asyncio


async def test_request_rejects_non_json_success(monkeypatch: pytest.MonkeyPatch) -> None:
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
    with pytest.raises(OpenHandsError, match="returned non-JSON response"):
        await client._request("GET", "/api/conversations/conv-1/messages")


async def test_fetch_final_text_fallback_ignores_non_json_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("GET", "http://openhands/api/conversations/conv-1/messages")
    response = httpx.Response(200, request=request, content=b"<!DOCTYPE html><html></html>", headers={"content-type": "text/html"})

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url, headers=None):
            return response

    monkeypatch.setattr(httpx, "AsyncClient", DummyAsyncClient)

    client = OpenHandsClient("http://openhands")
    start = AppConversationStart(
        conversation_id="conv-1",
        task_id=None,
        status="READY",
        sandbox_id=None,
        agent_server_url="http://openhands",
        conversation_url="http://openhands/api/conversations/conv-1",
        session_api_key="session-key",
        raw_task={},
        raw_conversation={},
    )

    text = await client.fetch_final_text_fallback(start)
    assert text == ""

async def test_fetch_final_text_fallback_ignores_json_wrapped_openhands_html(monkeypatch: pytest.MonkeyPatch) -> None:
    html = "<!DOCTYPE html><html><head><title>OpenHands</title></head><body>SPA</body></html>"
    request = httpx.Request("GET", "http://openhands/api/conversations/conv-1/messages")
    response = httpx.Response(
        200,
        request=request,
        json={"messages": [{"kind": "MessageEvent", "source": "agent", "message": html}]},
        headers={"content-type": "application/json"},
    )

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url, headers=None):
            return response

    monkeypatch.setattr(httpx, "AsyncClient", DummyAsyncClient)

    client = OpenHandsClient("http://openhands")
    start = AppConversationStart(
        conversation_id="conv-1",
        agent_server_url="http://openhands",
        conversation_url="http://openhands/api/conversations/conv-1",
        session_api_key="session-key",
    )

    assert await client.fetch_final_text_fallback(start) == ""

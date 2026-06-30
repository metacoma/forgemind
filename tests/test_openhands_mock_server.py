from __future__ import annotations

import pytest

from artifact_workflow_runtime.openhands_adapter import run_conversation_and_collect

pytestmark = pytest.mark.asyncio


async def test_run_conversation_collects_final_answer(fake_openhands_server) -> None:
    result = await run_conversation_and_collect(
        endpoint=fake_openhands_server.endpoint,
        prompt="study repo",
        llm_model="openai/executor",
        start_poll_interval=0,
        websocket_retry_seconds=1,
        terminal_grace_seconds=0,
    )
    assert result.text == "main answer"
    assert result.conversation_id == fake_openhands_server.conversation_id
    assert fake_openhands_server.created_payloads == [
        {
            "initial_message": {"content": [{"type": "text", "text": "study repo"}]},
            "llm_model": "openai/executor",
        }
    ]

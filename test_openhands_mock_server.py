from __future__ import annotations

import pytest

from artifact_workflow_runtime.openhands_adapter import run_conversation_and_collect, run_followup_message_and_collect

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


async def test_followup_uses_existing_agent_conversation_not_new_app_conversation(fake_openhands_server) -> None:
    main = await run_conversation_and_collect(
        endpoint=fake_openhands_server.endpoint,
        prompt="study repo",
        llm_model="openai/executor",
        start_poll_interval=0,
        websocket_retry_seconds=1,
        terminal_grace_seconds=0,
    )

    followup = await run_followup_message_and_collect(
        endpoint=fake_openhands_server.endpoint,
        conversation=main.start,
        prompt="summarize as JSON",
        known_event_ids=main.seen_event_ids,
        websocket_retry_seconds=1,
        terminal_grace_seconds=0,
    )

    assert followup.text.startswith('{"valid": true')
    assert len(fake_openhands_server.created_payloads) == 1
    assert len(fake_openhands_server.followup_payloads) == 1
    assert fake_openhands_server.followup_payloads[0]["role"] == "user"
    assert fake_openhands_server.followup_payloads[0]["run"] is True
    assert fake_openhands_server.followup_payloads[0]["content"][0]["text"] == "summarize as JSON"

from __future__ import annotations

from artifact_workflow_runtime.cli import build_parser
from artifact_workflow_runtime.openhands_adapter import OpenHandsInstance


def test_reuse_flag_defaults_to_false() -> None:
    args = build_parser().parse_args([
        "--task", "inspect repo",
        "--direct-llm-endpoint", "http://llm",
        "--direct-llm-model", "openai/reasoner",
        "--openhands-endpoint", "http://openhands",
        "--openhands-model", "openai/executor",
    ])
    assert args.reuse is False


def test_reuse_flag_set_when_present() -> None:
    args = build_parser().parse_args([
        "--task", "inspect repo",
        "--direct-llm-endpoint", "http://llm",
        "--direct-llm-model", "openai/reasoner",
        "--openhands-endpoint", "http://openhands",
        "--openhands-model", "openai/executor",
        "--reuse",
    ])
    assert args.reuse is True


async def test_openhands_instance_reuses_existing_sandbox(fake_openhands_server) -> None:
    instance = OpenHandsInstance(
        fake_openhands_server.endpoint,
        default_model=fake_openhands_server.llm_model,
        reuse_sandbox=True,
    )
    result = await instance.run(prompt="observe repo")
    assert result.start.sandbox_id == fake_openhands_server.sandbox_id
    assert fake_openhands_server.created_payloads == [
        {
            "initial_message": {"content": [{"type": "text", "text": "observe repo"}]},
            "llm_model": fake_openhands_server.llm_model,
            "sandbox_id": fake_openhands_server.sandbox_id,
        }
    ]


async def test_openhands_instance_reuses_sandbox_but_starts_new_conversation_each_run(fake_openhands_server) -> None:
    instance = OpenHandsInstance(
        fake_openhands_server.endpoint,
        default_model=fake_openhands_server.llm_model,
        reuse_sandbox=True,
    )
    first = await instance.run(prompt="observe repo")
    second = await instance.run(prompt="verify repo")
    assert first.start.sandbox_id == second.start.sandbox_id == fake_openhands_server.sandbox_id
    assert first.conversation_id != second.conversation_id
    assert len(fake_openhands_server.created_payloads) == 2
    assert fake_openhands_server.followup_payloads == []
    assert fake_openhands_server.created_payloads[0]["sandbox_id"] == fake_openhands_server.sandbox_id
    assert fake_openhands_server.created_payloads[1]["sandbox_id"] == fake_openhands_server.sandbox_id

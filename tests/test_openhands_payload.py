from __future__ import annotations

import pytest

from artifact_workflow_runtime.openhands_adapter import OpenHandsError, build_app_conversation_payload


def test_build_payload_sends_only_explicit_fields() -> None:
    payload = build_app_conversation_payload(
        prompt="hello",
        llm_model="openai/executor",
        selected_repository="metacoma/freeplane_plugin_grpc",
        git_provider="github",
        secret=["GITHUB_TOKEN=secret"],
    )
    assert payload == {
        "initial_message": {"content": [{"type": "text", "text": "hello"}]},
        "llm_model": "openai/executor",
        "selected_repository": "metacoma/freeplane_plugin_grpc",
        "git_provider": "github",
        "secrets": {"GITHUB_TOKEN": "secret"},
    }
    assert "sandbox_id" not in payload


def test_build_payload_requires_initial_message() -> None:
    with pytest.raises(OpenHandsError, match="No initial_message"):
        build_app_conversation_payload(llm_model="openai/executor")

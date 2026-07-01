from __future__ import annotations

import pytest

from artifact_workflow_runtime.model_routing import DEFAULT_CANONICAL_MODEL
from artifact_workflow_runtime.runtime_factory import build_controller
from artifact_workflow_runtime.strategy import StrategySelectionMode


def _build(tmp_path, *, strategy_selection_mode=StrategySelectionMode.RULE_BASED):
    return build_controller(
        artifact_dir=str(tmp_path),
        direct_llm_endpoint="http://llm",
        direct_llm_model="openai/reasoner",
        direct_llm_api_key=None,
        openhands_endpoint="http://openhands",
        openhands_model="openai/executor",
        openhands_api_key=None,
        reuse=False,
        sandbox_id=None,
        conversation_id=None,
        auto_approve=False,
        strategy_selection_mode=strategy_selection_mode,
    )


def test_build_controller_defaults_to_rule_based_strategy_mode(tmp_path) -> None:
    controller = _build(tmp_path)

    assert controller.services.strategy_selection_mode == StrategySelectionMode.RULE_BASED


def test_build_controller_passes_hybrid_strategy_mode(tmp_path) -> None:
    controller = _build(tmp_path, strategy_selection_mode="hybrid")

    assert controller.services.strategy_selection_mode == StrategySelectionMode.HYBRID


def test_build_controller_normalizes_string_strategy_mode(tmp_path) -> None:
    controller = _build(tmp_path, strategy_selection_mode="llm-assisted")

    assert controller.services.strategy_selection_mode == StrategySelectionMode.LLM_ASSISTED


def test_build_controller_rejects_invalid_strategy_mode(tmp_path) -> None:
    with pytest.raises(ValueError, match="Unknown strategy selection mode"):
        _build(tmp_path, strategy_selection_mode="magic")


def test_build_controller_installs_default_model_routing_without_config(tmp_path) -> None:
    controller = build_controller(
        artifact_dir=str(tmp_path),
        direct_llm_endpoint="http://llm",
        direct_llm_api_key=None,
        openhands_endpoint="http://openhands",
        openhands_api_key=None,
        reuse=False,
        sandbox_id=None,
        conversation_id=None,
        auto_approve=False,
    )

    routing = controller.services.model_routing
    assert routing is not None
    assert routing.resolve_direct_llm("classify", None) == DEFAULT_CANONICAL_MODEL
    assert routing.resolve_openhands_transport("execute", None) == "openai/qwen36-35b"
    assert routing.resolve_verification_check("future unknown check", None) == DEFAULT_CANONICAL_MODEL

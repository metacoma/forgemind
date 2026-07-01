from __future__ import annotations

from pathlib import Path

import pytest

from artifact_workflow_runtime.model_routing import (
    DEFAULT_CANONICAL_MODEL,
    ModelRoutingConfig,
    ModelRoutingConfigError,
    load_model_routing_config,
    normalize_canonical_model_name,
    resolve_openhands_transport_model,
)


def test_load_stage_based_models_uses_canonical_names(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """direct_llm:
  classify: qwen36-27b
  route: qwen36-35b
  obligations: qwen36-35b
  plan: qwen36-35b
  verify: qwen36-27b
openhands:
  observe: qwen36-27b
  research: qwen36-27b
  execute: qwen36-35b
  publish: qwen36-35b
"""
    )
    routing = load_model_routing_config(str(cfg))
    assert routing is not None
    assert routing.resolve_direct_llm("classify", "fallback") == "qwen36-27b"
    assert routing.resolve_direct_llm("plan", "fallback") == "qwen36-35b"
    assert routing.resolve_openhands("execute", "fallback") == "qwen36-35b"
    assert routing.resolve_openhands_transport("publish", "fallback") == "openai/qwen36-35b"


def test_transport_prefixed_qwen_models_are_normalized_to_canonical(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """direct_llm:
  classify: openai/qwen36-27b
openhands:
  execute: openai/qwen36-35b
verification_checks:
  unit_tests: openai/qwen36-27b
"""
    )
    routing = load_model_routing_config(str(cfg))
    assert routing is not None
    assert routing.resolve_direct_llm("classify", None) == "qwen36-27b"
    assert routing.resolve_openhands("execute", None) == "qwen36-35b"
    assert routing.resolve_verification_check("run unit tests", None) == "qwen36-27b"


def test_stages_mapping_overrides_nested_models(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """models:
  direct_llm:
    classify: qwen36-27b
  openhands:
    execute: qwen36-27b
stages:
  classify:
    backend: direct_llm
    model: qwen36-35b
  execute:
    backend: openhands
    model: qwen36-35b
"""
    )
    routing = load_model_routing_config(str(cfg))
    assert routing is not None
    assert routing.resolve_direct_llm("classify", "fallback") == "qwen36-35b"
    assert routing.resolve_openhands("execute", "fallback") == "qwen36-35b"


def test_legacy_roles_config_is_rejected(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """roles:
  scout: qwen36-27b
  architect: qwen36-35b
"""
    )
    with pytest.raises(ModelRoutingConfigError):
        load_model_routing_config(str(cfg))


def test_verification_check_models_are_normalized_and_resolved(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """verification_checks:
  unit_tests: qwen36-27b
  pr_checks: qwen36-35b
  default: qwen36-27b
"""
    )
    routing = load_model_routing_config(str(cfg))
    assert routing is not None
    assert routing.resolve_verification_check("run unit tests", "fallback") == "qwen36-27b"
    assert routing.resolve_verification_check("wait for GitHub Actions PR checks", "fallback") == "qwen36-35b"
    assert routing.resolve_verification_check("custom acceptance check", "fallback") == "qwen36-27b"


def test_verification_check_routing_falls_back_to_default_qwen35() -> None:
    routing = ModelRoutingConfig.defaults()
    assert routing.resolve_verification_check("run integration tests", None) == DEFAULT_CANONICAL_MODEL


def test_unknown_stage_and_component_fall_back_to_qwen35_without_config() -> None:
    routing = ModelRoutingConfig.defaults()
    assert routing.resolve_direct_llm("future_direct_stage", None) == DEFAULT_CANONICAL_MODEL
    assert routing.resolve_openhands("future_openhands_stage", None) == DEFAULT_CANONICAL_MODEL
    assert routing.resolve_openhands_transport("future_openhands_stage", None) == "openai/qwen36-35b"
    assert routing.resolve_verification_check("future custom check", None) == DEFAULT_CANONICAL_MODEL


def test_transport_resolution_adds_openai_prefix_only_for_openhands() -> None:
    assert normalize_canonical_model_name("qwen36-27b") == "qwen36-27b"
    assert resolve_openhands_transport_model("qwen36-27b") == "openai/qwen36-27b"
    assert resolve_openhands_transport_model("openai/qwen36-27b") == "openai/qwen36-27b"

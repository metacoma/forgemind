from __future__ import annotations

from pathlib import Path

import pytest

from artifact_workflow_runtime.model_routing import ModelRoutingConfigError, load_model_routing_config


def test_load_stage_based_models(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """direct_llm:
  classify: openai/qwen36-27b
  route: openai/qwen36-35b
  obligations: openai/qwen36-35b
  plan: openai/qwen36-35b
  verify: openai/qwen36-27b
openhands:
  observe: openai/qwen36-27b
  research: openai/qwen36-27b
  execute: openai/qwen36-35b
  publish: openai/qwen36-35b
"""
    )
    routing = load_model_routing_config(str(cfg))
    assert routing is not None
    assert routing.resolve_direct_llm("classify", "fallback") == "openai/qwen36-27b"
    assert routing.resolve_direct_llm("plan", "fallback") == "openai/qwen36-35b"
    assert routing.resolve_openhands("execute", "fallback") == "openai/qwen36-35b"
    assert routing.resolve_openhands("publish", "fallback") == "openai/qwen36-35b"


def test_stages_mapping_overrides_nested_models(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """models:
  direct_llm:
    classify: openai/from-models
  openhands:
    execute: openai/from-models
stages:
  classify:
    backend: direct_llm
    model: openai/from-stages
  execute:
    backend: openhands
    model: openai/from-stages
"""
    )
    routing = load_model_routing_config(str(cfg))
    assert routing is not None
    assert routing.resolve_direct_llm("classify", "fallback") == "openai/from-stages"
    assert routing.resolve_openhands("execute", "fallback") == "openai/from-stages"


def test_legacy_roles_config_is_rejected(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """roles:
  scout: openai/qwen36-27b
  architect: openai/qwen36-35b
"""
    )
    with pytest.raises(ModelRoutingConfigError):
        load_model_routing_config(str(cfg))


def test_verification_check_models_are_normalized_and_resolved(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """direct_llm:
  verify: openai/default-verifier
verification_checks:
  unit_tests: openai/qwen36-27b
  pr_checks: openai/qwen36-35b
  default: openai/qwen36-27b
"""
    )
    routing = load_model_routing_config(str(cfg))
    assert routing is not None
    assert routing.resolve_verification_check("run unit tests", "fallback") == "openai/qwen36-27b"
    assert routing.resolve_verification_check("wait for GitHub Actions PR checks", "fallback") == "openai/qwen36-35b"
    assert routing.resolve_verification_check("custom acceptance check", "fallback") == "openai/qwen36-27b"


def test_verification_check_routing_falls_back_to_verify_slot(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """direct_llm:
  verify: openai/default-verifier
verification_checks:
  unit_tests: openai/unit-verifier
"""
    )
    routing = load_model_routing_config(str(cfg))
    assert routing is not None
    assert routing.resolve_verification_check("run integration tests", "fallback") == "openai/default-verifier"

import pytest

from artifact_workflow_runtime.contracts import ContractGateway, ContractViolationError
from artifact_workflow_runtime.llm_backend.fake import ScriptedLLMBackend
from artifact_workflow_runtime.models import (
    BackendKind,
    DiscoveredImpactKind,
    LLMRequest,
    ObligationAnalysis,
)


def _request() -> LLMRequest:
    return LLMRequest(
        kind="obligation_analysis",
        prompt="Return obligation analysis.",
        task_id="task_1",
        task_text="add Kotlin client",
        backend=BackendKind.DIRECT_LLM,
    )


def test_domain_model_rejects_schema_drift_without_aliases() -> None:
    with pytest.raises(Exception):
        ObligationAnalysis.model_validate(
            {
                "reasoning_summary": "Feature impacts build and docs.",
                "discovered_impacts": [{"kind": "build", "summary": "Build config must change."}],
            }
        )


def test_contract_gateway_reports_violations_instead_of_normalizing() -> None:
    gateway = ContractGateway()
    spec = gateway.spec_for_model(ObligationAnalysis)
    parsed, result = gateway.validate_payload(
        {
            "reasoning_summary": "Feature impacts build and docs.",
            "discovered_impacts": [{"kind": "build", "summary": "Build config must change."}],
            "work_surface": {"impacts": ["README must be updated"]},
        },
        ObligationAnalysis,
        spec,
    )

    assert parsed is None
    assert not result.ok
    assert {violation.path for violation in result.violations} >= {
        "discovered_impacts.0.kind",
        "work_surface.impacts.0",
    }


@pytest.mark.asyncio
async def test_scripted_llm_uses_contract_repair_payload_instead_of_model_aliases() -> None:
    invalid_payload = {
        "reasoning_summary": "Feature impacts build and docs.",
        "discovered_impacts": [{"kind": "build", "summary": "Build config must change."}],
        "work_surface": {"impacts": ["README must be updated"]},
    }
    repaired_payload = {
        "reasoning_summary": "Feature impacts CI/build and docs.",
        "discovered_impacts": [
            {"kind": "ci_build", "summary": "Build config must change."},
            {"kind": "documentation", "summary": "README must be updated."},
        ],
        "work_surface": {
            "affected_surfaces": ["build", "documentation"],
            "impacts": [
                {"kind": "ci_build", "summary": "Build config must change."},
                {"kind": "documentation", "summary": "README must be updated."},
            ],
            "adjacent_components": [],
            "reasoning": "Repaired to canonical schema.",
        },
    }
    backend = ScriptedLLMBackend({"obligation_analysis": [invalid_payload, repaired_payload]})

    result, parsed = await backend.complete_json(_request(), ObligationAnalysis)

    assert parsed.discovered_impacts[0].kind is DiscoveredImpactKind.CI_BUILD
    assert result.contract_result["repaired"] is True
    assert result.contract_result["repair_attempts"] == 1
    assert result.contract_result["violations"] == []


@pytest.mark.asyncio
async def test_scripted_llm_contract_violation_is_controlled_when_repair_fails() -> None:
    invalid_payload = {
        "reasoning_summary": "Feature impacts build and docs.",
        "discovered_impacts": [{"kind": "build", "summary": "Build config must change."}],
    }
    backend = ScriptedLLMBackend({"obligation_analysis": [invalid_payload]}, max_contract_repair_attempts=1)

    with pytest.raises(ContractViolationError) as exc_info:
        await backend.complete_json(_request(), ObligationAnalysis)

    assert exc_info.value.result.schema_id.endswith("ObligationAnalysis")
    assert exc_info.value.result.violations

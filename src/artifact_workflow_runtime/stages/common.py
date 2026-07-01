from __future__ import annotations

from typing import Any
import json

from artifact_workflow_runtime.evidence import render_structured_evidence_summary
from artifact_workflow_runtime.graph.services import WorkflowServices
from artifact_workflow_runtime.control_plane import RuntimeKernel
from artifact_workflow_runtime.graph.stage_gates import StageReadinessGate
from artifact_workflow_runtime.llm_backend.prompts import (
    build_classification_prompt,
    build_obligation_analysis_prompt,
    build_plan_prompt,
    build_route_prompt,
    build_verification_check_prompt,
    build_verification_prompt,
)
from artifact_workflow_runtime.models import (
    AcceptanceDecision,
    ApprovalRequest,
    BackendKind,
    BlockerKind,
    Capability,
    ContextPacket,
    EvidenceVerification,
    ExecutionPlan,
    ObligationAnalysis,
    ExecutionRequest,
    ExecutionResult,
    LLMRequest,
    ObservationResult,
    PolicyDecision,
    PublishRequest,
    PublishResult,
    RepairRequest,
    RepairResult,
    RoutingDecision,
    Task,
    TaskAcceptanceContract,
    TaskClassification,
    VerificationCheckRequest,
    VerificationCheckResult,
    VerificationMode,
    VerificationRequest,
    VerificationResult,
    WorkPacketKind,
)
from artifact_workflow_runtime.models.state import ControllerDecision, StageTransition, WorkflowState, WorkflowStateSnapshot, WorkflowStatus
from artifact_workflow_runtime.lifecycle import PipelineLoopDecision, PipelineReentryTarget, PipelineLoopTriggerKind
from artifact_workflow_runtime.strategy import (
    active_strategy_prompt_block as _active_strategy_prompt_block,
    merge_strategy_update as _merge_strategy_update,
    record_strategy_checkpoint_async as _record_strategy_checkpoint,
    strategy_metadata as _strategy_metadata,
)
from artifact_workflow_runtime.model_routing import normalize_verification_check_slot
from artifact_workflow_runtime.decomposition import packet_from_state as _packet_from_state, packet_metadata as _packet_metadata, packet_prompt_block as _packet_prompt_block, planner_for as _planner_for, selector_for as _selector_for, status_from_execution_result as _packet_status_from_execution_result, update_packet_status as _update_packet_status
from artifact_workflow_runtime.runtime_events import emit_event
from artifact_workflow_runtime.graph.contracts import (
    append_artifact_id as _append_artifact_id,
    effective_task_intent as _effective_task_intent,
    execution_capabilities as _execution_capabilities,
    merge_plan_with_obligations as _merge_plan_with_obligations,
    normalized_completion_status as _normalized_completion_status,
    publish_capabilities as _publish_capabilities,
    render_steps as _render_steps,
)


async def _emit(services: WorkflowServices, kind: str, stage: str, message: str, **payload: Any) -> None:
    await emit_event(services.event_sink, kind, stage, message, payload)


def _llm_model_for(services: WorkflowServices, slot: str) -> str | None:
    routing = services.model_routing
    default_model = getattr(services.llm_backend, "default_model", None)
    return routing.resolve_direct_llm(slot, default_model) if routing else default_model


def _openhands_model_for(services: WorkflowServices, slot: str) -> str | None:
    routing = services.model_routing
    instance = getattr(services.openhands_adapter, "instance", None)
    default_model = getattr(instance, "default_model", None)
    return routing.resolve_openhands(slot, default_model) if routing else default_model



def _llm_model_for_verification_check(services: WorkflowServices, check_name: object) -> str | None:
    routing = services.model_routing
    default_model = getattr(services.llm_backend, "default_model", None)
    return routing.resolve_verification_check(check_name, default_model) if routing else _llm_model_for(services, "verify")




def _append_transition(state: WorkflowState, stage: str, to_status: str, reason: str, artifact_ids_added: list[str] | None = None) -> list[dict[str, Any]]:
    transition = StageTransition(
        from_status=WorkflowStatus.coerce(state.get("status")),
        to_status=WorkflowStatus.coerce(to_status),
        stage=stage,
        reason=reason,
        artifact_ids_added=artifact_ids_added or [],
    )
    return [*(state.get("transitions") or []), transition.model_dump(mode="json")]


def _append_controller_decision(state: WorkflowState, decision: ControllerDecision) -> list[dict[str, Any]]:
    return [*(state.get("controller_decisions") or []), decision.model_dump(mode="json")]


def _append_packet_history(state: WorkflowState, entry: Any) -> list[dict[str, Any]]:
    return [*(state.get("packet_history") or []), entry.model_dump(mode="json")]


def _append_lifecycle_decision(state: WorkflowState, decision: Any) -> list[dict[str, Any]]:
    return [*(state.get("lifecycle_decisions") or []), decision.model_dump(mode="json")]


def _append_pipeline_loop_decision(state: WorkflowState, decision: PipelineLoopDecision) -> list[dict[str, Any]]:
    return [*(state.get("pipeline_loop_decisions") or []), decision.model_dump(mode="json")]


def _pipeline_loop_decisions(state: WorkflowState) -> list[PipelineLoopDecision]:
    return [PipelineLoopDecision.model_validate(item) for item in (state.get("pipeline_loop_decisions") or [])]


def _reentry_target(decision: PipelineLoopDecision) -> str | None:
    if decision.target_stage == PipelineReentryTarget.CONTINUE:
        return None
    return decision.target_stage.value


def _clear_for_reentry(target: str) -> dict[str, Any]:
    common = {
        "policy_decision": None,
        "approval_request": None,
        "publish_request": None,
        "publish_result": None,
        "publish_review_decision": None,
        "verification_request": None,
        "verification_check_requests": [],
        "verification_check_results": [],
        "acceptance_decision": None,
    }
    if target in {"research", "observe", "build_context", "obligations"}:
        common.update({"obligations": None, "plan": None, "acceptance_contract": None})
    if target == "plan":
        common.update({"plan": None, "acceptance_contract": None})
    if target in {"research", "observe", "build_context"}:
        common.update({"context_packet": None})
    return common


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out



_REPAIRABLE_FAILURE_TERMS = (
    "build failure",
    "build failed",
    "compile failed",
    "compiler",
    "error cs",
    "cs0",
    "test failed",
    "tests failed",
    "unit test",
    "execution failure",
    "namespace",
    "does not exist",
    "dotnet build",
    "dotnet test",
)

_ENVIRONMENT_BLOCKER_KINDS = {
    BlockerKind.MISSING_ENVIRONMENT_DEPENDENCY,
    BlockerKind.MISSING_RUNTIME_PREREQUISITE,
    BlockerKind.INTEGRATION_ENVIRONMENT_UNAVAILABLE,
}


def _execution_repair_failure_summaries(execution: ExecutionResult | None) -> list[str]:
    """Return concrete execution/build/test failures that should route to repair.

    This intentionally ignores deferred publish blockers and pure environment
    blockers. A broken build, compiler error, failed unit check, or
    execution_failure blocker is repairable work and must not be sent through
    pipeline re-entry/finalize before bounded repair attempts are exhausted.
    """

    if execution is None:
        return []
    failures: list[str] = []
    if execution.stage_failure is not None:
        failures.append(execution.stage_failure.summary)
    if not execution.ok:
        failures.append(execution.summary or "Execution result did not complete successfully.")

    status = str(getattr(execution.execution_status, "value", execution.execution_status) or "").lower()
    if status in {"failed", "blocked"}:
        failures.append(execution.summary or f"Execution status is {status}.")

    evidence = execution.structured_evidence
    for item in evidence.tests:
        item_status = str(getattr(item, "status", "") or "").lower()
        if item.passed is False or item_status in {"failed", "error"}:
            name = str(item.name or item.command or "failed execution check")
            excerpt = str(item.output_excerpt or "").strip()
            failures.append(f"{name}: {excerpt}" if excerpt else name)

    for item in evidence.commands_run:
        if item.exit_code is not None and item.exit_code != 0:
            command_text = str(item.command or "command").strip()
            output = str(item.output_excerpt or "").strip()
            combined = f"{command_text} {output}".lower()
            if any(term in combined for term in _REPAIRABLE_FAILURE_TERMS):
                failures.append(f"{command_text}: {output}" if output else command_text)

    for blocker in evidence.blockers:
        summary = str(blocker.summary or "").strip()
        kind = _blocker_kind_value(getattr(blocker, "blocker_kind", BlockerKind.GENERIC))
        if _deferred_publish_summary(summary):
            continue
        if kind in {_blocker_kind_value(item) for item in _ENVIRONMENT_BLOCKER_KINDS}:
            continue
        lowered = summary.lower()
        if kind in {BlockerKind.EXECUTION_FAILURE.value, BlockerKind.TEST_FAILURE.value} or any(term in lowered for term in _REPAIRABLE_FAILURE_TERMS):
            failures.append(summary)
    return _unique(failures)


def _blocker_kind_value(value: object) -> str:
    return str(getattr(value, "value", value) or "").lower()


def _deferred_publish_summary(summary: str) -> bool:
    text = str(summary or "").lower()
    publish_terms = ("commit", "committed", "push", "pushed", "pull request", " pr", "pr ", "create_pr", "open_pull_request", "wait_pr_checks")
    deferral_terms = ("forbidden", "deferred", "publish", "publisher", "not been", "not run yet", "has not", "missing evidence")
    return any(term in text for term in publish_terms) and any(term in text for term in deferral_terms)


def _pipeline_continue_decision(source_stage: str, reason: str) -> PipelineLoopDecision:
    return PipelineLoopDecision(
        source_stage=source_stage,
        target_stage=PipelineReentryTarget.CONTINUE,
        trigger_kind=PipelineLoopTriggerKind.NONE,
        reason=reason,
        automatic=False,
        allowed=True,
    )

def _publish_failed_check_names(publish: PublishResult) -> list[str]:
    names: list[str] = []
    for test in publish.structured_evidence.tests:
        if str(test.status).lower() in {"failed", "error", "blocked"} or test.passed is False:
            names.append(test.name)
    text = publish.evidence_text.lower()
    if not names and any(marker in text for marker in ("pr checks failed", "checks failed", "ci failed", "job failed", "workflow failed")):
        names.append("publish/PR checks failed")
    return _unique(names)


def _publish_blocker_summaries(publish: PublishResult) -> list[str]:
    blockers = [item.summary for item in publish.structured_evidence.blockers]
    return _unique(blockers)



def _aggregate_confidence(values: list[str]) -> str:
    normalized = {str(value or "").strip().lower() for value in values}
    if "low" in normalized:
        return "low"
    if "medium" in normalized:
        return "medium"
    if "high" in normalized:
        return "high"
    return "low"


async def _run_check_routed_verification(
    services: WorkflowServices,
    *,
    task: Task,
    plan: ExecutionPlan,
    execution: ExecutionResult,
    publish: PublishResult | None,
    context_packet: ContextPacket,
    base_request: VerificationRequest,
    artifact_ids: list[str],
) -> tuple[VerificationRequest, VerificationResult, list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    check_requests: list[VerificationCheckRequest] = []
    check_results: list[VerificationCheckResult] = []
    parsed_assessments: list[EvidenceVerification] = []
    result_artifacts = []

    for check_name in plan.verification_checks:
        normalized_check = normalize_verification_check_slot(check_name)
        model_override = _llm_model_for_verification_check(services, check_name)
        prompt = build_verification_check_prompt(task, context_packet, plan, execution, check_name, publish)
        check_request = VerificationCheckRequest(
            parent_request_id=base_request.id,
            task_id=task.id,
            execution_result_id=execution.id,
            execution_family=plan.execution_family,
            check_name=check_name,
            normalized_check=normalized_check,
            prompt=prompt,
            context_packet_id=context_packet.id,
            artifact_ids=list(artifact_ids),
            metadata={
                "mode": "per_check_evidence_only",
                "model_slot": f"verify:{normalized_check}",
                "verification_check": normalized_check,
                "model_override": model_override,
            },
        )
        llm_request = LLMRequest(
            kind="verification_check",
            prompt=check_request.compiled_prompt(),
            task_id=task.id,
            task_text=task.description,
            context_packet_id=context_packet.id,
            input_artifact_ids=list(artifact_ids),
            instructions=["review only provided evidence", "do not infer filesystem/runtime state"],
            metadata={
                **check_request.metadata,
                "verification_check_request_id": check_request.id,
                "check_name": check_name,
            },
        )
        llm_result, parsed = await services.llm_backend.complete_json(llm_request, EvidenceVerification)
        parsed_assessments.append(parsed)

        check_result = VerificationCheckResult(
            request_id=check_request.id,
            check_name=check_name,
            normalized_check=normalized_check,
            passed=parsed.passed,
            summary=parsed.summary,
            evidence_text=llm_result.raw_text,
            missing_evidence=list(parsed.missing_evidence),
            confidence=parsed.confidence,
            model=llm_result.model,
            verifier_backend=llm_result.backend or "direct_llm",
            llm_request_id=llm_request.id,
        )
        assessment_artifact = services.artifact_store.add_json(
            "verification_check_assessment",
            {
                "request": check_request.model_dump(mode="json"),
                "llm_request": llm_request.model_dump(mode="json"),
                "llm_result": llm_result.model_dump(mode="json"),
                "assessment": parsed.model_dump(mode="json"),
                "check_result": check_result.model_dump(mode="json"),
            },
            metadata={
                "task_id": task.id,
                "check_name": check_name,
                "normalized_check": normalized_check,
                "model": llm_result.model,
            },
        )
        raw_artifact = services.artifact_store.add_text(
            "verification_check_llm_raw",
            llm_result.raw_text,
            metadata={
                "request_id": llm_request.id,
                "verification_check_request_id": check_request.id,
                "check_name": check_name,
                "normalized_check": normalized_check,
                "backend": llm_result.backend,
                "model": llm_result.model,
            },
        )
        result_artifacts.extend([assessment_artifact, raw_artifact])
        artifact_ids.extend([assessment_artifact.id, raw_artifact.id])
        check_requests.append(check_request)
        check_results.append(check_result)

    checks_passed = _unique([result.check_name for result in check_results if result.passed])
    checks_failed = _unique([result.check_name for result in check_results if not result.passed])
    missing_evidence = _unique([item for parsed in parsed_assessments for item in parsed.missing_evidence])
    performed_test_levels = _unique([item for parsed in parsed_assessments for item in parsed.performed_test_levels])
    missing_test_levels = _unique([item for parsed in parsed_assessments for item in parsed.missing_test_levels])
    setup_steps_performed = _unique([item for parsed in parsed_assessments for item in parsed.setup_steps_performed])
    missing_setup_steps = _unique([item for parsed in parsed_assessments for item in parsed.missing_setup_steps])
    missing_obligations = _unique([item for parsed in parsed_assessments for item in parsed.missing_obligations])
    pr_checks_passed = _unique([item for parsed in parsed_assessments for item in parsed.pr_checks_passed])
    pr_checks_failed = _unique([item for parsed in parsed_assessments for item in parsed.pr_checks_failed])
    pr_checks_pending = _unique([item for parsed in parsed_assessments for item in parsed.pr_checks_pending])
    passed = bool(check_results) and not checks_failed and not missing_evidence and all(parsed.passed for parsed in parsed_assessments)
    commit_required = any(parsed.commit_required for parsed in parsed_assessments)
    push_required = any(parsed.push_required for parsed in parsed_assessments)
    commit_done = any(parsed.commit_done for parsed in parsed_assessments)
    push_done = any(parsed.push_done for parsed in parsed_assessments)
    completion_status = _normalized_completion_status(
        passed,
        missing_evidence,
        checks_passed,
        checks_failed,
        missing_test_levels,
        missing_setup_steps,
        missing_obligations,
        commit_required,
        push_required,
        commit_done,
        push_done,
        "completed" if passed else "partially_completed",
    )
    aggregate_payload = {
        "mode": "per_check_evidence_only",
        "checks": [result.model_dump(mode="json") for result in check_results],
        "models": {result.check_name: result.model for result in check_results},
        "passed": passed,
        "checks_passed": checks_passed,
        "checks_failed": checks_failed,
        "missing_evidence": missing_evidence,
        "completion_status": completion_status,
    }
    aggregate_artifact = services.artifact_store.add_json("verification_assessment", aggregate_payload, metadata={"task_id": task.id, "mode": "per_check"})
    artifact_ids.append(aggregate_artifact.id)
    result_artifacts.append(aggregate_artifact)

    request = VerificationRequest(
        execution_result_id=execution.id,
        execution_family=plan.execution_family,
        backend=BackendKind.DIRECT_LLM,
        mode=VerificationMode.EVIDENCE_REVIEW,
        prompt="per_check_evidence_verification",
        artifact_ids=artifact_ids,
        checks=list(plan.verification_checks),
        metadata={
            "mode": "per_check_evidence_only",
            "parent_request_id": base_request.id,
            "check_count": len(check_results),
            "check_models": {result.check_name: result.model for result in check_results},
        },
    )
    result = VerificationResult(
        request_id=request.id,
        passed=passed,
        summary=f"Per-check verification completed: {len(checks_passed)} passed, {len(checks_failed)} failed.",
        evidence_text=json.dumps(aggregate_payload, ensure_ascii=False, indent=2),
        artifacts=result_artifacts,
        checks_passed=checks_passed,
        checks_failed=checks_failed,
        missing_evidence=missing_evidence,
        confidence=_aggregate_confidence([parsed.confidence for parsed in parsed_assessments]),
        verifier_backend="direct_llm_per_check",
        performed_test_levels=performed_test_levels,
        missing_test_levels=missing_test_levels,
        setup_steps_performed=setup_steps_performed,
        missing_setup_steps=missing_setup_steps,
        commit_required=commit_required,
        push_required=push_required,
        commit_done=commit_done,
        push_done=push_done,
        pr_detected=any(parsed.pr_detected for parsed in parsed_assessments),
        pr_checks_waited=any(parsed.pr_checks_waited for parsed in parsed_assessments),
        pr_checks_passed=pr_checks_passed,
        pr_checks_failed=pr_checks_failed,
        pr_checks_pending=pr_checks_pending,
        missing_obligations=missing_obligations,
        completion_status=completion_status,
    )
    return request, result, artifact_ids, [req.model_dump(mode="json") for req in check_requests], [res.model_dump(mode="json") for res in check_results]

# Export private helper names as well so stage mixins can use a compact
# ``from .common import *`` while this file is still being physically split.
__all__ = [name for name in globals() if not name.startswith("__")]

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import json

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.context import ContextBuilder
from artifact_workflow_runtime.control_plane import RuntimeKernel
from artifact_workflow_runtime.llm_backend.prompts import (
    build_classification_prompt,
    build_obligation_analysis_prompt,
    build_plan_prompt,
    build_route_prompt,
    build_verification_check_prompt,
    build_verification_prompt,
)
from artifact_workflow_runtime.models import (
    ApprovalRequest,
    BackendKind,
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
    RoutingDecision,
    Task,
    TaskClassification,
    VerificationCheckRequest,
    VerificationCheckResult,
    VerificationMode,
    VerificationRequest,
    VerificationResult,
    WorkPacketKind,
)
from artifact_workflow_runtime.models.state import WorkflowState
from artifact_workflow_runtime.observation import ObservationService
from artifact_workflow_runtime.policy import ApprovalProvider, PolicyEngine
from artifact_workflow_runtime.reports import FinalReportBuilder
from artifact_workflow_runtime.runtime_events import EventSink, emit_event
from artifact_workflow_runtime.model_routing import ModelRoutingConfig, normalize_verification_check_slot
from .contracts import (
    append_artifact_id as _append_artifact_id,
    effective_task_intent as _effective_task_intent,
    execution_capabilities as _execution_capabilities,
    merge_plan_with_obligations as _merge_plan_with_obligations,
    normalized_completion_status as _normalized_completion_status,
    publish_capabilities as _publish_capabilities,
    render_steps as _render_steps,
)

try:
    from langgraph.graph import END, StateGraph  # type: ignore
except Exception:  # pragma: no cover - exercised when langgraph is absent
    from .compat import END, StateGraph


@dataclass
class WorkflowServices:
    llm_backend: Any
    openhands_adapter: Any
    artifact_store: ArtifactStore
    context_builder: ContextBuilder
    observation_service: ObservationService
    policy_engine: PolicyEngine
    approval_provider: ApprovalProvider
    final_report_builder: FinalReportBuilder
    event_sink: EventSink | None = None
    model_routing: ModelRoutingConfig | None = None
    runtime_kernel: RuntimeKernel | None = None


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
            prompt=check_request.prompt,
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

def build_workflow_graph(services: WorkflowServices):
    async def intake_node(state: WorkflowState) -> dict[str, Any]:
        task = Task.model_validate(state["task"])
        await _emit(services, "stage_started", "intake", "Persisting task input", task_id=task.id)
        artifact = services.artifact_store.add_json("task", task.model_dump(mode="json"))
        await _emit(services, "stage_completed", "intake", "Task stored as artifact", artifact_id=artifact.id)
        return {
            "task_artifact": artifact.model_dump(mode="json"),
            "artifact_ids": _append_artifact_id(state.get("artifact_ids"), artifact.id),
            "status": "intake_completed",
        }

    async def classify_node(state: WorkflowState) -> dict[str, Any]:
        task = Task.model_validate(state["task"])
        await _emit(services, "stage_started", "classify", "Sending task to Direct LLM for triage", task_id=task.id)
        request = LLMRequest(
            kind="classification",
            prompt=build_classification_prompt(task),
            task_id=task.id,
            task_text=task.description,
            instructions=["classify task intent", "declare whether world facts are needed"],
            metadata={"model_slot": "classify", "model_override": _llm_model_for(services, "classify")},
        )
        result, parsed = await services.llm_backend.complete_json(request, TaskClassification)
        artifact = services.artifact_store.add_json("classification", parsed.model_dump(mode="json"))
        await _emit(
            services,
            "stage_completed",
            "classify",
            "Classification completed",
            execution_family=parsed.execution_family.value,
            needs_world_facts=parsed.needs_world_facts,
            task_intent=parsed.task_intent,
            artifact_id=artifact.id,
        )
        return {
            "classification_request": request.model_dump(mode="json"),
            "classification_result": result.model_dump(mode="json"),
            "classification": parsed.model_dump(mode="json"),
            "artifact_ids": _append_artifact_id(state.get("artifact_ids"), artifact.id),
            "status": "classified",
        }

    async def route_node(state: WorkflowState) -> dict[str, Any]:
        task = Task.model_validate(state["task"])
        classification = TaskClassification.model_validate(state["classification"])
        await _emit(services, "stage_started", "route", "Analyzing evidence requirements before planning", task_id=task.id)
        request = LLMRequest(
            kind="route_analysis",
            prompt=build_route_prompt(task, classification),
            task_id=task.id,
            task_text=task.description,
            instructions=["decide required evidence", "do not plan implementation"],
            metadata={"model_slot": "route", "model_override": _llm_model_for(services, "route")},
        )
        result, parsed = await services.llm_backend.complete_json(request, RoutingDecision)
        artifact = services.artifact_store.add_json("route_decision", parsed.model_dump(mode="json"))
        await _emit(
            services,
            "stage_completed",
            "route",
            "Route decision completed",
            needs_repository_observation=parsed.needs_repository_observation,
            needs_world_observation=parsed.needs_world_observation,
            needs_fresh_external_research=parsed.needs_fresh_external_research,
            can_plan_immediately=parsed.can_plan_immediately,
            artifact_id=artifact.id,
        )
        return {
            "route_request": request.model_dump(mode="json"),
            "route_result": result.model_dump(mode="json"),
            "route_decision": parsed.model_dump(mode="json"),
            "artifact_ids": _append_artifact_id(state.get("artifact_ids"), artifact.id),
            "status": "routed",
        }

    def route_next(state: WorkflowState) -> str:
        decision = RoutingDecision.model_validate(state["route_decision"])
        kernel = services.runtime_kernel or RuntimeKernel()
        return kernel.next_after_route(decision)

    async def research_node(state: WorkflowState) -> dict[str, Any]:
        task = Task.model_validate(state["task"])
        classification = TaskClassification.model_validate(state["classification"])
        route = RoutingDecision.model_validate(state["route_decision"])
        await _emit(services, "stage_started", "research", "Collecting fresh external research evidence", task_id=task.id)
        request = services.observation_service.build_research_request(task, classification, route)
        result = await services.openhands_adapter.observe(request)
        artifact_ids = list(state.get("artifact_ids") or [])
        artifact_ids.extend(artifact.id for artifact in result.artifacts)
        await _emit(
            services,
            "stage_completed",
            "research",
            "Research observation completed",
            ok=result.ok,
            conversation_id=result.conversation_id,
            evidence_kind=result.evidence_kind,
            artifact_ids=[artifact.id for artifact in result.artifacts],
        )
        return {
            "research_request": request.model_dump(mode="json"),
            "research_result": result.model_dump(mode="json"),
            "artifact_ids": artifact_ids,
            "status": "researched",
        }

    def research_next(state: WorkflowState) -> str:
        decision = RoutingDecision.model_validate(state["route_decision"])
        kernel = services.runtime_kernel or RuntimeKernel()
        return kernel.next_after_research(decision)

    async def observe_node(state: WorkflowState) -> dict[str, Any]:
        task = Task.model_validate(state["task"])
        classification = TaskClassification.model_validate(state["classification"])
        route = RoutingDecision.model_validate(state["route_decision"])
        await _emit(services, "stage_started", "observe", "Collecting world facts through OpenHands", task_id=task.id)
        request = services.observation_service.build_request(task, classification, route)
        result = await services.openhands_adapter.observe(request)
        artifact_ids = list(state.get("artifact_ids") or [])
        artifact_ids.extend(artifact.id for artifact in result.artifacts)
        await _emit(
            services,
            "stage_completed",
            "observe",
            "Observation completed",
            ok=result.ok,
            conversation_id=result.conversation_id,
            evidence_kind=result.evidence_kind,
            artifact_ids=[artifact.id for artifact in result.artifacts],
        )
        return {
            "observation_request": request.model_dump(mode="json"),
            "observation_result": result.model_dump(mode="json"),
            "artifact_ids": artifact_ids,
            "status": "observed",
        }

    async def build_context_node(state: WorkflowState) -> dict[str, Any]:
        task = Task.model_validate(state["task"])
        await _emit(services, "stage_started", "build_context", "Building context packet from artifacts", task_id=task.id)
        artifacts = []
        artifact_texts: dict[str, str] = {}
        if state.get("task_artifact"):
            task_artifact = services.artifact_store.get(state["task_artifact"]["id"])
            artifacts.append(task_artifact)
            artifact_texts[task_artifact.id] = services.artifact_store.read_text(task_artifact.id)
        for result_key in ("research_result", "observation_result"):
            if state.get(result_key):
                for art in state[result_key].get("artifacts", []):
                    artifact = services.artifact_store.get(art["id"])
                    artifacts.append(artifact)
                    artifact_texts[artifact.id] = services.artifact_store.read_text(artifact.id)
        context_packet = services.context_builder.build(task, artifacts, artifact_texts=artifact_texts)
        artifact = services.artifact_store.add_text("context_packet", context_packet.text, metadata={"task_id": task.id})
        await _emit(
            services,
            "stage_completed",
            "build_context",
            "Context packet built",
            artifact_count=len(context_packet.artifact_ids),
            section_count=len(context_packet.sections),
            artifact_id=artifact.id,
        )
        return {
            "context_packet": context_packet.model_dump(mode="json"),
            "artifact_ids": _append_artifact_id(state.get("artifact_ids"), artifact.id),
            "status": "context_built",
        }

    async def obligation_analysis_node(state: WorkflowState) -> dict[str, Any]:
        task = Task.model_validate(state["task"])
        classification = TaskClassification.model_validate(state["classification"])
        route = RoutingDecision.model_validate(state["route_decision"])
        context_packet_raw = state.get("context_packet")
        if context_packet_raw is None:
            raise RuntimeError("context_packet missing")
        context_packet = ContextPacket.model_validate(context_packet_raw)
        await _emit(services, "stage_started", "obligations", "Synthesizing obligations from evidence before planning", task_id=task.id)
        request = LLMRequest(
            kind="obligation_analysis",
            prompt=build_obligation_analysis_prompt(task, classification, route, context_packet),
            task_id=task.id,
            task_text=task.description,
            context_packet_id=context_packet.id,
            input_artifact_ids=list(context_packet.artifact_ids),
            instructions=["derive obligations only from the context packet", "return structured completion requirements"],
            metadata={"model_slot": "obligations", "model_override": _llm_model_for(services, "obligations")},
        )
        result, parsed = await services.llm_backend.complete_json(request, ObligationAnalysis)
        artifact = services.artifact_store.add_json("obligation_analysis", parsed.model_dump(mode="json"))
        await _emit(
            services,
            "stage_completed",
            "obligations",
            "Obligations synthesized from evidence",
            required_test_levels=list(parsed.required_test_levels),
            required_setup_steps=list(parsed.required_setup_steps),
            required_publish_actions=list(parsed.required_publish_actions),
            artifact_id=artifact.id,
        )
        return {
            "obligation_request": request.model_dump(mode="json"),
            "obligation_result": result.model_dump(mode="json"),
            "obligations": parsed.model_dump(mode="json"),
            "artifact_ids": _append_artifact_id(state.get("artifact_ids"), artifact.id),
            "status": "obligations_synthesized",
        }

    async def plan_node(state: WorkflowState) -> dict[str, Any]:
        task = Task.model_validate(state["task"])
        classification = TaskClassification.model_validate(state["classification"])
        await _emit(services, "stage_started", "plan", "Generating execution plan from task and evidence", task_id=task.id)
        context_packet_raw = state.get("context_packet")
        if context_packet_raw is None:
            raise RuntimeError("context_packet missing")
        context_packet = ContextPacket.model_validate(context_packet_raw)
        obligations_raw = state.get("obligations")
        if obligations_raw is None:
            raise RuntimeError("obligations missing")
        obligations = ObligationAnalysis.model_validate(obligations_raw)
        request = LLMRequest(
            kind="planning",
            prompt=build_plan_prompt(task, context_packet, _effective_task_intent(classification), obligations),
            task_id=task.id,
            task_text=task.description,
            context_packet_id=context_packet.id,
            input_artifact_ids=list(context_packet.artifact_ids),
            instructions=["plan from typed obligations and context packet", "do not assume unobserved world facts"],
            metadata={"model_slot": "plan", "model_override": _llm_model_for(services, "plan")},
        )
        result, parsed = await services.llm_backend.complete_json(request, ExecutionPlan)
        parsed = _merge_plan_with_obligations(parsed, obligations)
        artifact = services.artifact_store.add_json("execution_plan", parsed.model_dump(mode="json"))
        await _emit(
            services,
            "stage_completed",
            "plan",
            "Execution plan generated",
            execution_family=parsed.execution_family.value,
            task_intent=parsed.task_intent,
            deliverable_kind=parsed.deliverable_kind,
            requires_mutation=parsed.requires_mutation,
            artifact_id=artifact.id,
        )
        return {
            "plan_request": request.model_dump(mode="json"),
            "plan_result": result.model_dump(mode="json"),
            "plan": parsed.model_dump(mode="json"),
            "artifact_ids": _append_artifact_id(state.get("artifact_ids"), artifact.id),
            "status": "planned",
        }

    async def policy_node(state: WorkflowState) -> dict[str, Any]:
        classification = TaskClassification.model_validate(state["classification"])
        route = RoutingDecision.model_validate(state["route_decision"])
        plan = ExecutionPlan.model_validate(state["plan"])
        task = Task.model_validate(state["task"])
        await _emit(services, "stage_started", "policy", "Checking policy and evidence gates", task_id=task.id)
        observation = ObservationResult.model_validate(state["observation_result"]) if state.get("observation_result") else None
        research = ObservationResult.model_validate(state["research_result"]) if state.get("research_result") else None
        kernel = services.runtime_kernel or RuntimeKernel()
        decision = kernel.evaluate_policy(
            classification=classification,
            route=route,
            plan=plan,
            policy_engine=services.policy_engine,
            research=research,
            observation=observation,
        )
        artifact = services.artifact_store.add_json("policy_decision", decision.model_dump(mode="json"))
        await _emit(
            services,
            "stage_completed",
            "policy",
            "Policy decision recorded",
            allowed=decision.allowed,
            blocked=decision.blocked,
            requires_approval=decision.requires_approval,
            reasons=list(decision.reasons),
            artifact_id=artifact.id,
        )
        return {
            "policy_decision": decision.model_dump(mode="json"),
            "artifact_ids": _append_artifact_id(state.get("artifact_ids"), artifact.id),
            "status": "policy_checked",
        }

    def policy_next(state: WorkflowState) -> str:
        kernel = services.runtime_kernel or RuntimeKernel()
        return kernel.next_after_policy(state["policy_decision"])

    async def approval_node(state: WorkflowState) -> dict[str, Any]:
        decision = state["policy_decision"]
        await _emit(services, "stage_started", "approval", "Resolving approval requirement", policy_decision_id=decision["id"])
        request = ApprovalRequest(
            policy_decision_id=decision["id"],
            rationale="Policy requires approval for mutating capabilities.",
            required=True,
        )
        reviewed = await services.approval_provider.review(request)
        artifact = services.artifact_store.add_json("approval_decision", reviewed.model_dump(mode="json"))
        await _emit(services, "stage_completed", "approval", "Approval resolved", approved=reviewed.approved, reviewer=reviewed.reviewer, artifact_id=artifact.id)
        return {
            "approval_request": reviewed.model_dump(mode="json"),
            "artifact_ids": _append_artifact_id(state.get("artifact_ids"), artifact.id),
            "status": "approval_resolved",
        }

    def approval_next(state: WorkflowState) -> str:
        kernel = services.runtime_kernel or RuntimeKernel()
        return kernel.next_after_approval(state.get("approval_request"))

    async def execute_node(state: WorkflowState) -> dict[str, Any]:
        task = Task.model_validate(state["task"])
        await _emit(services, "stage_started", "execute", "Executing plan in OpenHands", task_id=task.id)
        plan = ExecutionPlan.model_validate(state["plan"])
        observation_result = ObservationResult.model_validate(state["observation_result"]) if state.get("observation_result") else None
        context_packet = ContextPacket.model_validate(state["context_packet"]) if state.get("context_packet") else None
        observation_text = observation_result.evidence_text if observation_result else "No observation evidence was collected."
        context_text = context_packet.text if context_packet else ""
        prompt = (
            "You are executing an approved controller plan.\n"
            "Use the environment as needed and make the requested changes.\n"
            "Ground your work in the evidence below.\n"
            "The original task intent is primary; do not silently degrade implementation work into analysis-only output.\n\n"
            f"Task: {task.description}\n\n"
            f"ContextPacket:\n{context_text}\n\n"
            f"Observation evidence:\n{observation_text}\n\n"
            f"Plan summary: {plan.summary}\n"
            "Steps:\n"
            + "\n".join(f"- {step}" for step in plan.steps)
            + "\n\nSuccess criteria:\n"
            + "\n".join(f"- {item}" for item in plan.success_criteria)
            + "\n\nThe environment is a Docker container. Install any dependencies required to run the required test levels inside the container.\n"
            + f"Required setup steps: {plan.required_setup_steps}\n"
            + f"Required test levels: {plan.required_test_levels}\n"
            + f"Require commit: {plan.require_commit}\n"
            + f"Require push: {plan.require_push}\n"
            + "\nWhen finished, report concrete evidence: changed files, commands run, outputs, setup/install steps, test/build results, blockers."
        )
        request = ExecutionRequest(
            task_id=task.id,
            execution_family=plan.execution_family,
            capabilities=plan.capabilities,
            prompt=prompt,
            objective="execute approved controller plan",
            plan_steps=list(plan.steps),
            expected_changes=list(plan.expected_repo_changes),
            verification_commands=list(plan.verification_checks),
            scope_constraints=["do not choose next workflow step", "do not expand task scope", "collect structured evidence"],
            plan_summary=plan.summary,
            context_packet_id=context_packet.id if context_packet else None,
            artifact_ids=list(state.get("artifact_ids") or []),
            success_criteria=list(plan.success_criteria),
            expected_outputs=["changed_files", "commands_run", "setup_steps", "test_results", "blockers"],
            metadata={"evidence_required": True, "model_slot": "execute", "model_override": _openhands_model_for(services, "execute")},
        )
        await _emit(services, "execution_request", "execute", "Execution request created", execution_family=request.execution_family.value, capability_count=len(request.capabilities))
        result = await services.openhands_adapter.execute(request)
        artifact_ids = list(state.get("artifact_ids") or [])
        artifact_ids.extend(artifact.id for artifact in result.artifacts)
        await _emit(
            services,
            "stage_completed",
            "execute",
            "Execution finished",
            ok=result.ok,
            conversation_id=result.conversation_id,
            transport_error=result.transport_error,
            evidence_kind=result.evidence_kind,
            artifact_ids=[artifact.id for artifact in result.artifacts],
        )
        return {
            "execution_request": request.model_dump(mode="json"),
            "execution_result": result.model_dump(mode="json"),
            "artifact_ids": artifact_ids,
            "status": "executed",
        }

    def execute_next(state: WorkflowState) -> str:
        plan = ExecutionPlan.model_validate(state["plan"])
        execution = ExecutionResult.model_validate(state["execution_result"])
        kernel = services.runtime_kernel or RuntimeKernel()
        return kernel.next_after_execution(plan, execution)

    async def publish_node(state: WorkflowState) -> dict[str, Any]:
        task = Task.model_validate(state["task"])
        plan = ExecutionPlan.model_validate(state["plan"])
        execution = ExecutionResult.model_validate(state["execution_result"])
        await _emit(services, "stage_started", "publish", "Ensuring commit/push obligations are satisfied", task_id=task.id)
        prompt = (
            "You are performing repository completion steps after implementation.\n"
            "You are running inside a Docker container.\n"
            "Use the existing workspace, credentials, git remote configuration, and GitHub token or CLI authentication if available.\n"
            "Do not re-implement the feature unless fixing PR/CI failures requires a focused follow-up patch.\n"
            "Repository completion is not finished until commit/push obligations are satisfied and, if a PR exists or is created, its checks are fully assessed.\n\n"
            f"Task: {task.description}\n\n"
            f"Require commit: {plan.require_commit}\n"
            f"Require push: {plan.require_push}\n"
            f"Execution summary: {execution.summary}\n\n"
            "Do the following as needed:\n"
            "- inspect git status, current branch, remote tracking branch, and whether a PR already exists for the branch\n"
            "- create a commit if required and changes are not committed\n"
            "- push the branch/changes if required and remote credentials allow it\n"
            "- if a PR exists already or is created/updated by this push, identify the PR number/URL\n"
            "- wait for all PR checks and GitHub Actions/jobs for the current PR head SHA to finish\n"
            "- if checks fail, inspect the failing jobs/logs, identify the root cause, apply the smallest necessary fix, install any missing build/test/integration dependencies inside Docker, rerun relevant local checks, commit, push, and wait for PR checks again\n"
            "- perform at most 2 CI-fix iterations in this publish step\n"
            "- report exact commands, commit hashes, branch names, PR number/URL, check names and statuses, whether checks were waited to completion, fix iterations performed, and any remaining blockers\n"
            "- if no PR exists and none is needed, state that explicitly\n"
        )
        request = PublishRequest(
            execution_result_id=execution.id,
            task_id=task.id,
            prompt=prompt,
            require_commit=plan.require_commit,
            require_push=plan.require_push,
            artifact_ids=list(state.get("artifact_ids") or []),
            metadata={"mode": "repo_completion", "execution_environment": plan.execution_environment},
        )
        run = await services.openhands_adapter.execute(
            ExecutionRequest(
                task_id=task.id,
                execution_family=plan.execution_family,
                work_packet_kind=WorkPacketKind.PUBLISH,
                capabilities=_publish_capabilities(plan),
                prompt=prompt,
                objective="complete commit/push/PR obligations only",
                plan_steps=list(plan.publication_steps),
                expected_changes=[],
                verification_commands=list(plan.verification_checks),
                scope_constraints=["do not choose next workflow step", "do not reimplement except minimal CI repair", "do not expand task scope"],
                plan_summary="publish obligations",
                context_packet_id=state.get("context_packet", {}).get("id") if isinstance(state.get("context_packet"), dict) else None,
                artifact_ids=list(state.get("artifact_ids") or []),
                expected_outputs=["git_status", "commit_hashes", "push_result", "pr_url", "check_statuses", "blockers"],
                metadata={"mode": "publish", "require_commit": plan.require_commit, "require_push": plan.require_push, "model_slot": "publish", "model_override": _openhands_model_for(services, "publish")},
            )
        )
        artifact_ids = list(state.get("artifact_ids") or [])
        artifact_ids.extend(artifact.id for artifact in run.artifacts)
        result = PublishResult(
            request_id=request.id,
            ok=run.ok,
            summary=run.summary,
            evidence_text=run.evidence_text,
            artifacts=run.artifacts,
            conversation_id=run.conversation_id,
            transport_error=run.transport_error,
            evidence_kind=run.evidence_kind,
        )
        await _emit(
            services,
            "stage_completed",
            "publish",
            "Publish obligations attempted",
            ok=result.ok,
            conversation_id=result.conversation_id,
            require_commit=plan.require_commit,
            require_push=plan.require_push,
            artifact_ids=[artifact.id for artifact in result.artifacts],
        )
        return {
            "publish_request": request.model_dump(mode="json"),
            "publish_result": result.model_dump(mode="json"),
            "artifact_ids": artifact_ids,
            "status": "published",
        }

    async def verify_node(state: WorkflowState) -> dict[str, Any]:
        task = Task.model_validate(state["task"])
        await _emit(services, "stage_started", "verify", "Verifying execution evidence", task_id=task.id)
        plan = ExecutionPlan.model_validate(state["plan"])
        execution = ExecutionResult.model_validate(state["execution_result"])
        publish = PublishResult.model_validate(state["publish_result"]) if state.get("publish_result") else None
        artifact_ids = list(state.get("artifact_ids") or [])
        request = VerificationRequest(
            execution_result_id=execution.id,
            execution_family=plan.execution_family,
            backend=BackendKind.DIRECT_LLM,
            mode=VerificationMode.EVIDENCE_REVIEW,
            prompt="evidence_verification",
            artifact_ids=artifact_ids,
            checks=list(plan.verification_checks),
            metadata={"mode": "evidence_only"},
        )
        check_requests: list[dict[str, Any]] = []
        check_results: list[dict[str, Any]] = []
        if not execution.ok:
            parsed = EvidenceVerification(
                passed=False,
                summary="Execution did not produce usable evidence, so verification failed.",
                checks_passed=[],
                checks_failed=list(plan.verification_checks),
                missing_evidence=["usable execution evidence"],
                confidence="high" if execution.transport_error else "medium",
                reasoning="Verification is blocked because execution evidence was empty or transport-corrupted.",
                pr_detected=False,
                pr_checks_waited=False,
                pr_checks_passed=[],
                pr_checks_failed=[],
                pr_checks_pending=[],
                missing_obligations=["usable execution evidence"],
                completion_status="blocked",
            )
            raw_text = parsed.model_dump_json(indent=2)
            verification_artifact = services.artifact_store.add_json("verification_assessment", parsed.model_dump(mode="json"))
            artifact_ids.append(verification_artifact.id)
            completion_status = _normalized_completion_status(
                parsed.passed,
                parsed.missing_evidence,
                parsed.checks_passed,
                parsed.checks_failed,
                parsed.missing_test_levels,
                parsed.missing_setup_steps,
                parsed.missing_obligations,
                parsed.commit_required,
                parsed.push_required,
                parsed.commit_done,
                parsed.push_done,
                parsed.completion_status,
            )
            result = VerificationResult(
                request_id=request.id,
                passed=parsed.passed,
                summary=parsed.summary,
                evidence_text=raw_text,
                artifacts=[verification_artifact],
                checks_passed=parsed.checks_passed,
                checks_failed=parsed.checks_failed,
                missing_evidence=parsed.missing_evidence,
                confidence=parsed.confidence,
                verifier_backend="evidence_guard",
                performed_test_levels=parsed.performed_test_levels,
                missing_test_levels=parsed.missing_test_levels,
                setup_steps_performed=parsed.setup_steps_performed,
                missing_setup_steps=parsed.missing_setup_steps,
                commit_required=parsed.commit_required,
                push_required=parsed.push_required,
                commit_done=parsed.commit_done,
                push_done=parsed.push_done,
                pr_detected=parsed.pr_detected,
                pr_checks_waited=parsed.pr_checks_waited,
                pr_checks_passed=parsed.pr_checks_passed,
                pr_checks_failed=parsed.pr_checks_failed,
                pr_checks_pending=parsed.pr_checks_pending,
                missing_obligations=parsed.missing_obligations,
                completion_status=completion_status,
            )
        else:
            context_packet_raw = state.get("context_packet")
            if context_packet_raw is None:
                raise RuntimeError("context_packet missing")
            context_packet = ContextPacket.model_validate(context_packet_raw)
            kernel = services.runtime_kernel or RuntimeKernel()
            strategy = kernel.verification_strategy(
                plan=plan,
                execution=execution,
                publish=publish,
                per_check_routing_enabled=bool(services.model_routing and services.model_routing.verification_checks),
            )
            if strategy.requires_world_check:
                prompt = (
                    "You are performing a bounded world verification packet for the controller.\n"
                    "Do not choose the next workflow step. Do not expand task scope. Do not publish.\n"
                    "Run only the checks requested by the controller and report commands, outputs, statuses, blockers, and missing evidence.\n\n"
                    f"Task: {task.description}\n\n"
                    f"ContextPacket:\n{context_packet.text}\n\n"
                    f"Execution summary: {execution.summary}\n"
                    f"Checks: {plan.verification_checks}\n"
                )
                request = VerificationRequest(
                    execution_result_id=execution.id,
                    execution_family=plan.execution_family,
                    backend=BackendKind.OPENHANDS,
                    mode=VerificationMode.WORLD_CHECK,
                    prompt=prompt,
                    artifact_ids=artifact_ids,
                    checks=list(plan.verification_checks),
                    allowed_inputs=["filesystem", "shell", "git", "test_runtime", "context_packet_text"],
                    forbidden_inputs=["change_workflow_decision", "expand_task_scope", "publish", "mutate_without_explicit_check_need"],
                    expected_outputs=["commands_run", "check_statuses", "outputs", "blockers", "missing_evidence"],
                    metadata={"mode": "world_check", "controller_reason": strategy.reason, "model_slot": "verify", "model_override": _openhands_model_for(services, "verify")},
                )
                result = await services.openhands_adapter.verify(request)
                artifact_ids.extend(artifact.id for artifact in result.artifacts)
            elif strategy.per_check:
                request, result, artifact_ids, check_requests, check_results = await _run_check_routed_verification(
                    services,
                    task=task,
                    plan=plan,
                    execution=execution,
                    publish=publish,
                    context_packet=context_packet,
                    base_request=request,
                    artifact_ids=artifact_ids,
                )
            else:
                llm_request = LLMRequest(
                    kind="verification",
                    prompt=build_verification_prompt(task, context_packet, plan, execution, publish),
                    task_id=task.id,
                    task_text=task.description,
                    context_packet_id=context_packet.id,
                    input_artifact_ids=list(artifact_ids),
                    instructions=["review structured artifacts and evidence text only", "separate missing evidence from failed checks"],
                    metadata={"model_slot": "verify", "model_override": _llm_model_for(services, "verify")},
                )
                llm_result, parsed = await services.llm_backend.complete_json(llm_request, EvidenceVerification)
                verification_artifact = services.artifact_store.add_json("verification_assessment", parsed.model_dump(mode="json"))
                llm_artifact = services.artifact_store.add_text(
                    "verification_llm_raw",
                    llm_result.raw_text,
                    metadata={"request_id": llm_request.id, "backend": llm_result.backend, "model": llm_result.model},
                )
                artifact_ids.extend([verification_artifact.id, llm_artifact.id])
                completion_status = _normalized_completion_status(
                    parsed.passed,
                    parsed.missing_evidence,
                    parsed.checks_passed,
                    parsed.checks_failed,
                    parsed.missing_test_levels,
                    parsed.missing_setup_steps,
                    parsed.missing_obligations,
                    parsed.commit_required,
                    parsed.push_required,
                    parsed.commit_done,
                    parsed.push_done,
                    parsed.completion_status,
                )
                request = VerificationRequest(
                    execution_result_id=execution.id,
                    execution_family=plan.execution_family,
                    backend=BackendKind.DIRECT_LLM,
                    mode=VerificationMode.EVIDENCE_REVIEW,
                    prompt=llm_request.prompt,
                    artifact_ids=artifact_ids,
                    checks=list(plan.verification_checks),
                    metadata={"mode": "evidence_only", "llm_request_id": llm_request.id},
                )
                result = VerificationResult(
                    request_id=request.id,
                    passed=parsed.passed,
                    summary=parsed.summary,
                    evidence_text=llm_result.raw_text,
                    artifacts=[verification_artifact, llm_artifact],
                    checks_passed=parsed.checks_passed,
                    checks_failed=parsed.checks_failed,
                    missing_evidence=parsed.missing_evidence,
                    confidence=parsed.confidence,
                    verifier_backend=llm_result.backend or "direct_llm",
                    performed_test_levels=parsed.performed_test_levels,
                    missing_test_levels=parsed.missing_test_levels,
                    setup_steps_performed=parsed.setup_steps_performed,
                    missing_setup_steps=parsed.missing_setup_steps,
                    commit_required=parsed.commit_required,
                    push_required=parsed.push_required,
                    commit_done=parsed.commit_done,
                    push_done=parsed.push_done,
                    pr_detected=parsed.pr_detected,
                    pr_checks_waited=parsed.pr_checks_waited,
                    pr_checks_passed=parsed.pr_checks_passed,
                    pr_checks_failed=parsed.pr_checks_failed,
                    pr_checks_pending=parsed.pr_checks_pending,
                    missing_obligations=parsed.missing_obligations,
                    completion_status=completion_status,
                )
        await _emit(
            services,
            "stage_completed",
            "verify",
            "Verification completed",
            passed=result.passed,
            confidence=result.confidence,
            checks_passed=len(result.checks_passed),
            checks_failed=len(result.checks_failed),
            missing_evidence=list(result.missing_evidence),
            missing_test_levels=list(result.missing_test_levels),
            missing_obligations=list(result.missing_obligations),
            pr_detected=result.pr_detected,
            pr_checks_waited=result.pr_checks_waited,
            pr_checks_failed=list(result.pr_checks_failed),
            pr_checks_pending=list(result.pr_checks_pending),
            completion_status=result.completion_status,
        )
        return {
            "verification_request": request.model_dump(mode="json"),
            "verification_check_requests": check_requests,
            "verification_check_results": check_results,
            "verification_result": result.model_dump(mode="json"),
            "artifact_ids": artifact_ids,
            "status": "verified",
        }

    async def finalize_node(state: WorkflowState) -> dict[str, Any]:
        task = Task.model_validate(state["task"])
        await _emit(services, "stage_started", "finalize", "Assembling final report", task_id=task.id)
        report = services.final_report_builder.build(
            task=task,
            classification=TaskClassification.model_validate(state["classification"]) if state.get("classification") else None,
            route=RoutingDecision.model_validate(state["route_decision"]) if state.get("route_decision") else None,
            obligations=ObligationAnalysis.model_validate(state["obligations"]) if state.get("obligations") else None,
            plan=ExecutionPlan.model_validate(state["plan"]) if state.get("plan") else None,
            policy=PolicyDecision.model_validate(state["policy_decision"]) if state.get("policy_decision") else None,
            approval=ApprovalRequest.model_validate(state["approval_request"]) if state.get("approval_request") else None,
            research=ObservationResult.model_validate(state["research_result"]) if state.get("research_result") else None,
            observation=ObservationResult.model_validate(state["observation_result"]) if state.get("observation_result") else None,
            execution=ExecutionResult.model_validate(state["execution_result"]) if state.get("execution_result") else None,
            publish=PublishResult.model_validate(state["publish_result"]) if state.get("publish_result") else None,
            verification=VerificationResult.model_validate(state["verification_result"]) if state.get("verification_result") else None,
            artifact_ids=list(state.get("artifact_ids") or []),
        )
        artifact = services.artifact_store.add_json("final_report", report.model_dump(mode="json"))
        await _emit(services, "stage_completed", "finalize", "Final report ready", status=report.status, artifact_id=artifact.id)
        return {
            "final_report": report.model_dump(mode="json"),
            "artifact_ids": _append_artifact_id(state.get("artifact_ids"), artifact.id),
            "status": report.status,
        }

    graph = StateGraph(WorkflowState)
    graph.add_node("intake", intake_node)
    graph.add_node("classify", classify_node)
    graph.add_node("route", route_node)
    graph.add_node("research", research_node)
    graph.add_node("observe", observe_node)
    graph.add_node("build_context", build_context_node)
    graph.add_node("obligations", obligation_analysis_node)
    graph.add_node("plan", plan_node)
    graph.add_node("policy", policy_node)
    graph.add_node("approval", approval_node)
    graph.add_node("execute", execute_node)
    graph.add_node("publish", publish_node)
    graph.add_node("verify", verify_node)
    graph.add_node("finalize", finalize_node)

    graph.set_entry_point("intake")
    graph.add_edge("intake", "classify")
    graph.add_edge("classify", "route")
    graph.add_conditional_edges("route", route_next, {"research": "research", "observe": "observe", "build_context": "build_context"})
    graph.add_conditional_edges("research", research_next, {"observe": "observe", "build_context": "build_context"})
    graph.add_edge("observe", "build_context")
    graph.add_edge("build_context", "obligations")
    graph.add_edge("obligations", "plan")
    graph.add_edge("plan", "policy")
    graph.add_conditional_edges("policy", policy_next, {"approval": "approval", "execute": "execute", "finalize": "finalize"})
    graph.add_conditional_edges("approval", approval_next, {"execute": "execute", "finalize": "finalize"})
    graph.add_conditional_edges("execute", execute_next, {"publish": "publish", "verify": "verify"})
    graph.add_edge("publish", "verify")
    graph.add_edge("verify", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()

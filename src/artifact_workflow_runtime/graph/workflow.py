from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.context import ContextBuilder
from artifact_workflow_runtime.families import family_requires_evidence_gate
from artifact_workflow_runtime.llm_backend.prompts import (
    build_classification_prompt,
    build_obligation_analysis_prompt,
    build_plan_prompt,
    build_route_prompt,
    build_verification_prompt,
)
from artifact_workflow_runtime.models import (
    ApprovalRequest,
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
    VerificationRequest,
    VerificationResult,
)
from artifact_workflow_runtime.models.state import WorkflowState
from artifact_workflow_runtime.observation import ObservationService
from artifact_workflow_runtime.policy import ApprovalProvider, PolicyEngine
from artifact_workflow_runtime.reports import FinalReportBuilder
from artifact_workflow_runtime.runtime_events import EventSink, emit_event
from artifact_workflow_runtime.model_routing import ModelRoutingConfig
from .contracts import (
    append_artifact_id as _append_artifact_id,
    effective_task_intent as _effective_task_intent,
    execution_capabilities as _execution_capabilities,
    merge_plan_with_obligations as _merge_plan_with_obligations,
    normalized_completion_status as _normalized_completion_status,
    plan_intent_mismatch as _plan_intent_mismatch,
    publish_capabilities as _publish_capabilities,
    publish_required as _publish_required,
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


async def _emit(services: WorkflowServices, kind: str, stage: str, message: str, **payload: Any) -> None:
    await emit_event(services.event_sink, kind, stage, message, payload)


def _llm_model_for(services: WorkflowServices, slot: str) -> str | None:
    routing = services.model_routing
    default_model = getattr(services.llm_backend, "default_model", None)
    return routing.resolve_direct_llm(slot, default_model) if routing else default_model


def _openhands_model_for(services: WorkflowServices, slot: str) -> str | None:
    routing = services.model_routing
    default_model = getattr(services.openhands_adapter.instance, "default_model", None)
    return routing.resolve_openhands(slot, default_model) if routing else default_model


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
        request = LLMRequest(kind="classification", prompt=build_classification_prompt(task), task_id=task.id, metadata={"model_slot": "classify", "model_override": _llm_model_for(services, "classify")})
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
        request = LLMRequest(kind="route_analysis", prompt=build_route_prompt(task, classification), task_id=task.id, metadata={"model_slot": "route", "model_override": _llm_model_for(services, "route")})
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
        if decision.needs_fresh_external_research:
            return "research"
        if decision.needs_repository_observation or decision.needs_world_observation:
            return "observe"
        return "build_context"

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
        if decision.needs_repository_observation or decision.needs_world_observation:
            return "observe"
        return "build_context"

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
            context_packet_id=context_packet.id,
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
            context_packet_id=context_packet.id,
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
        reasons: list[str] = []
        blocked = False
        mismatch = _plan_intent_mismatch(classification, plan)
        if mismatch:
            blocked = True
            reasons.append(mismatch)
        observation_raw = state.get("observation_result")
        if family_requires_evidence_gate(plan.execution_family):
            if observation_raw is None:
                blocked = True
                reasons.append("Execution requires observation evidence, but no observation result was captured.")
            else:
                observation = ObservationResult.model_validate(observation_raw)
                if not observation.ok:
                    blocked = True
                    reasons.append("Execution requires usable observation evidence, but observation failed or returned transport garbage.")
        if route.needs_fresh_external_research:
            research_raw = state.get("research_result")
            if research_raw is None:
                blocked = True
                reasons.append("Planning and execution require fresh external research evidence, but none was captured.")
            else:
                research = ObservationResult.model_validate(research_raw)
                if not research.ok:
                    blocked = True
                    reasons.append("Fresh external research was required, but the research observation failed or returned unusable evidence.")
        if blocked:
            decision = PolicyDecision(
                allowed=False,
                blocked=True,
                requires_approval=False,
                reasons=reasons,
                execution_family=plan.execution_family,
                capabilities=list(dict.fromkeys([*classification.capabilities, *plan.capabilities])),
            )
        else:
            decision = services.policy_engine.decide(classification, plan)
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
        decision = state["policy_decision"]
        if decision.get("blocked"):
            return "finalize"
        if decision.get("requires_approval"):
            return "approval"
        return "execute"

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
        approval = state.get("approval_request") or {}
        return "execute" if approval.get("approved") else "finalize"

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
            plan_summary=plan.summary,
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
        if execution.ok and _publish_required(plan):
            return "publish"
        return "verify"

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
            metadata={"mode": "repo_completion", "execution_environment": plan.execution_environment},
        )
        run = await services.openhands_adapter.execute(
            ExecutionRequest(
                task_id=task.id,
                execution_family=plan.execution_family,
                capabilities=_publish_capabilities(plan),
                prompt=prompt,
                plan_summary="publish obligations",
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
            prompt="evidence_verification",
            artifact_ids=artifact_ids,
            checks=list(plan.verification_checks),
            metadata={"mode": "evidence_only"},
        )
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
            llm_request = LLMRequest(
                kind="verification",
                prompt=build_verification_prompt(task, context_packet, plan, execution, publish),
                task_id=task.id,
                context_packet_id=context_packet.id,
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

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.context import ContextBuilder
from artifact_workflow_runtime.families import family_requires_evidence_gate, family_requires_observation, infer_task_intent, task_text_suggests_world_facts
from artifact_workflow_runtime.llm_backend.prompts import build_classification_prompt, build_plan_prompt, build_verification_prompt
from artifact_workflow_runtime.models import (
    ApprovalRequest,
    ContextPacket,
    EvidenceVerification,
    ExecutionPlan,
    ExecutionRequest,
    ExecutionResult,
    LLMRequest,
    ObservationResult,
    PolicyDecision,
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


def _effective_task_intent(task: Task, classification: TaskClassification) -> str:
    inferred = infer_task_intent(task.description)
    classified = (classification.task_intent or "").strip().lower()
    if inferred in {"implement", "modify"} and classified not in {"implement", "modify"}:
        return inferred
    return classified or inferred or "investigate"


def _plan_is_analysis_only(plan: ExecutionPlan) -> bool:
    text = " ".join([plan.summary, *plan.steps, *plan.success_criteria]).lower()
    analysis_markers = ("analyze", "design", "document", "outline", "instructions", "review", "draft plan")
    implementation_markers = ("implement", "add", "modify", "edit", "write code", "create file", "update build", "run test", "compile")
    has_analysis = any(marker in text for marker in analysis_markers)
    has_implementation = any(marker in text for marker in implementation_markers)
    return has_analysis and not has_implementation


def _plan_intent_mismatch(task: Task, classification: TaskClassification, plan: ExecutionPlan) -> str | None:
    expected = _effective_task_intent(task, classification)
    raw_actual = (plan.task_intent or "").strip().lower()
    raw_deliverable = (plan.deliverable_kind or "").strip().lower()
    text = " ".join([plan.summary, *plan.steps, *plan.success_criteria]).lower()
    has_implementation_markers = any(marker in text for marker in ("implement", "add", "modify", "edit", "write code", "create", "update build", "run test", "compile", "fix"))
    actual = raw_actual
    if actual not in {"implement", "modify", "investigate", "document", "verify"}:
        actual = ""
    if actual in {"", "investigate"} and (plan.requires_mutation or plan.must_change_world or has_implementation_markers):
        actual = "implement"
    deliverable = raw_deliverable
    if deliverable in {"", "analysis"} and (plan.requires_mutation or plan.must_change_world or has_implementation_markers):
        deliverable = "repository_changes" if classification.execution_family.value == "repository_change" else "changes"
    if expected in {"implement", "modify"}:
        if actual not in {"implement", "modify"}:
            return f"Planner degraded a {expected} task into {actual or 'unknown'} intent."
        if deliverable in {"analysis", "documentation"}:
            return f"Planner produced {deliverable} deliverable for a {expected} task instead of real changes."
        if not plan.requires_mutation and not plan.must_change_world and not has_implementation_markers:
            return f"Planner marked a {expected} task as non-mutating, which conflicts with the requested outcome."
        if classification.execution_family.value == "repository_change" and not plan.expected_repo_changes and raw_deliverable == "documentation":
            return "Repository implementation plan does not declare expected repository changes."
        if _plan_is_analysis_only(plan):
            return "Planner produced an analysis-only plan for an implementation task."
    return None



def _append_artifact_id(state: WorkflowState, artifact_id: str) -> list[str]:
    return [*(state.get("artifact_ids") or []), artifact_id]



def _extend_artifact_ids(state: WorkflowState, artifact_ids: list[str]) -> list[str]:
    return [*(state.get("artifact_ids") or []), *artifact_ids]


async def _emit(services: WorkflowServices, kind: str, stage: str, message: str, **payload: Any) -> None:
    await emit_event(services.event_sink, kind, stage, message, payload)



def build_workflow_graph(services: WorkflowServices):
    async def intake_node(state: WorkflowState) -> dict[str, Any]:
        task = Task.model_validate(state["task"])
        await _emit(services, "stage_started", "intake", "Persisting task input", task_id=task.id)
        artifact = services.artifact_store.add_json("task", task.model_dump(mode="json"))
        await _emit(services, "stage_completed", "intake", "Task stored as artifact", artifact_id=artifact.id)
        return {
            "task_artifact": artifact.model_dump(mode="json"),
            "artifact_ids": _append_artifact_id(state, artifact.id),
            "status": "intake_completed",
        }

    async def classify_node(state: WorkflowState) -> dict[str, Any]:
        task = Task.model_validate(state["task"])
        await _emit(services, "stage_started", "classify", "Sending task to Direct LLM for triage", task_id=task.id)
        request = LLMRequest(kind="classification", prompt=build_classification_prompt(task), task_id=task.id)
        result, parsed = await services.llm_backend.complete_json(request, TaskClassification)
        artifact = services.artifact_store.add_json("classification", parsed.model_dump(mode="json"))
        await _emit(services, "stage_completed", "classify", "Classification completed", execution_family=parsed.execution_family.value, needs_world_facts=parsed.needs_world_facts, task_intent=parsed.task_intent, artifact_id=artifact.id)
        return {
            "classification_request": request.model_dump(mode="json"),
            "classification_result": result.model_dump(mode="json"),
            "classification": parsed.model_dump(mode="json"),
            "artifact_ids": _append_artifact_id(state, artifact.id),
            "status": "classified",
        }

    def classify_next(state: WorkflowState) -> str:
        task = Task.model_validate(state["task"])
        classification = TaskClassification.model_validate(state["classification"])
        must_observe = classification.needs_world_facts or family_requires_observation(classification.execution_family) or task_text_suggests_world_facts(task.description)
        return "observe" if must_observe else "build_context"

    async def observe_node(state: WorkflowState) -> dict[str, Any]:
        task = Task.model_validate(state["task"])
        await _emit(services, "stage_started", "observe", "Collecting world facts through OpenHands", task_id=task.id)
        classification = TaskClassification.model_validate(state["classification"])
        request = services.observation_service.build_request(task, classification)
        result = await services.openhands_adapter.observe(request)
        artifact_ids = list(state.get("artifact_ids") or [])
        artifact_ids.extend(artifact.id for artifact in result.artifacts)
        await _emit(services, "stage_completed", "observe", "Observation completed", ok=result.ok, conversation_id=result.conversation_id, evidence_kind=result.evidence_kind, artifact_ids=[artifact.id for artifact in result.artifacts])
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
        if state.get("observation_result"):
            for art in state["observation_result"].get("artifacts", []):
                artifact = services.artifact_store.get(art["id"])
                artifacts.append(artifact)
                artifact_texts[artifact.id] = services.artifact_store.read_text(artifact.id)
        context_packet = services.context_builder.build(task, artifacts, artifact_texts=artifact_texts)
        artifact = services.artifact_store.add_text("context_packet", context_packet.text, metadata={"task_id": task.id})
        await _emit(services, "stage_completed", "build_context", "Context packet built", artifact_count=len(context_packet.artifact_ids), section_count=len(context_packet.sections), artifact_id=artifact.id)
        return {
            "context_packet": context_packet.model_dump(mode="json"),
            "artifact_ids": _append_artifact_id(state, artifact.id),
            "status": "context_built",
        }

    async def plan_node(state: WorkflowState) -> dict[str, Any]:
        task = Task.model_validate(state["task"])
        await _emit(services, "stage_started", "plan", "Generating execution plan from task and evidence", task_id=task.id)
        context_packet_raw = state.get("context_packet")
        if context_packet_raw is None:
            raise RuntimeError("context_packet missing")
        context_packet = ContextPacket.model_validate(context_packet_raw)
        request = LLMRequest(
            kind="planning",
            prompt=build_plan_prompt(task, context_packet, _effective_task_intent(task, TaskClassification.model_validate(state["classification"]))),
            task_id=task.id,
            context_packet_id=context_packet.id,
        )
        result, parsed = await services.llm_backend.complete_json(request, ExecutionPlan)
        artifact = services.artifact_store.add_json("execution_plan", parsed.model_dump(mode="json"))
        await _emit(services, "stage_completed", "plan", "Execution plan generated", execution_family=parsed.execution_family.value, task_intent=parsed.task_intent, deliverable_kind=parsed.deliverable_kind, requires_mutation=parsed.requires_mutation, artifact_id=artifact.id)
        return {
            "plan_request": request.model_dump(mode="json"),
            "plan_result": result.model_dump(mode="json"),
            "plan": parsed.model_dump(mode="json"),
            "artifact_ids": _append_artifact_id(state, artifact.id),
            "status": "planned",
        }

    async def policy_node(state: WorkflowState) -> dict[str, Any]:
        task = Task.model_validate(state["task"])
        await _emit(services, "stage_started", "policy", "Checking policy and evidence gates", task_id=task.id)
        classification = TaskClassification.model_validate(state["classification"])
        plan = ExecutionPlan.model_validate(state["plan"])
        reasons: list[str] = []
        blocked = False
        mismatch = _plan_intent_mismatch(task, classification, plan)
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
        await _emit(services, "stage_completed", "policy", "Policy decision recorded", allowed=decision.allowed, blocked=decision.blocked, requires_approval=decision.requires_approval, reasons=list(decision.reasons), artifact_id=artifact.id)
        return {
            "policy_decision": decision.model_dump(mode="json"),
            "artifact_ids": _append_artifact_id(state, artifact.id),
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
            "artifact_ids": _append_artifact_id(state, artifact.id),
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
        observation_text = observation_result.evidence_text if observation_result else "No observation evidence was collected."
        prompt = (
            "You are executing an approved controller plan.\n"
            "Use the environment as needed and make the requested changes.\n"
            "Ground your work in the observation evidence below.\n\n"
            f"Task: {task.description}\n\n"
            f"Observation evidence:\n{observation_text}\n\n"
            f"Plan summary: {plan.summary}\n"
            "Steps:\n"
            + "\n".join(f"- {step}" for step in plan.steps)
            + "\n\nSuccess criteria:\n"
            + "\n".join(f"- {item}" for item in plan.success_criteria)
            + "\n\nWhen finished, report concrete evidence: changed files, commands run, outputs, test/build results, blockers."
        )
        request = ExecutionRequest(
            task_id=task.id,
            execution_family=plan.execution_family,
            capabilities=plan.capabilities,
            prompt=prompt,
            plan_summary=plan.summary,
            metadata={"evidence_required": True},
        )
        await _emit(services, "execution_request", "execute", "Execution request created", execution_family=request.execution_family.value, capability_count=len(request.capabilities))
        result = await services.openhands_adapter.execute(request)
        artifact_ids = list(state.get("artifact_ids") or [])
        artifact_ids.extend(artifact.id for artifact in result.artifacts)
        await _emit(services, "stage_completed", "execute", "Execution finished", ok=result.ok, conversation_id=result.conversation_id, transport_error=result.transport_error, evidence_kind=result.evidence_kind, artifact_ids=[artifact.id for artifact in result.artifacts])
        return {
            "execution_request": request.model_dump(mode="json"),
            "execution_result": result.model_dump(mode="json"),
            "artifact_ids": artifact_ids,
            "status": "executed",
        }

    async def verify_node(state: WorkflowState) -> dict[str, Any]:
        task = Task.model_validate(state["task"])
        await _emit(services, "stage_started", "verify", "Verifying execution evidence", task_id=task.id)
        plan = ExecutionPlan.model_validate(state["plan"])
        execution = ExecutionResult.model_validate(state["execution_result"])
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
            )
            raw_text = parsed.model_dump_json(indent=2)
            verification_artifact = services.artifact_store.add_json("verification_assessment", parsed.model_dump(mode="json"))
            artifact_ids.append(verification_artifact.id)
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
            )
        else:
            context_packet_raw = state.get("context_packet")
            if context_packet_raw is None:
                raise RuntimeError("context_packet missing")
            context_packet = ContextPacket.model_validate(context_packet_raw)
            llm_request = LLMRequest(
                kind="verification",
                prompt=build_verification_prompt(task, context_packet, plan, execution),
                task_id=task.id,
                context_packet_id=context_packet.id,
            )
            llm_result, parsed = await services.llm_backend.complete_json(llm_request, EvidenceVerification)
            verification_artifact = services.artifact_store.add_json("verification_assessment", parsed.model_dump(mode="json"))
            llm_artifact = services.artifact_store.add_text(
                "verification_llm_raw",
                llm_result.raw_text,
                metadata={"request_id": llm_request.id, "backend": llm_result.backend, "model": llm_result.model},
            )
            artifact_ids.extend([verification_artifact.id, llm_artifact.id])
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
            )
        await _emit(services, "stage_completed", "verify", "Verification completed", passed=result.passed, confidence=result.confidence, checks_passed=len(result.checks_passed), checks_failed=len(result.checks_failed), missing_evidence=list(result.missing_evidence))
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
            plan=ExecutionPlan.model_validate(state["plan"]) if state.get("plan") else None,
            policy=PolicyDecision.model_validate(state["policy_decision"]) if state.get("policy_decision") else None,
            approval=ApprovalRequest.model_validate(state["approval_request"]) if state.get("approval_request") else None,
            observation=ObservationResult.model_validate(state["observation_result"]) if state.get("observation_result") else None,
            execution=ExecutionResult.model_validate(state["execution_result"]) if state.get("execution_result") else None,
            verification=VerificationResult.model_validate(state["verification_result"]) if state.get("verification_result") else None,
            artifact_ids=list(state.get("artifact_ids") or []),
        )
        artifact = services.artifact_store.add_json("final_report", report.model_dump(mode="json"))
        await _emit(services, "stage_completed", "finalize", "Final report ready", status=report.status, artifact_id=artifact.id)
        return {
            "final_report": report.model_dump(mode="json"),
            "artifact_ids": _append_artifact_id(state, artifact.id),
            "status": report.status,
        }

    graph = StateGraph(WorkflowState)
    graph.add_node("intake", intake_node)
    graph.add_node("classify", classify_node)
    graph.add_node("observe", observe_node)
    graph.add_node("build_context", build_context_node)
    graph.add_node("plan", plan_node)
    graph.add_node("policy", policy_node)
    graph.add_node("approval", approval_node)
    graph.add_node("execute", execute_node)
    graph.add_node("verify", verify_node)
    graph.add_node("finalize", finalize_node)

    graph.set_entry_point("intake")
    graph.add_edge("intake", "classify")
    graph.add_conditional_edges("classify", classify_next, {"observe": "observe", "build_context": "build_context"})
    graph.add_edge("observe", "build_context")
    graph.add_edge("build_context", "plan")
    graph.add_edge("plan", "policy")
    graph.add_conditional_edges("policy", policy_next, {"approval": "approval", "execute": "execute", "finalize": "finalize"})
    graph.add_conditional_edges("approval", approval_next, {"execute": "execute", "finalize": "finalize"})
    graph.add_edge("execute", "verify")
    graph.add_edge("verify", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()

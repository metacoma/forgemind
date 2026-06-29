from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.context import ContextBuilder
from artifact_workflow_runtime.llm_backend.prompts import build_classification_prompt, build_plan_prompt
from artifact_workflow_runtime.models import (
    ApprovalRequest,
    ContextPacket,
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


def _append_artifact_id(state: WorkflowState, artifact_id: str) -> list[str]:
    return [*(state.get("artifact_ids") or []), artifact_id]


def build_workflow_graph(services: WorkflowServices):
    async def intake_node(state: WorkflowState) -> dict[str, Any]:
        task = Task.model_validate(state["task"])
        artifact = services.artifact_store.add_json("task", task.model_dump(mode="json"))
        return {
            "task_artifact": artifact.model_dump(mode="json"),
            "artifact_ids": _append_artifact_id(state, artifact.id),
            "status": "intake_completed",
        }

    async def classify_node(state: WorkflowState) -> dict[str, Any]:
        task = Task.model_validate(state["task"])
        request = LLMRequest(kind="classification", prompt=build_classification_prompt(task), task_id=task.id)
        result, parsed = await services.llm_backend.complete_json(request, TaskClassification)
        artifact = services.artifact_store.add_json("classification", parsed.model_dump(mode="json"))
        return {
            "classification_request": request.model_dump(mode="json"),
            "classification_result": result.model_dump(mode="json"),
            "classification": parsed.model_dump(mode="json"),
            "artifact_ids": _append_artifact_id(state, artifact.id),
            "status": "classified",
        }

    def classify_next(state: WorkflowState) -> str:
        classification = TaskClassification.model_validate(state["classification"])
        return "observe" if classification.needs_world_facts else "build_context"

    async def observe_node(state: WorkflowState) -> dict[str, Any]:
        task = Task.model_validate(state["task"])
        classification = TaskClassification.model_validate(state["classification"])
        request = services.observation_service.build_request(task, classification)
        result = await services.openhands_adapter.observe(request)
        artifact_ids = list(state.get("artifact_ids") or [])
        artifact_ids.extend(artifact.id for artifact in result.artifacts)
        return {
            "observation_request": request.model_dump(mode="json"),
            "observation_result": result.model_dump(mode="json"),
            "artifact_ids": artifact_ids,
            "status": "observed",
        }

    async def build_context_node(state: WorkflowState) -> dict[str, Any]:
        task = Task.model_validate(state["task"])
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
        return {
            "context_packet": context_packet.model_dump(mode="json"),
            "artifact_ids": _append_artifact_id(state, artifact.id),
            "status": "context_built",
        }

    async def plan_node(state: WorkflowState) -> dict[str, Any]:
        task = Task.model_validate(state["task"])
        context_packet_raw = state.get("context_packet")
        if context_packet_raw is None:
            raise RuntimeError("context_packet missing")
        context_packet = ContextPacket.model_validate(context_packet_raw)
        request = LLMRequest(
            kind="planning",
            prompt=build_plan_prompt(task, context_packet),
            task_id=task.id,
            context_packet_id=context_packet.id,
        )
        result, parsed = await services.llm_backend.complete_json(request, ExecutionPlan)
        artifact = services.artifact_store.add_json("execution_plan", parsed.model_dump(mode="json"))
        return {
            "plan_request": request.model_dump(mode="json"),
            "plan_result": result.model_dump(mode="json"),
            "plan": parsed.model_dump(mode="json"),
            "artifact_ids": _append_artifact_id(state, artifact.id),
            "status": "planned",
        }

    async def policy_node(state: WorkflowState) -> dict[str, Any]:
        classification = TaskClassification.model_validate(state["classification"])
        plan = ExecutionPlan.model_validate(state["plan"])
        decision = services.policy_engine.decide(classification, plan)
        artifact = services.artifact_store.add_json("policy_decision", decision.model_dump(mode="json"))
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
        request = ApprovalRequest(
            policy_decision_id=decision["id"],
            rationale="Policy requires approval for mutating capabilities.",
            required=True,
        )
        reviewed = await services.approval_provider.review(request)
        artifact = services.artifact_store.add_json("approval_decision", reviewed.model_dump(mode="json"))
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
        plan = ExecutionPlan.model_validate(state["plan"])
        prompt = (
            "You are executing an approved controller plan.\n"
            "Use the environment as needed and make the requested changes.\n\n"
            f"Task: {task.description}\n"
            f"Plan summary: {plan.summary}\n"
            "Steps:\n"
            + "\n".join(f"- {step}" for step in plan.steps)
        )
        request = ExecutionRequest(
            task_id=task.id,
            execution_family=plan.execution_family,
            capabilities=plan.capabilities,
            prompt=prompt,
            plan_summary=plan.summary,
        )
        result = await services.openhands_adapter.execute(request)
        artifact_ids = list(state.get("artifact_ids") or [])
        artifact_ids.extend(artifact.id for artifact in result.artifacts)
        return {
            "execution_request": request.model_dump(mode="json"),
            "execution_result": result.model_dump(mode="json"),
            "artifact_ids": artifact_ids,
            "status": "executed",
        }

    async def verify_node(state: WorkflowState) -> dict[str, Any]:
        task = Task.model_validate(state["task"])
        plan = ExecutionPlan.model_validate(state["plan"])
        execution = ExecutionResult.model_validate(state["execution_result"])
        prompt = (
            "Verify the world state after execution.\n"
            f"Task: {task.description}\n"
            f"Plan summary: {plan.summary}\n"
            f"Execution evidence:\n{execution.evidence_text}\n\n"
            "Run only verification and report pass/fail evidence."
        )
        request = VerificationRequest(
            execution_result_id=execution.id,
            execution_family=plan.execution_family,
            prompt=prompt,
        )
        result = await services.openhands_adapter.verify(request)
        artifact_ids = list(state.get("artifact_ids") or [])
        artifact_ids.extend(artifact.id for artifact in result.artifacts)
        return {
            "verification_request": request.model_dump(mode="json"),
            "verification_result": result.model_dump(mode="json"),
            "artifact_ids": artifact_ids,
            "status": "verified",
        }

    async def finalize_node(state: WorkflowState) -> dict[str, Any]:
        task = Task.model_validate(state["task"])
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

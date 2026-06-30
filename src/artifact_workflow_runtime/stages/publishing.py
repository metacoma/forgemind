from __future__ import annotations

from .common import *
from artifact_workflow_runtime.done_contract import DoneContract
from artifact_workflow_runtime.models import AcceptanceObligationKind, AcceptanceObligationStatus


class PublishingStageMixin:
    async def publish_node(self, state: WorkflowState) -> dict[str, Any]:
        services = self.services
        readiness_gate = self.readiness_gate
        readiness_gate.require(state, "publish", "task", "plan", "execution_result", "done_contract", "acceptance_decision")
        task = Task.model_validate(state["task"])
        plan = ExecutionPlan.model_validate(state["plan"])
        execution = ExecutionResult.model_validate(state["execution_result"])
        done_contract = DoneContract.model_validate(state["done_contract"])
        acceptance = AcceptanceDecision.model_validate(state["acceptance_decision"])
        only_publish_missing = bool(acceptance.obligation_results) and all(
            item.get("kind") == AcceptanceObligationKind.PUBLISH_OBLIGATIONS_SATISFIED.value if isinstance(item, dict) else item.kind == AcceptanceObligationKind.PUBLISH_OBLIGATIONS_SATISFIED
            for item in acceptance.obligation_results
            if (item.get("status") if isinstance(item, dict) else item.status.value) != AcceptanceObligationStatus.PASSED.value
        )
        if not acceptance.accepted and not only_publish_missing:
            raise RuntimeError("publish requires an accepted acceptance_decision or only publish obligations pending")
        await _emit(services, "stage_started", "publish", "Running deterministic publish backend", task_id=task.id)
        prompt = (
            "Publish the accepted revision only. do not patch or run a CI repair loop. Do not repair or re-implement source files.\n"
            "Only perform bounded publish actions such as commit/push/PR creation and wait for all PR checks.\n"
            f"Task: {task.description}\n"
            f"Primary goal: {done_contract.primary_goal}\n"
            f"Require commit: {plan.require_commit}\n"
            f"Require push: {plan.require_push}\n"
            f"Execution summary: {execution.summary}\n"
        )
        request = PublishRequest(
            execution_result_id=execution.id,
            task_id=task.id,
            prompt=prompt,
            require_commit=plan.require_commit,
            require_push=plan.require_push,
            artifact_ids=list(state.get("artifact_ids") or []),
            metadata={"mode": "deterministic_publish", "workspace_branch": state.get("workspace_branch")},
        )
        result = await services.publisher_backend.publish(request)
        artifact_ids = list(state.get("artifact_ids") or [])
        artifact_ids.extend(artifact.id for artifact in result.artifacts)
        await _emit(services, "stage_completed", "publish", "Deterministic publish completed", ok=result.ok, artifact_ids=[artifact.id for artifact in result.artifacts])
        return {
            "publish_request": request.model_dump(mode="json"),
            "publish_result": result.model_dump(mode="json"),
            "artifact_ids": artifact_ids,
            "status": "published",
            "transitions": _append_transition(state, "publish", "published", "Deterministic publisher attempted publish obligations.", [artifact.id for artifact in result.artifacts]),
        }

    async def post_publish_verify_node(self, state: WorkflowState) -> dict[str, Any]:
        services = self.services
        readiness_gate = self.readiness_gate
        readiness_gate.require(state, "post_publish_verify", "task", "plan", "publish_result")
        task = Task.model_validate(state["task"])
        plan = ExecutionPlan.model_validate(state["plan"])
        execution = ExecutionResult.model_validate(state["execution_result"]) if state.get("execution_result") else None
        publish = PublishResult.model_validate(state["publish_result"])
        contract = TaskAcceptanceContract.model_validate(state["acceptance_contract"]) if state.get("acceptance_contract") else None
        kernel = services.runtime_kernel or RuntimeKernel()
        await _emit(services, "stage_started", "post_publish_verify", "Reviewing deterministic publish result", task_id=task.id)
        decision = kernel.review_publish(plan=plan, execution=execution, publish=publish, acceptance_contract=contract, repair_attempt_count=len(state.get("repair_results") or []), max_repair_attempts=2)
        summary = decision.reason
        artifact = services.artifact_store.add_json(
            "post_publish_verification",
            {
                "task_id": task.id,
                "ok": publish.ok,
                "summary": summary,
                "graph_next": decision.graph_next,
                "violations": [item.model_dump(mode="json") for item in decision.violations],
                "pr_checks": [item.model_dump(mode="json") for item in publish.structured_evidence.tests],
            },
            metadata={"task_id": task.id},
        )
        await _emit(services, "stage_completed", "post_publish_verify", "Post-publish verification recorded", ok=publish.ok, next_stage=decision.graph_next, artifact_id=artifact.id)
        update = {
            "publish_review_decision": decision.model_dump(mode="json"),
            "artifact_ids": _append_artifact_id(state.get("artifact_ids"), artifact.id),
            "status": "post_publish_verified",
            "lifecycle_decisions": _append_lifecycle_decision(state, decision),
            "transitions": _append_transition(state, "post_publish_verify", "post_publish_verified", summary, [artifact.id]),
            "controller_decisions": _append_controller_decision(state, kernel.controller_decision(stage="post_publish_verify", selected_next_stage=decision.graph_next, reason=summary)),
        }
        if decision.graph_next == "finalize" and decision.allowed and state.get("acceptance_contract") is not None:
            acceptance = kernel.evaluate_acceptance(
                contract=TaskAcceptanceContract.model_validate(state["acceptance_contract"]),
                execution=execution,
                verification=VerificationResult.model_validate(state["verification_result"]) if state.get("verification_result") else None,
                publish=publish,
            )
            acceptance_artifact = services.artifact_store.add_json("acceptance_decision", acceptance.model_dump(mode="json"), metadata={"task_id": task.id, "source": "post_publish_verify"})
            update["acceptance_decision"] = acceptance.model_dump(mode="json")
            update["artifact_ids"] = _append_artifact_id(update["artifact_ids"], acceptance_artifact.id)
        return update

    def post_publish_verify_next(self, state: WorkflowState) -> str:
        decision = state.get("publish_review_decision") or {}
        next_stage = str(decision.get("graph_next") or "finalize") if isinstance(decision, dict) else "finalize"
        return next_stage if next_stage in {"repair", "finalize"} else "finalize"

from __future__ import annotations

from .common import *
from artifact_workflow_runtime.done_contract import DoneContract
from artifact_workflow_runtime.environment import EnvironmentPlan


class ContractPrepStageMixin:
    async def done_contract_node(self, state: WorkflowState) -> dict[str, Any]:
        services = self.services
        readiness_gate = self.readiness_gate
        readiness_gate.require(state, "done_contract", "task", "classification", "obligations")
        task = Task.model_validate(state["task"])
        classification = TaskClassification.model_validate(state["classification"])
        obligations = ObligationAnalysis.model_validate(state["obligations"])
        context_packet = ContextPacket.model_validate(state["context_packet"]) if state.get("context_packet") else None
        await _emit(services, "stage_started", "done_contract", "Compiling done contract from task and obligations", task_id=task.id)
        contract = services.done_contract_compiler.compile(
            task=task,
            classification=classification,
            obligations=obligations,
            context_packet=context_packet,
        )
        artifact = services.artifact_store.add_json("done_contract", contract.model_dump(mode="json"), metadata={"task_id": task.id, "change_class": contract.change_class})
        await _emit(
            services,
            "stage_completed",
            "done_contract",
            "Done contract compiled",
            change_class=contract.change_class,
            deliverables=list(contract.deliverables),
            artifact_id=artifact.id,
        )
        return {
            "done_contract": contract.model_dump(mode="json"),
            "artifact_ids": _append_artifact_id(state.get("artifact_ids"), artifact.id),
            "status": "done_contract_built",
            "transitions": _append_transition(state, "done_contract", "done_contract_built", "Done contract compiled from task, context, and obligations.", [artifact.id]),
        }

    async def workspace_prepare_node(self, state: WorkflowState) -> dict[str, Any]:
        services = self.services
        readiness_gate = self.readiness_gate
        readiness_gate.require(state, "workspace_prepare", "task", "done_contract")
        task = Task.model_validate(state["task"])
        contract = DoneContract.model_validate(state["done_contract"])
        context_packet = ContextPacket.model_validate(state["context_packet"]) if state.get("context_packet") else None
        await _emit(services, "stage_started", "workspace_prepare", "Preparing workspace branch and environment plan", task_id=task.id)
        workspace_branch = f"awrt/{task.id}"
        env_plan = services.environment_discovery.build_plan(
            task=task,
            done_contract=contract,
            context_packet=context_packet,
            workspace_branch=workspace_branch,
        )
        branch_artifact = services.artifact_store.add_json(
            "workspace_allocation",
            {"task_id": task.id, "workspace_branch": workspace_branch, "created": False, "mode": "allocated_only"},
            metadata={"task_id": task.id},
        )
        env_artifact = services.artifact_store.add_json("environment_plan", env_plan.model_dump(mode="json"), metadata={"task_id": task.id})
        await _emit(
            services,
            "stage_completed",
            "workspace_prepare",
            "Workspace metadata and environment plan prepared",
            workspace_branch=workspace_branch,
            environment_items=[item.model_dump(mode="json") for item in env_plan.items],
            artifact_id=branch_artifact.id,
            environment_artifact_id=env_artifact.id,
        )
        return {
            "workspace_branch": workspace_branch,
            "environment_plan": env_plan.model_dump(mode="json"),
            "artifact_ids": [*_append_artifact_id(state.get("artifact_ids"), branch_artifact.id), env_artifact.id],
            "status": "workspace_prepared",
            "transitions": _append_transition(state, "workspace_prepare", "workspace_prepared", "Workspace branch allocated and environment plan synthesized.", [branch_artifact.id, env_artifact.id]),
            "controller_decisions": _append_controller_decision(state, (services.runtime_kernel or RuntimeKernel()).controller_decision(stage="workspace_prepare", selected_next_stage="execute", reason="Workspace prepared; execute may start with a bounded lease.")),
        }

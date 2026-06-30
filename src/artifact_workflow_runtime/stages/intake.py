from __future__ import annotations

from .common import *


class IntakeStageMixin:
    async def intake_node(self, state: WorkflowState) -> dict[str, Any]:
            services = self.services
            readiness_gate = self.readiness_gate
            task = Task.model_validate(state["task"])
            await _emit(services, "stage_started", "intake", "Persisting task input", task_id=task.id)
            artifact = services.artifact_store.add_json("task", task.model_dump(mode="json"))
            await _emit(services, "stage_completed", "intake", "Task stored as artifact", artifact_id=artifact.id)
            return {
                "task_artifact": artifact.model_dump(mode="json"),
                "artifact_ids": _append_artifact_id(state.get("artifact_ids"), artifact.id),
                "status": "intake_completed",
                "transitions": _append_transition(state, "intake", "intake_completed", "Task persisted as artifact", [artifact.id]),
            }

    async def classify_node(self, state: WorkflowState) -> dict[str, Any]:
            services = self.services
            readiness_gate = self.readiness_gate
            readiness_gate.require(state, "classify", "task")
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
                "transitions": _append_transition(state, "classify", "classified", "Task classified by Direct LLM", [artifact.id]),
            }

    async def route_node(self, state: WorkflowState) -> dict[str, Any]:
            services = self.services
            readiness_gate = self.readiness_gate
            readiness_gate.require(state, "route", "task", "classification")
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
                "transitions": _append_transition(state, "route", "routed", "Controller route evidence requirements recorded", [artifact.id]),
                "controller_decisions": _append_controller_decision(state, (services.runtime_kernel or RuntimeKernel()).controller_decision(stage="route", selected_next_stage=(services.runtime_kernel or RuntimeKernel()).next_after_route(parsed), reason=parsed.reasoning)),
            }

    def route_next(self, state: WorkflowState) -> str:
            services = self.services
            readiness_gate = self.readiness_gate
            decision = RoutingDecision.model_validate(state["route_decision"])
            kernel = services.runtime_kernel or RuntimeKernel()
            return kernel.next_after_route(decision)

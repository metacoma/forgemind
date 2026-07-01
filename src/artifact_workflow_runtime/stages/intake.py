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
            freshness_gate = services.freshness_gate
            freshness_decision = freshness_gate.decide(task, classification, parsed) if freshness_gate is not None else None
            freshness_artifact = None
            retrieval_service = services.retrieval_service
            if freshness_decision is not None:
                if retrieval_service is not None:
                    freshness_artifact = retrieval_service.persist_decision(artifact_store=services.artifact_store, task=task, decision=freshness_decision)
                else:
                    freshness_artifact = services.artifact_store.add_json(
                        "freshness_decision",
                        freshness_decision.model_dump(mode="json"),
                        metadata={"task_id": task.id, "freshness_required": freshness_decision.freshness_required},
                    )
                if freshness_decision.freshness_required:
                    evidence_types = list(parsed.required_evidence_types)
                    for evidence_type in freshness_decision.retrieval_artifact_kinds:
                        if evidence_type not in evidence_types:
                            evidence_types.append(evidence_type)
                    research_targets = list(parsed.research_targets)
                    for target in freshness_decision.targets:
                        if target not in research_targets:
                            research_targets.append(target)
                    parsed = parsed.model_copy(update={
                        "needs_fresh_external_research": True,
                        "can_plan_immediately": False,
                        "required_evidence_types": evidence_types,
                        "research_targets": research_targets,
                        "reasoning": parsed.reasoning + "\nControl-plane freshness gate: " + freshness_decision.retrieval_reason,
                    })
            artifact = services.artifact_store.add_json("route_decision", parsed.model_dump(mode="json"))
            added_artifacts = [artifact.id] + ([freshness_artifact.id] if freshness_artifact is not None else [])
            await _emit(
                services,
                "stage_completed",
                "route",
                "Route decision completed",
                needs_repository_observation=parsed.needs_repository_observation,
                needs_world_observation=parsed.needs_world_observation,
                needs_fresh_external_research=parsed.needs_fresh_external_research,
                can_plan_immediately=parsed.can_plan_immediately,
                freshness_required=bool(freshness_decision and freshness_decision.freshness_required),
                retrieval_mode=(freshness_decision.retrieval_mode.value if freshness_decision else "none"),
                artifact_id=artifact.id,
            )
            artifact_ids = list(state.get("artifact_ids") or [])
            for artifact_id in added_artifacts:
                artifact_ids = _append_artifact_id(artifact_ids, artifact_id)
            kernel = services.runtime_kernel or RuntimeKernel()
            next_stage = kernel.next_after_route(parsed)
            reason = freshness_decision.retrieval_reason if freshness_decision and freshness_decision.freshness_required else parsed.reasoning
            return {
                "route_request": request.model_dump(mode="json"),
                "route_result": result.model_dump(mode="json"),
                "route_decision": parsed.model_dump(mode="json"),
                **({"freshness_decision": freshness_decision.model_dump(mode="json")} if freshness_decision is not None else {}),
                "artifact_ids": artifact_ids,
                "status": "routed",
                "transitions": _append_transition(state, "route", "routed", "Controller route evidence requirements recorded", added_artifacts),
                "controller_decisions": _append_controller_decision(state, kernel.controller_decision(stage="route", selected_next_stage=next_stage, reason=reason)),
            }

    def route_next(self, state: WorkflowState) -> str:
            services = self.services
            readiness_gate = self.readiness_gate
            decision = RoutingDecision.model_validate(state["route_decision"])
            kernel = services.runtime_kernel or RuntimeKernel()
            return kernel.next_after_route(decision)

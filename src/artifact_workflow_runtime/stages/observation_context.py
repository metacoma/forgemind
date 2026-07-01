from __future__ import annotations

from .common import *


class ObservationContextStageMixin:
    async def research_node(self, state: WorkflowState) -> dict[str, Any]:
            services = self.services
            readiness_gate = self.readiness_gate
            readiness_gate.require(state, "research", "task", "route_decision")
            task = Task.model_validate(state["task"])
            classification = TaskClassification.model_validate(state["classification"])
            route = RoutingDecision.model_validate(state["route_decision"])
            freshness_decision = None
            if state.get("freshness_decision"):
                from artifact_workflow_runtime.freshness import FreshnessDecision
                freshness_decision = FreshnessDecision.model_validate(state["freshness_decision"])
            await _emit(services, "stage_started", "research", "Collecting fresh external research evidence", task_id=task.id, freshness_required=bool(freshness_decision and freshness_decision.freshness_required))
            retrieval_service = services.retrieval_service
            if freshness_decision is not None and freshness_decision.freshness_required and retrieval_service is not None:
                request = retrieval_service.build_request(task=task, classification=classification, decision=freshness_decision)
            else:
                request = services.observation_service.build_research_request(task, classification, route)
            result = await services.openhands_adapter.observe(request)
            retrieval_snapshot = None
            retrieval_artifacts: list[Any] = []
            if freshness_decision is not None and freshness_decision.freshness_required and retrieval_service is not None:
                retrieval_snapshot, retrieval_artifacts = retrieval_service.normalize_result(
                    artifact_store=services.artifact_store,
                    task=task,
                    decision=freshness_decision,
                    result=result,
                )
                result = result.model_copy(update={
                    "artifacts": [*result.artifacts, *retrieval_artifacts],
                    "primary_evidence_artifact_ids": [*result.primary_evidence_artifact_ids, *[artifact.id for artifact in retrieval_artifacts]],
                })
            artifact_ids = list(state.get("artifact_ids") or [])
            artifact_ids.extend(artifact.id for artifact in result.artifacts)
            artifact_ids = list(dict.fromkeys(artifact_ids))
            await _emit(
                services,
                "stage_completed",
                "research",
                "Research observation completed",
                ok=result.ok,
                conversation_id=result.conversation_id,
                evidence_kind=result.evidence_kind,
                retrieval_mode=(freshness_decision.retrieval_mode.value if freshness_decision else "generic_research"),
                retrieval_artifact_ids=[artifact.id for artifact in retrieval_artifacts],
                artifact_ids=[artifact.id for artifact in result.artifacts],
            )
            kernel = services.runtime_kernel or RuntimeKernel()
            added_artifacts = [artifact.id for artifact in result.artifacts]
            return {
                "research_request": request.model_dump(mode="json"),
                "research_result": result.model_dump(mode="json"),
                **({"retrieval_snapshot": retrieval_snapshot.model_dump(mode="json"), "retrieval_artifact_ids": list(retrieval_snapshot.artifact_ids)} if retrieval_snapshot is not None else {}),
                "artifact_ids": artifact_ids,
                "status": "researched",
                "transitions": _append_transition(state, "research", "researched", "Fresh external research evidence collected", added_artifacts),
                "controller_decisions": _append_controller_decision(state, kernel.controller_decision(stage="research", selected_next_stage=kernel.next_after_research(route), reason="Research/retrieval complete; controller selected next stage from route requirements.")),
            }

    def research_next(self, state: WorkflowState) -> str:
            services = self.services
            readiness_gate = self.readiness_gate
            if state.get("research_result"):
                result = ObservationResult.model_validate(state["research_result"])
                if not result.ok:
                    return "finalize"
            decision = RoutingDecision.model_validate(state["route_decision"])
            kernel = services.runtime_kernel or RuntimeKernel()
            return kernel.next_after_research(decision)

    async def observe_node(self, state: WorkflowState) -> dict[str, Any]:
            services = self.services
            readiness_gate = self.readiness_gate
            readiness_gate.require(state, "observe", "task", "route_decision")
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
                "transitions": _append_transition(state, "observe", "observed", "World/repository observation evidence collected", [artifact.id for artifact in result.artifacts]),
            }

    def observe_next(self, state: WorkflowState) -> str:
            services = self.services
            readiness_gate = self.readiness_gate
            if state.get("observation_result"):
                result = ObservationResult.model_validate(state["observation_result"])
                if not result.ok:
                    return "finalize"
            return "build_context"

    async def build_context_node(self, state: WorkflowState) -> dict[str, Any]:
            services = self.services
            readiness_gate = self.readiness_gate
            readiness_gate.require(state, "build_context", "task")
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
            for artifact_id in state.get("retrieval_artifact_ids") or []:
                try:
                    artifact = services.artifact_store.get(artifact_id)
                except KeyError:
                    continue
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
                "transitions": _append_transition(state, "build_context", "context_built", "ContextPacket built from artifacts", [artifact.id]),
            }

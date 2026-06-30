from __future__ import annotations

from .common import *


class PlanningPolicyStageMixin:
    async def obligation_analysis_node(self, state: WorkflowState) -> dict[str, Any]:
            services = self.services
            readiness_gate = self.readiness_gate
            readiness_gate.require(state, "obligations", "task", "classification", "route_decision", "context_packet", "done_contract")
            task = Task.model_validate(state["task"])
            classification = TaskClassification.model_validate(state["classification"])
            route = RoutingDecision.model_validate(state["route_decision"])
            context_packet_raw = state.get("context_packet")
            if context_packet_raw is None:
                raise RuntimeError("context_packet missing")
            context_packet = ContextPacket.model_validate(context_packet_raw)
            await _emit(services, "stage_started", "obligations", "Refining obligations from done contract and evidence before planning", task_id=task.id)
            done_contract_raw = state.get("done_contract")
            done_contract_text = json.dumps(done_contract_raw, ensure_ascii=False, indent=2) if done_contract_raw is not None else "{}"
            request = LLMRequest(
                kind="obligation_analysis",
                prompt=(
                    build_obligation_analysis_prompt(task, classification, route, context_packet)
                    + "\n\nExisting DoneContract (treat as the compiled minimum completion contract; refine but do not contradict repository facts):\n"
                    + done_contract_text
                ),
                task_id=task.id,
                task_text=task.description,
                context_packet_id=context_packet.id,
                input_artifact_ids=list(context_packet.artifact_ids),
                instructions=["derive obligations from the context packet and compiled done contract", "return structured completion requirements"],
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
                "transitions": _append_transition(state, "obligations", "obligations_synthesized", "Evidence-backed obligations synthesized", [artifact.id]),
            }

    async def plan_node(self, state: WorkflowState) -> dict[str, Any]:
            services = self.services
            readiness_gate = self.readiness_gate
            readiness_gate.require(state, "plan", "task", "classification", "context_packet", "obligations", "done_contract")
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
            done_contract = state.get("done_contract")
            done_contract_text = json.dumps(done_contract, ensure_ascii=False, indent=2) if done_contract is not None else "{}"
            request = LLMRequest(
                kind="planning",
                prompt=build_plan_prompt(task, context_packet, _effective_task_intent(classification), obligations) + "\n\nDoneContract:\n" + done_contract_text,
                task_id=task.id,
                task_text=task.description,
                context_packet_id=context_packet.id,
                input_artifact_ids=list(context_packet.artifact_ids),
                instructions=["plan from typed obligations and context packet", "do not assume unobserved world facts"],
                metadata={"model_slot": "plan", "model_override": _llm_model_for(services, "plan")},
            )
            result, parsed = await services.llm_backend.complete_json(request, ExecutionPlan)
            parsed = _merge_plan_with_obligations(parsed, obligations)
            kernel = services.runtime_kernel or RuntimeKernel()
            acceptance_contract = kernel.build_acceptance_contract(
                task=task,
                classification=classification,
                plan=parsed,
                obligations=obligations,
            )
            artifact = services.artifact_store.add_json("execution_plan", parsed.model_dump(mode="json"))
            acceptance_artifact = services.artifact_store.add_json("task_acceptance_contract", acceptance_contract.model_dump(mode="json"), metadata={"task_id": task.id, "plan_id": parsed.id})
            await _emit(
                services,
                "stage_completed",
                "plan",
                "Execution plan and acceptance contract generated",
                execution_family=parsed.execution_family.value,
                task_intent=parsed.task_intent,
                deliverable_kind=parsed.deliverable_kind,
                requires_mutation=parsed.requires_mutation,
                acceptance_obligations=len(acceptance_contract.obligations),
                artifact_id=artifact.id,
                acceptance_artifact_id=acceptance_artifact.id,
            )
            return {
                "plan_request": request.model_dump(mode="json"),
                "plan_result": result.model_dump(mode="json"),
                "plan": parsed.model_dump(mode="json"),
                "acceptance_contract": acceptance_contract.model_dump(mode="json"),
                "artifact_ids": [*_append_artifact_id(state.get("artifact_ids"), artifact.id), acceptance_artifact.id],
                "status": "planned",
                "transitions": _append_transition(state, "plan", "planned", "Execution plan and acceptance contract generated from ContextPacket and obligations", [artifact.id, acceptance_artifact.id]),
            }

    async def policy_node(self, state: WorkflowState) -> dict[str, Any]:
            services = self.services
            readiness_gate = self.readiness_gate
            readiness_gate.require(state, "policy", "task", "classification", "route_decision", "plan")
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
                "transitions": _append_transition(state, "policy", "policy_checked", "Policy and evidence gates evaluated", [artifact.id]),
                "controller_decisions": _append_controller_decision(state, (services.runtime_kernel or RuntimeKernel()).controller_decision(stage="policy", selected_next_stage=(services.runtime_kernel or RuntimeKernel()).next_after_policy(decision), reason="Policy decision controls whether execution, approval, or finalize is next.")),
            }

    def policy_next(self, state: WorkflowState) -> str:
            services = self.services
            readiness_gate = self.readiness_gate
            kernel = services.runtime_kernel or RuntimeKernel()
            return kernel.next_after_policy(state["policy_decision"])

    async def approval_node(self, state: WorkflowState) -> dict[str, Any]:
            services = self.services
            readiness_gate = self.readiness_gate
            readiness_gate.require(state, "approval", "policy_decision")
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
                "transitions": _append_transition(state, "approval", "approval_resolved", "Approval provider resolved policy request", [artifact.id]),
                "controller_decisions": _append_controller_decision(state, (services.runtime_kernel or RuntimeKernel()).controller_decision(stage="approval", selected_next_stage=(services.runtime_kernel or RuntimeKernel()).next_after_approval(reviewed), reason="Approval result controls execution eligibility.")),
            }

    def approval_next(self, state: WorkflowState) -> str:
            services = self.services
            readiness_gate = self.readiness_gate
            kernel = services.runtime_kernel or RuntimeKernel()
            return kernel.next_after_approval(state.get("approval_request"))

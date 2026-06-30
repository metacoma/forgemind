from __future__ import annotations

from .common import *
from artifact_workflow_runtime.control_plane.stage_filters import execute_prompt_steps, execute_success_criteria as build_execute_success_criteria, execute_verification_commands


class ExecutionStageMixin:
    async def execute_node(self, state: WorkflowState) -> dict[str, Any]:
            services = self.services
            readiness_gate = self.readiness_gate
            readiness_gate.require(state, "execute", "task", "plan", "context_packet")
            task = Task.model_validate(state["task"])
            await _emit(services, "stage_started", "execute", "Executing plan in OpenHands", task_id=task.id)
            plan = ExecutionPlan.model_validate(state["plan"])
            observation_result = ObservationResult.model_validate(state["observation_result"]) if state.get("observation_result") else None
            context_packet = ContextPacket.model_validate(state["context_packet"]) if state.get("context_packet") else None
            observation_text = (render_structured_evidence_summary(observation_result.structured_evidence) if observation_result else "No observation evidence was collected.")
            context_text = context_packet.text if context_packet else ""
            execute_steps = execute_prompt_steps(plan)
            execute_success = build_execute_success_criteria(plan)
            prompt = (
                "You are executing an approved controller plan.\n"
                "Use the environment as needed and make the requested changes.\n"
                "Ground your work in the evidence below.\n"
                "The original task intent is primary; do not silently degrade implementation work into analysis-only output.\n"
                "This is the execute stage only: edit files, install required local dependencies, and run build/unit/integration checks.\n"
                "Repository publication is handled by a later publish stage. Do not commit, push, create a PR, wait for PR checks, or report missing publication as an execute blocker.\n\n"
                f"Task: {task.description}\n\n"
                f"ContextPacket:\n{context_text}\n\n"
                f"Observation evidence:\n{observation_text}\n\n"
                f"Plan summary: {plan.summary}\n"
                "Execute-stage steps:\n"
                + "\n".join(f"- {step}" for step in execute_steps)
                + "\n\nExecute-stage success criteria:\n"
                + "\n".join(f"- {item}" for item in execute_success)
                + "\n\nThe environment is a Docker container. Install any dependencies required to run the required test levels inside the container.\n"
                + f"Required setup steps: {plan.required_setup_steps}\n"
                + f"Required test levels: {plan.required_test_levels}\n"
                + "\nWhen finished, report concrete evidence: changed files, commands run, outputs, setup/install steps, test/build results, blockers. Do not list deferred publication as a blocker."
            )
            request = ExecutionRequest(
                task_id=task.id,
                execution_family=plan.execution_family,
                capabilities=_execution_capabilities(plan),
                prompt=prompt,
                objective="execute approved controller plan",
                plan_steps=execute_steps,
                expected_changes=list(plan.expected_repo_changes),
                verification_commands=execute_verification_commands(plan),
                scope_constraints=["do not choose next workflow step", "do not expand task scope", "collect structured evidence"],
                plan_summary=plan.summary,
                context_packet_id=context_packet.id if context_packet else None,
                artifact_ids=list(state.get("artifact_ids") or []),
                success_criteria=execute_success,
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
                "transitions": _append_transition(state, "execute", "executed", "Bounded OpenHands execution packet finished", [artifact.id for artifact in result.artifacts]),
                "controller_decisions": _append_controller_decision(state, (services.runtime_kernel or RuntimeKernel()).controller_decision(stage="execute", selected_next_stage="review", reason="Execution completed; review gate must evaluate the candidate revision before QA.")),
            }

    def execute_next(self, state: WorkflowState) -> str:
            services = self.services
            readiness_gate = self.readiness_gate
            plan = ExecutionPlan.model_validate(state["plan"])
            execution = ExecutionResult.model_validate(state["execution_result"])
            kernel = services.runtime_kernel or RuntimeKernel()
            return "review"

    async def execution_review_node(self, state: WorkflowState) -> dict[str, Any]:
            services = self.services
            readiness_gate = self.readiness_gate
            readiness_gate.require(state, "execution_review", "task", "plan", "execution_result")
            task = Task.model_validate(state["task"])
            plan = ExecutionPlan.model_validate(state["plan"])
            execution = ExecutionResult.model_validate(state["execution_result"])
            contract = TaskAcceptanceContract.model_validate(state["acceptance_contract"]) if state.get("acceptance_contract") else None
            kernel = services.runtime_kernel or RuntimeKernel()
            await _emit(services, "stage_started", "execution_review", "Reviewing lifecycle transition after execute", task_id=task.id)
            decision = kernel.review_execution(plan=plan, execution=execution, acceptance_contract=contract)
            artifact = services.artifact_store.add_json("lifecycle_transition_decision", decision.model_dump(mode="json"), metadata={"task_id": task.id, "event": decision.event.value})
            update: dict[str, Any] = {
                "execution_review_decision": decision.model_dump(mode="json"),
                "artifact_ids": _append_artifact_id(state.get("artifact_ids"), artifact.id),
                "status": "execution_reviewed",
                "lifecycle_decisions": _append_lifecycle_decision(state, decision),
                "transitions": _append_transition(state, "execution_review", "execution_reviewed", decision.reason, [artifact.id]),
                "controller_decisions": _append_controller_decision(state, kernel.controller_decision(stage="execution_review", selected_next_stage=decision.graph_next, reason=decision.reason)),
            }
            if not decision.allowed and contract is not None:
                acceptance = kernel.acceptance_from_lifecycle_violation(contract=contract, execution=execution, decision=decision)
                acceptance_artifact = services.artifact_store.add_json("acceptance_decision", acceptance.model_dump(mode="json"), metadata={"task_id": task.id, "source": "lifecycle_violation"})
                update["acceptance_decision"] = acceptance.model_dump(mode="json")
                update["artifact_ids"] = _append_artifact_id(update["artifact_ids"], acceptance_artifact.id)
            await _emit(services, "stage_completed", "execution_review", "Lifecycle transition reviewed", allowed=decision.allowed, next_stage=decision.graph_next, violations=[item.code for item in decision.violations])
            return update

    def execution_review_next(self, state: WorkflowState) -> str:
            services = self.services
            readiness_gate = self.readiness_gate
            decision = (state.get("execution_review_decision") or {})
            next_stage = str(decision.get("graph_next") or "verify") if isinstance(decision, dict) else "verify"
            return next_stage if next_stage in {"verify", "publish", "acceptance", "finalize"} else "verify"

    async def repair_node(self, state: WorkflowState) -> dict[str, Any]:
            services = self.services
            readiness_gate = self.readiness_gate
            readiness_gate.require(state, "repair", "task", "plan", "execution_result")
            task = Task.model_validate(state["task"])
            plan = ExecutionPlan.model_validate(state["plan"])
            execution = ExecutionResult.model_validate(state["execution_result"])
            publish = PublishResult.model_validate(state["publish_result"]) if state.get("publish_result") else None
            context_packet = ContextPacket.model_validate(state["context_packet"]) if state.get("context_packet") else None
            attempt = len(state.get("repair_results") or []) + 1
            if publish is not None:
                failed_checks = _publish_failed_check_names(publish)
                blocker_summaries = _publish_blocker_summaries(publish)
                publish_summary = publish.summary
            else:
                qa_review = state.get("qa_review_result") or {}
                verification = state.get("verification_result") or {}
                failed_checks = list(qa_review.get("failing_checks") or verification.get("checks_failed") or [])
                blocker_summaries = list(qa_review.get("environment_blockers") or verification.get("missing_evidence") or [])
                publish_summary = "No publish result; repairing after review/QA failure."
            await _emit(services, "stage_started", "repair", "Running bounded repair packet after failed publish/check evidence", task_id=task.id, attempt=attempt, failed_checks=failed_checks)
            prompt = (
                "You are performing a bounded repair packet after publish/PR checks reported failures.\n"
                "Do not commit, push, create or update PRs, wait PR checks, or choose the next workflow step.\n"
                "Make only the smallest source/test changes needed to address the controller-provided failed checks, then run the relevant local checks and return structured evidence.\n\n"
                f"Task: {task.description}\n\n"
                f"Failed checks: {failed_checks}\n"
                f"Publish blockers: {blocker_summaries}\n"
                f"Previous execution summary: {execution.summary}\n"
                f"Publish summary: {publish_summary}\n\n"
                f"Plan summary: {plan.summary}\n"
                "Plan steps:\n" + "\n".join(f"- {step}" for step in plan.steps) + "\n\n"
                "Return changed files, commands run, test results, blockers, and repair summary as structured evidence."
            )
            request = RepairRequest(
                task_id=task.id,
                execution_result_id=execution.id,
                publish_result_id=publish.id if publish is not None else None,
                attempt=attempt,
                max_attempts=2,
                execution_family=plan.execution_family,
                prompt=prompt,
                failed_checks=failed_checks,
                blocker_summaries=blocker_summaries,
                plan_steps=list(plan.steps),
                expected_changes=list(plan.expected_repo_changes),
                scope_constraints=["do not choose next workflow step", "do not expand task scope", "do not commit/push/create PR", "repair only controller-provided failures"],
                context_packet_id=context_packet.id if context_packet else None,
                artifact_ids=list(state.get("artifact_ids") or []),
                metadata={"model_slot": "execute", "model_override": _openhands_model_for(services, "execute"), "repair_attempt": attempt},
            )
            result = await services.openhands_adapter.repair(request)
            artifact_ids = list(state.get("artifact_ids") or [])
            artifact_ids.extend(artifact.id for artifact in result.execution_result.artifacts)
            repair_requests = [*(state.get("repair_requests") or []), request.model_dump(mode="json")]
            repair_results = [*(state.get("repair_results") or []), result.model_dump(mode="json")]
            await _emit(services, "stage_completed", "repair", "Repair packet completed", ok=result.ok, attempt=attempt, artifact_ids=[artifact.id for artifact in result.execution_result.artifacts])
            return {
                "repair_requests": repair_requests,
                "repair_results": repair_results,
                "execution_result": result.execution_result.model_dump(mode="json"),
                "publish_request": None,
                "publish_result": None,
                "publish_review_decision": None,
                "verification_request": None,
                "verification_result": None,
                "verification_check_requests": [],
                "verification_check_results": [],
                "acceptance_decision": None,
                "artifact_ids": artifact_ids,
                "status": "repaired",
                "transitions": _append_transition(state, "repair", "repaired", "Bounded repair packet finished; lifecycle requires review before continuing", [artifact.id for artifact in result.execution_result.artifacts]),
                "controller_decisions": _append_controller_decision(state, (services.runtime_kernel or RuntimeKernel()).controller_decision(stage="repair", selected_next_stage="review", reason="Repair completed; review must re-evaluate the candidate revision before QA.")),
            }

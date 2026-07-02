from __future__ import annotations

from .common import *
from artifact_workflow_runtime.control_plane.stage_filters import execute_prompt_steps, execute_success_criteria as build_execute_success_criteria, execute_verification_commands
from artifact_workflow_runtime.environment import EnvironmentPlan
from artifact_workflow_runtime.state.workspace import infer_workspace_root_from_execution, workspace_root_from_state

def _runtime_sensitive_levels(levels: list[str] | None) -> bool:
    lowered = {str(level).strip().lower() for level in (levels or []) if str(level).strip()}
    return bool(lowered & {"integration", "smoke", "e2e", "end-to-end", "runtime", "runtime_proof"})


def _has_explicit_non_environment_failure(result: ExecutionResult) -> bool:
    evidence = result.structured_evidence
    for command in evidence.commands_run:
        if command.exit_code not in (None, 0):
            return True
    for test in evidence.tests:
        status = str(test.status).lower()
        if status in {"failed", "error"}:
            return True
    return False


def _packet_status_from_typed_execution_result(
    result: ExecutionResult,
    *,
    required_test_levels: list[str] | None = None,
    required_setup_steps: list[str] | None = None,
) -> object:
    status = _packet_status_from_execution_result(result)
    if status.value != "failed":
        return status
    runtime_sensitive = _runtime_sensitive_levels(required_test_levels) or bool(required_setup_steps)
    if not runtime_sensitive:
        return status
    if _has_explicit_non_environment_failure(result):
        return status
    if str(result.execution_status.value if hasattr(result.execution_status, "value") else result.execution_status).lower() in {"partial", "blocked"} and bool(result.structured_evidence.blockers):
        from artifact_workflow_runtime.decomposition import ExecutionPacketStatus
        return ExecutionPacketStatus.BLOCKED
    return status

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
            strategy_block = _active_strategy_prompt_block(services, state)

            decomposition_plan_raw = state.get("decomposition_plan")
            if decomposition_plan_raw is None:
                acceptance_contract = state.get("acceptance_contract")
                obligations = state.get("obligations")
                decomposition_plan = _planner_for(services).build_plan(
                    task=task,
                    strategy_id=state.get("active_strategy"),
                    acceptance_contract=acceptance_contract,
                    obligations=obligations,
                    snapshot=WorkflowStateSnapshot.from_graph_state(state),
                )
                decomposition_artifact = services.artifact_store.add_json(
                    "decomposition_plan",
                    decomposition_plan.model_dump(mode="json"),
                    metadata={"task_id": task.id, "source": "execute_fallback"},
                )
                state_artifact_ids = [*_append_artifact_id(state.get("artifact_ids"), decomposition_artifact.id)]
            else:
                from artifact_workflow_runtime.decomposition import DecompositionPlan
                decomposition_plan = DecompositionPlan.model_validate(decomposition_plan_raw)
                decomposition_artifact = None
                state_artifact_ids = list(state.get("artifact_ids") or [])

            packet_selection = _selector_for(services).select(plan=decomposition_plan, active_strategy=state.get("active_strategy"))
            packet_selection_artifact = services.artifact_store.add_json(
                "packet_selection",
                packet_selection.model_dump(mode="json"),
                metadata={"task_id": task.id, "plan_id": decomposition_plan.plan_id, "stage": "execute"},
            )
            state_artifact_ids.append(packet_selection_artifact.id)
            active_packet_id = packet_selection.selected_packet_id
            packet = _packet_from_state({"active_packet_id": active_packet_id}, decomposition_plan)
            packet_block = _packet_prompt_block(packet)
            env_plan = EnvironmentPlan.model_validate(state["environment_plan"]) if state.get("environment_plan") else None
            execute_steps = execute_prompt_steps(plan)
            execute_success = build_execute_success_criteria(plan)
            execute_verification = execute_verification_commands(plan)
            setup_block = _environment_materialization_block(env_plan, packet=packet)
            if setup_block["scoped_only"]:
                execute_steps = list(setup_block["suggested_steps"])
                execute_success = list(dict.fromkeys([*setup_block["success_criteria"], *(packet.success_criteria if packet is not None else [])]))
                execute_verification = list(setup_block["verification_commands"])
            else:
                if setup_block["suggested_steps"]:
                    execute_steps = list(dict.fromkeys([*setup_block["suggested_steps"], *execute_steps]))
                if packet is not None and packet.success_criteria:
                    execute_success = list(dict.fromkeys([*execute_success, *packet.success_criteria]))

            prompt = (
                "You are executing an approved controller plan.\n"
                "Use the environment as needed and make the requested changes.\n"
                "Ground your work in the evidence below.\n"
                "Freshness/retrieval artifacts in the ContextPacket are the truth layer for current docs, versions, changelog, CLI flags, compatibility, and migration facts; do not guess current versions or docs from stale model memory.\n"
                "The original task intent is primary; do not silently degrade implementation work into analysis-only output.\n"
                "This is the execute stage only: edit files, install required local dependencies, and run build/unit/integration checks.\n"
                "Repository publication is handled by a later publish stage. Do not commit, push, create a PR, wait for PR checks, or report missing publication as an execute blocker.\n"
                "Runtime/bootstrap obligation: when the task or packet requires runtime, smoke, integration, post-deploy, or environment-sensitive proof, do not replace it with syntax checks, compile-only/build-only evidence, script existence, or an 'environment unavailable' claim before attempting any repository-supported bootstrap/setup/run path you discover.\n"
                "Setup completion requires evidence that bootstrap/setup was actually attempted and either made the prerequisite usable, failed with concrete output, or was inapplicable for a demonstrated reason. Found scripts alone are not setup success.\n"
                "In your final structured evidence, label each executed command in structured_evidence.commands_run.role and each executed check in structured_evidence.tests.level.\n\n"
                f"Task: {task.description}\n\n"
                f"ContextPacket:\n{context_text}\n\n"
                f"Observation evidence:\n{observation_text}\n\n"
                f"{strategy_block}\n\n"
                f"{packet_block}\n\n"
                + setup_block["prompt_block"]
                + "\n"
                + f"Plan summary: {plan.summary}\n"
                + "Execute-stage steps:\n"
                + "\n".join(f"- {step}" for step in execute_steps)
                + "\n\nExecute-stage success criteria:\n"
                + "\n".join(f"- {item}" for item in execute_success)
                + "\n\nThe environment is a Docker container. Install any dependencies required to run the required test levels inside the container.\n"
                + f"Required setup steps: {plan.required_setup_steps}\n"
                + f"Required test levels: {plan.required_test_levels}\n"
                + "\nWhen finished, report concrete evidence: changed files, commands run, outputs, setup/bootstrap attempts and outcomes, test/build/runtime results, blockers. Do not list deferred publication as a blocker."
            )
            request = ExecutionRequest(
                task_id=task.id,
                execution_family=plan.execution_family,
                capabilities=_execution_capabilities(plan),
                prompt=prompt,
                objective="execute approved controller plan",
                plan_steps=execute_steps,
                expected_changes=list(plan.expected_repo_changes),
                verification_commands=execute_verification,
                scope_constraints=["do not choose next workflow step", "do not expand task scope", "collect structured evidence"],
                plan_summary=plan.summary,
                context_packet_id=context_packet.id if context_packet else None,
                artifact_ids=list(state_artifact_ids),
                success_criteria=execute_success,
                expected_outputs=["changed_files", "commands_run", "setup_steps", "test_results", "blockers"],
                metadata={"evidence_required": True, "model_slot": "execute", "model_override": _openhands_model_for(services, "execute"), **_strategy_metadata(services, state), **_packet_metadata(packet)},
            )
            await _emit(services, "execution_request", "execute", "Execution request created", execution_family=request.execution_family.value, capability_count=len(request.capabilities), active_packet_id=active_packet_id)
            result = await services.openhands_adapter.execute(request)
            artifact_ids = list(state_artifact_ids)
            artifact_ids.extend(artifact.id for artifact in result.artifacts)
            workspace_root = infer_workspace_root_from_execution(result) or workspace_root_from_state(state)

            packet_history = list(state.get("packet_history") or [])
            updated_decomposition = decomposition_plan
            if packet is not None:
                packet_status = _packet_status_from_typed_execution_result(result, required_test_levels=list(plan.required_test_levels), required_setup_steps=list(plan.required_setup_steps))
                updated_decomposition, history_entry = _update_packet_status(
                    decomposition_plan,
                    packet_id=packet.packet_id,
                    new_status=packet_status,
                    reason=result.summary,
                    stage="execute",
                    execution_result_id=result.id,
                )
                packet_history = _append_packet_history(state, history_entry)
                packet_status_artifact = services.artifact_store.add_json(
                    "packet_status_update",
                    history_entry.model_dump(mode="json"),
                    metadata={"task_id": task.id, "packet_id": packet.packet_id, "stage": "execute"},
                )
                artifact_ids.append(packet_status_artifact.id)

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
                active_packet_id=active_packet_id,
            )
            added_artifacts = [artifact.id for artifact in result.artifacts]
            if decomposition_artifact is not None:
                added_artifacts.append(decomposition_artifact.id)
            added_artifacts.append(packet_selection_artifact.id)
            if packet is not None:
                added_artifacts.extend([aid for aid in artifact_ids if aid not in (state.get("artifact_ids") or []) and aid not in added_artifacts])
            return {
                "execution_request": request.model_dump(mode="json"),
                "execution_result": result.model_dump(mode="json"),
                "workspace_root": workspace_root,
                "decomposition_plan": updated_decomposition.model_dump(mode="json"),
                "active_packet_id": active_packet_id,
                "packet_history": packet_history,
                "artifact_ids": artifact_ids,
                "status": "executed",
                "transitions": _append_transition(state, "execute", "executed", "Bounded OpenHands execution packet finished", added_artifacts),
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
            if (not decision.allowed) or (not execution.ok) or execution.stage_failure is not None or execution.structured_evidence.blockers:
                strategy_state = dict(state)
                strategy_state.update(update)
                strategy_update = await _record_strategy_checkpoint(services, strategy_state, checkpoint_stage="execution_review")
                update = _merge_strategy_update(update, strategy_update)
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
            strategy_update = await _record_strategy_checkpoint(services, state, checkpoint_stage="repair")
            strategy_state = dict(state)
            strategy_state.update(strategy_update)
            attempt = len(state.get("repair_results") or []) + 1
            execution_failures = _execution_repair_failure_summaries(execution)
            if publish is not None:
                failed_checks = _publish_failed_check_names(publish)
                blocker_summaries = _publish_blocker_summaries(publish)
                publish_summary = publish.summary
                failure_source = "publish/PR checks"
            else:
                qa_review = state.get("qa_review_result") or {}
                verification = state.get("verification_result") or {}
                failed_checks = [*execution_failures, *list(qa_review.get("failing_checks") or verification.get("checks_failed") or [])]
                blocker_summaries = [*execution_failures, *list(qa_review.get("environment_blockers") or verification.get("missing_evidence") or [])]
                publish_summary = "No publish result; repairing after review/QA failure."
                failure_source = "execution/review/QA failure"
            failed_checks = _unique(failed_checks)
            blocker_summaries = _unique(blocker_summaries)
            strategy_block = _active_strategy_prompt_block(services, strategy_state)
            decomposition_plan = None
            packet = None
            if state.get("decomposition_plan"):
                from artifact_workflow_runtime.decomposition import DecompositionPlan
                decomposition_plan = DecompositionPlan.model_validate(state["decomposition_plan"])
                packet = _packet_from_state(state, decomposition_plan)
            packet_block = _packet_prompt_block(packet)
            await _emit(services, "stage_started", "repair", "Running bounded repair packet after failed publish/check evidence", task_id=task.id, attempt=attempt, failed_checks=failed_checks)
            prompt = (
                f"You are performing a bounded repair packet after {failure_source}.\n"
                "Do not commit, push, create or update PRs, wait PR checks, or choose the next workflow step.\n"
                "Make only the smallest source/test changes needed to address the controller-provided failed checks, then run the relevant local checks and return structured evidence.\n"
                "If the failure is a build/compiler/test failure, inspect the exact generated/types involved and repair that failure before expanding scope.\n"
                "In your final structured evidence, label each executed command in structured_evidence.commands_run.role and each executed check in structured_evidence.tests.level.\n\n"
                f"Task: {task.description}\n\n"
                f"Failed checks: {failed_checks}\n"
                f"Blockers: {blocker_summaries}\n"
                f"Previous execution summary: {execution.summary}\n"
                f"Publish summary: {publish_summary}\n\n"
                f"{strategy_block}\n\n"
                f"{packet_block}\n\n"
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
                artifact_ids=list(strategy_state.get("artifact_ids") or []),
                metadata={"model_slot": "execute", "model_override": _openhands_model_for(services, "execute"), "repair_attempt": attempt, **_strategy_metadata(services, strategy_state), **_packet_metadata(packet)},
            )
            result = await services.openhands_adapter.repair(request)
            artifact_ids = list(strategy_state.get("artifact_ids") or [])
            artifact_ids.extend(artifact.id for artifact in result.execution_result.artifacts)
            repair_requests = [*(state.get("repair_requests") or []), request.model_dump(mode="json")]
            repair_results = [*(state.get("repair_results") or []), result.model_dump(mode="json")]
            packet_history = list(state.get("packet_history") or [])
            updated_decomposition = state.get("decomposition_plan")
            if decomposition_plan is not None and packet is not None:
                updated_plan, history_entry = _update_packet_status(
                    decomposition_plan,
                    packet_id=packet.packet_id,
                    new_status=_packet_status_from_typed_execution_result(result.execution_result, required_test_levels=list(plan.required_test_levels), required_setup_steps=list(plan.required_setup_steps)),
                    reason=result.execution_result.summary,
                    stage="repair",
                    execution_result_id=result.execution_result.id,
                )
                updated_decomposition = updated_plan.model_dump(mode="json")
                packet_history = _append_packet_history(state, history_entry)
                packet_status_artifact = services.artifact_store.add_json(
                    "packet_status_update",
                    history_entry.model_dump(mode="json"),
                    metadata={"task_id": task.id, "packet_id": packet.packet_id, "stage": "repair"},
                )
                artifact_ids.append(packet_status_artifact.id)
            await _emit(services, "stage_completed", "repair", "Repair packet completed", ok=result.ok, attempt=attempt, artifact_ids=[artifact.id for artifact in result.execution_result.artifacts], active_packet_id=state.get("active_packet_id"))
            update = {
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
                "decomposition_plan": updated_decomposition,
                "active_packet_id": state.get("active_packet_id"),
                "packet_history": packet_history,
                "artifact_ids": artifact_ids,
                "status": "repaired",
                "transitions": _append_transition(state, "repair", "repaired", "Bounded repair packet finished; lifecycle requires review before continuing", [artifact.id for artifact in result.execution_result.artifacts]),
                "controller_decisions": _append_controller_decision(strategy_state, (services.runtime_kernel or RuntimeKernel()).controller_decision(stage="repair", selected_next_stage="review", reason="Repair completed; review must re-evaluate the candidate revision before QA.")),
            }
            return _merge_strategy_update(update, strategy_update)


def _environment_materialization_block(env_plan: EnvironmentPlan | None, *, packet) -> dict[str, object]:
    if env_plan is None or not env_plan.items:
        return {"prompt_block": "", "suggested_steps": [], "success_criteria": [], "verification_commands": [], "scoped_only": False}
    packet_type = getattr(packet, "packet_type", None)
    packet_type_value = getattr(packet_type, "value", str(packet_type or ""))
    packet_nodes = set(str(item) for item in ((getattr(packet, "metadata", {}) or {}).get("environment_nodes") or []))
    relevant_items = []
    for item in env_plan.items:
        applicable = set(item.applicable_packet_types or [])
        if packet_nodes and item.name not in packet_nodes:
            continue
        if applicable and packet_type_value and packet_type_value not in applicable:
            continue
        relevant_items.append(item)
    if not relevant_items:
        relevant_items = list(env_plan.items)

    prompt_lines: list[str] = ["Concrete environment/runtime dependency nodes:"]
    steps: list[str] = []
    success_criteria: list[str] = []
    verification_commands: list[str] = []
    scoped_only = packet_type_value == "setup"
    for item in relevant_items:
        bootstrap_commands = [action.command for action in item.bootstrap_actions if action.command]
        probe_commands = [action.command for action in item.runtime_probe_actions if action.command]
        if not bootstrap_commands and item.bootstrap_command:
            bootstrap_commands = [item.bootstrap_command]
        if not probe_commands and item.runtime_probe_command:
            probe_commands = [item.runtime_probe_command]
        prompt_lines.append(
            f"- {item.name} [{item.dependency_kind}]: bootstrap_actions={bootstrap_commands or ['none']}; runtime_probes={probe_commands or ['none']}"
        )
        if scoped_only:
            for command in bootstrap_commands:
                steps.append(f"Materialize environment dependency {item.name}: {command}")
            for command in probe_commands:
                steps.append(f"Probe readiness for environment dependency {item.name}: {command}")
                verification_commands.append(command)
            success_criteria.append(f"Environment dependency node ready: {item.name}")
        else:
            for command in bootstrap_commands[:1]:
                steps.append(f"If prerequisite {item.name} is still missing, attempt materialization: {command}")
            for command in probe_commands[:1]:
                steps.append(f"When runtime-sensitive proof is needed, verify {item.name} via: {command}")
    prompt_lines.append("Environment materialization is a separate proof layer. Do not claim setup success from file discovery, syntax checks, or compile-only evidence.")
    return {
        "prompt_block": "\n".join(prompt_lines),
        "suggested_steps": list(dict.fromkeys(steps)),
        "success_criteria": list(dict.fromkeys(success_criteria)),
        "verification_commands": list(dict.fromkeys(verification_commands)),
        "scoped_only": scoped_only,
    }

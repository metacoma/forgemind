from __future__ import annotations

import json

from .common import *
from artifact_workflow_runtime.done_contract import DoneContract
from artifact_workflow_runtime.environment import EnvironmentPlan
from artifact_workflow_runtime.qa import QAExecutionReport, QAPlan, QAReview
from artifact_workflow_runtime.state.workspace import workspace_root_from_state
from artifact_workflow_runtime.decomposition import DecompositionPlan, DecompositionProgressDecision


class ReviewQAStageMixin:
    async def review_node(self, state: WorkflowState) -> dict[str, Any]:
        services = self.services
        readiness_gate = self.readiness_gate
        readiness_gate.require(state, "review", "task", "plan", "execution_result", "done_contract")
        task = Task.model_validate(state["task"])
        plan = ExecutionPlan.model_validate(state["plan"])
        execution = ExecutionResult.model_validate(state["execution_result"])
        done_contract = DoneContract.model_validate(state["done_contract"])
        contract = TaskAcceptanceContract.model_validate(state["acceptance_contract"]) if state.get("acceptance_contract") else None
        kernel = services.runtime_kernel or RuntimeKernel()
        await _emit(services, "stage_started", "review", "Reviewing execution against done contract", task_id=task.id)
        lifecycle = kernel.review_execution(plan=plan, execution=execution, acceptance_contract=contract)
        missing = _missing_deliverables(done_contract, execution)
        status = "pass"
        summary_parts = []
        failing_checks: list[str] = []
        env_blockers: list[str] = []
        if not lifecycle.allowed:
            status = "policy_violation"
            summary_parts.append(lifecycle.reason)
            failing_checks.extend(item.code for item in lifecycle.violations)
        repairable_failures = _execution_repair_failure_summaries(execution)
        if repairable_failures and status == "pass":
            status = "fail_code"
            summary_parts.append("Execution produced repairable build/test/code failures: " + "; ".join(repairable_failures))
            failing_checks.extend(repairable_failures)
        elif execution.stage_failure is not None and status == "pass":
            status = "fail_code"
            summary_parts.append(execution.stage_failure.summary)
        # Missing derived deliverables are important, but they should flow into
        # QA/acceptance and possible obligation re-entry rather than forcing an
        # eager repair loop immediately after execute. This keeps review focused
        # on execution discipline and policy boundaries.
        if missing:
            summary_parts.append("Review noted missing derived deliverables for downstream QA/acceptance: " + ", ".join(missing))
        if not summary_parts:
            summary_parts.append("Execution evidence satisfies review gate and can enter QA planning.")
        review = QAReview(task_id=task.id, status=status, summary=" ".join(summary_parts), failing_checks=_unique(failing_checks), environment_blockers=_unique(env_blockers))
        artifact = services.artifact_store.add_json("review_result", review.model_dump(mode="json"), metadata={"task_id": task.id, "status": status})
        selected_next = "qa_plan" if status == "pass" else ("finalize" if status == "policy_violation" else "repair")
        packet_progression = None
        progression_artifact_id = None
        next_active_packet_id = state.get("active_packet_id")
        if status == "pass" and state.get("decomposition_plan") is not None:
            decomposition_plan = DecompositionPlan.model_validate(state["decomposition_plan"])
            packet_progression = kernel.evaluate_decomposition_progression(
                decomposition_plan=decomposition_plan,
                active_strategy=state.get("active_strategy"),
                current_packet_id=state.get("active_packet_id"),
            )
            if packet_progression is not None:
                selected_next = kernel.next_stage_after_decomposition_progression(packet_progression)
                next_active_packet_id = packet_progression.selected_next_packet_id if selected_next == "execute" else None
                progression_artifact = services.artifact_store.add_json(
                    "packet_progression",
                    packet_progression.model_dump(mode="json"),
                    metadata={"task_id": task.id, "plan_id": decomposition_plan.plan_id, "stage": "review"},
                )
                progression_artifact_id = progression_artifact.id
        await _emit(services, "stage_completed", "review", "Execution review completed", status=status, failing_checks=review.failing_checks, next_stage=selected_next, artifact_id=artifact.id)
        review_artifact_ids = _append_artifact_id(state.get("artifact_ids"), artifact.id)
        if progression_artifact_id is not None:
            review_artifact_ids = _append_artifact_id(review_artifact_ids, progression_artifact_id)
        transition_artifacts = [artifact.id]
        if progression_artifact_id is not None:
            transition_artifacts.append(progression_artifact_id)
        update = {
            "review_result": review.model_dump(mode="json"),
            "execution_review_decision": lifecycle.model_dump(mode="json"),
            "artifact_ids": review_artifact_ids,
            "active_packet_id": next_active_packet_id,
            **({"packet_progression": packet_progression.model_dump(mode="json")} if packet_progression is not None else {}),
            "status": "reviewed",
            "transitions": _append_transition(state, "review", "reviewed", review.summary, transition_artifacts),
            "controller_decisions": _append_controller_decision(state, kernel.controller_decision(stage="review", selected_next_stage=selected_next, reason=(packet_progression.reason if packet_progression is not None else review.summary))),
        }
        if status == "policy_violation" and contract is not None:
            acceptance = kernel.acceptance_from_lifecycle_violation(contract=contract, execution=execution, decision=lifecycle)
            acceptance_artifact = services.artifact_store.add_json("acceptance_decision", acceptance.model_dump(mode="json"), metadata={"task_id": task.id, "source": "review_lifecycle_violation"})
            update["acceptance_decision"] = acceptance.model_dump(mode="json")
            update["artifact_ids"] = _append_artifact_id(update["artifact_ids"], acceptance_artifact.id)
        if status != "pass" or missing or (packet_progression is not None and packet_progression.blocked):
            strategy_state = dict(state)
            strategy_state.update(update)
            strategy_update = await _record_strategy_checkpoint(services, strategy_state, checkpoint_stage="review")
            update = _merge_strategy_update(update, strategy_update)
        return update

    def review_next(self, state: WorkflowState) -> str:
        review = QAReview.model_validate(state["review_result"])
        if review.status == "pass":
            progression = DecompositionProgressDecision.model_validate(state["packet_progression"]) if state.get("packet_progression") else None
            kernel = self.services.runtime_kernel or RuntimeKernel()
            next_stage = kernel.next_stage_after_decomposition_progression(progression)
            return next_stage if next_stage in {"execute", "qa_plan", "finalize"} else "qa_plan"
        if review.status == "fail_code":
            return "repair"
        return "finalize"

    async def qa_plan_node(self, state: WorkflowState) -> dict[str, Any]:
        services = self.services
        readiness_gate = self.readiness_gate
        readiness_gate.require(state, "qa_plan", "task", "plan", "done_contract")
        task = Task.model_validate(state["task"])
        execution_plan = ExecutionPlan.model_validate(state["plan"])
        done_contract = DoneContract.model_validate(state["done_contract"])
        env_plan = EnvironmentPlan.model_validate(state["environment_plan"]) if state.get("environment_plan") else None
        await _emit(services, "stage_started", "qa_plan", "Building deterministic QA plan", task_id=task.id)
        qa_plan = services.qa_planner.build_plan(task_id=task.id, execution_plan=execution_plan, done_contract=done_contract, environment_plan=env_plan)
        artifact = services.artifact_store.add_json("qa_plan", qa_plan.model_dump(mode="json"), metadata={"task_id": task.id})
        await _emit(services, "stage_completed", "qa_plan", "QA plan compiled", checks=[item.model_dump(mode="json") for item in qa_plan.checks], artifact_id=artifact.id)
        return {
            "qa_plan": qa_plan.model_dump(mode="json"),
            "artifact_ids": _append_artifact_id(state.get("artifact_ids"), artifact.id),
            "status": "qa_planned",
            "transitions": _append_transition(state, "qa_plan", "qa_planned", "Deterministic QA plan compiled from done contract and execution plan.", [artifact.id]),
        }

    async def qa_execute_node(self, state: WorkflowState) -> dict[str, Any]:
        services = self.services
        readiness_gate = self.readiness_gate
        readiness_gate.require(state, "qa_execute")
        task = Task.model_validate(state["task"])
        qa_plan = QAPlan.model_validate(state["qa_plan"])
        env_plan = EnvironmentPlan.model_validate(state["environment_plan"]) if state.get("environment_plan") else None
        await _emit(services, "stage_started", "qa_execute", "Running deterministic QA plan", task_id=task.id)
        workspace_root = workspace_root_from_state(state)
        report = services.qa_runner.run(plan=qa_plan, environment_plan=env_plan, cwd=workspace_root)
        artifact = services.artifact_store.add_json("qa_execution_report", report.model_dump(mode="json"), metadata={"task_id": task.id})
        await _emit(services, "stage_completed", "qa_execute", "Deterministic QA execution completed", summary=report.summary, artifact_id=artifact.id)
        return {
            "qa_execution_report": report.model_dump(mode="json"),
            "workspace_root": report.workspace_root or workspace_root,
            "artifact_ids": _append_artifact_id(state.get("artifact_ids"), artifact.id),
            "status": "qa_executed",
            "transitions": _append_transition(state, "qa_execute", "qa_executed", report.summary, [artifact.id]),
        }

    async def qa_review_node(self, state: WorkflowState) -> dict[str, Any]:
        services = self.services
        readiness_gate = self.readiness_gate
        readiness_gate.require(state, "qa_review", "task", "plan", "execution_result", "context_packet", "qa_execution_report")
        task = Task.model_validate(state["task"])
        plan = ExecutionPlan.model_validate(state["plan"])
        execution = ExecutionResult.model_validate(state["execution_result"])
        context_packet = ContextPacket.model_validate(state["context_packet"])
        report = QAExecutionReport.model_validate(state["qa_execution_report"])
        artifact_ids = list(state.get("artifact_ids") or [])
        qa_report_artifact = services.artifact_store.add_text(
            "qa_execution_summary",
            json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
            metadata={"task_id": task.id},
        )
        artifact_ids.append(qa_report_artifact.id)

        request = VerificationRequest(
            execution_result_id=execution.id,
            execution_family=plan.execution_family,
            backend=BackendKind.DIRECT_LLM,
            mode=VerificationMode.EVIDENCE_REVIEW,
            prompt=build_verification_prompt(task, context_packet, plan, execution, None) + "\n\nDeterministic QA execution report:\n" + json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
            artifact_ids=artifact_ids,
            checks=list(plan.verification_checks),
            metadata={"mode": "evidence_only", "source": "qa_review"},
        )
        check_requests: list[dict[str, Any]] = []
        check_results: list[dict[str, Any]] = []

        if services.model_routing and services.model_routing.verification_checks:
            request, result, artifact_ids, check_requests, check_results = await _run_check_routed_verification(
                services,
                task=task,
                plan=plan,
                execution=execution,
                publish=None,
                context_packet=context_packet,
                base_request=request,
                artifact_ids=artifact_ids,
            )
            result.artifacts.append(qa_report_artifact)
        else:
            llm_request = LLMRequest(
                kind="verification",
                prompt=request.prompt,
                task_id=task.id,
                task_text=task.description,
                context_packet_id=context_packet.id,
                input_artifact_ids=list(artifact_ids),
                instructions=["review structured artifacts and evidence text only", "treat deterministic QA execution report as first-class evidence"],
                metadata={"model_slot": "verify", "model_override": _llm_model_for(services, "verify")},
            )
            llm_result, parsed = await services.llm_backend.complete_json(llm_request, EvidenceVerification)
            verification_artifact = services.artifact_store.add_json("verification_assessment", parsed.model_dump(mode="json"))
            llm_artifact = services.artifact_store.add_text(
                "verification_llm_raw",
                llm_result.raw_text,
                metadata={"request_id": llm_request.id, "backend": llm_result.backend, "model": llm_result.model},
            )
            artifact_ids.extend([verification_artifact.id, llm_artifact.id])
            completion_status = _normalized_completion_status(
                parsed.passed,
                parsed.missing_evidence,
                parsed.checks_passed,
                parsed.checks_failed,
                parsed.missing_test_levels,
                parsed.missing_setup_steps,
                parsed.missing_obligations,
                parsed.commit_required,
                parsed.push_required,
                parsed.commit_done,
                parsed.push_done,
                parsed.completion_status,
            )
            request.metadata = {"mode": "evidence_only", "llm_request_id": llm_request.id, "source": "qa_review"}
            result = VerificationResult(
                request_id=request.id,
                passed=parsed.passed,
                summary=parsed.summary,
                evidence_text=llm_result.raw_text,
                artifacts=[verification_artifact, llm_artifact, qa_report_artifact],
                checks_passed=parsed.checks_passed,
                checks_failed=parsed.checks_failed,
                missing_evidence=parsed.missing_evidence,
                confidence=parsed.confidence,
                verifier_backend=llm_result.backend or "direct_llm",
                performed_test_levels=parsed.performed_test_levels,
                missing_test_levels=parsed.missing_test_levels,
                setup_steps_performed=parsed.setup_steps_performed,
                missing_setup_steps=parsed.missing_setup_steps,
                commit_required=parsed.commit_required,
                push_required=parsed.push_required,
                commit_done=parsed.commit_done,
                push_done=parsed.push_done,
                pr_detected=parsed.pr_detected,
                pr_checks_waited=parsed.pr_checks_waited,
                pr_checks_passed=parsed.pr_checks_passed,
                pr_checks_failed=parsed.pr_checks_failed,
                pr_checks_pending=parsed.pr_checks_pending,
                missing_obligations=parsed.missing_obligations,
                completion_status=completion_status,
            )

        review = _qa_review_from_verification(task_id=task.id, result=result, report=report, execution=execution)
        review_artifact = services.artifact_store.add_json("qa_review_result", review.model_dump(mode="json"), metadata={"task_id": task.id, "status": review.status})
        artifact_ids.append(review_artifact.id)
        kernel = services.runtime_kernel or RuntimeKernel()
        selected_next = "acceptance" if review.status in {"pass", "fail_env"} else ("repair" if review.status == "fail_code" else "finalize")
        if review.status == "fail_code":
            loop_decision = _pipeline_continue_decision(
                "qa_review",
                "Repairable execution/build/test failure detected; bounded repair takes precedence over pipeline re-entry budget.",
            )
            reentry_target = None
            selected_next = "repair"
        else:
            loop_decision = kernel.evaluate_pipeline_reentry(
                source_stage="verify",
                plan=plan,
                obligations=ObligationAnalysis.model_validate(state["obligations"]) if state.get("obligations") else None,
                verification=result,
                loop_decisions=_pipeline_loop_decisions(state),
            )
            reentry_target = _reentry_target(loop_decision)
            if reentry_target is not None:
                selected_next = reentry_target
        await _emit(services, "stage_completed", "qa_review", "QA review completed", status=review.status, next_stage=selected_next, summary=review.summary)
        transition_artifacts = [review_artifact.id]
        transition_artifacts.extend(item for item in artifact_ids if item not in (state.get("artifact_ids") or []))
        update = {
            "verification_request": request.model_dump(mode="json"),
            "verification_check_requests": check_requests,
            "verification_check_results": check_results,
            "verification_result": result.model_dump(mode="json"),
            "qa_review_result": review.model_dump(mode="json"),
            "pipeline_loop_decisions": _append_pipeline_loop_decision(state, loop_decision),
            "artifact_ids": artifact_ids,
            "status": "qa_reviewed",
            "transitions": _append_transition(state, "qa_review", "qa_reviewed", review.summary, transition_artifacts),
            "controller_decisions": _append_controller_decision(state, kernel.controller_decision(stage="qa_review", selected_next_stage=selected_next, reason=review.summary)),
        }
        if reentry_target is not None:
            update.update(_clear_for_reentry(reentry_target))
        if review.status != "pass" or result.missing_evidence or result.checks_failed or result.missing_test_levels or result.missing_obligations:
            strategy_state = dict(state)
            strategy_state.update(update)
            strategy_update = await _record_strategy_checkpoint(services, strategy_state, checkpoint_stage="qa_review")
            update = _merge_strategy_update(update, strategy_update)
        return update

    def qa_review_next(self, state: WorkflowState) -> str:
        review = QAReview.model_validate(state["qa_review_result"])
        if review.status == "fail_code":
            return "repair"
        loop_decisions = state.get("pipeline_loop_decisions") or []
        if loop_decisions:
            loop = PipelineLoopDecision.model_validate(loop_decisions[-1])
            target = _reentry_target(loop)
            if target in {"research", "observe", "build_context", "obligations", "plan", "finalize"}:
                return target
        if review.status in {"pass", "fail_env"}:
            return "acceptance"
        return "finalize"


def _missing_deliverables(done_contract: DoneContract, execution: ExecutionResult) -> list[str]:
    evidence_text = " ".join(
        [
            execution.summary,
            execution.evidence_text,
            " ".join(item.path for item in execution.structured_evidence.files_changed),
            " ".join(item.name for item in execution.structured_evidence.tests),
            " ".join((item.command or "") for item in execution.structured_evidence.tests),
            " ".join(item.command for item in execution.structured_evidence.commands_run),
        ]
    ).lower()
    missing: list[str] = []
    if "runtime_proof" in done_contract.deliverables and not _has_real_runtime_proof(execution):
        missing.append("runtime_proof")
    if "integration_test_or_equivalent" in done_contract.deliverables and not _has_real_integration_or_smoke_proof(execution):
        missing.append("integration_test_or_equivalent")
    if "ci_update_if_tests_added" in done_contract.deliverables and ".github/workflows" not in evidence_text and "workflow" not in evidence_text:
        missing.append("ci_update_if_tests_added")
    if "documentation_update" in done_contract.deliverables and not any(marker in evidence_text for marker in ("readme", "docs", "documentation")):
        missing.append("documentation_update")
    if "example_update" in done_contract.deliverables and not any(marker in evidence_text for marker in ("example", "sample", "snippet")):
        missing.append("example_update")
    return _unique(missing)


def _has_real_runtime_proof(execution: ExecutionResult) -> bool:
    return _has_real_integration_or_smoke_proof(execution) or any(
        _is_runtime_evidence_text(f"{test.name} {test.command or ''} {test.output_excerpt or ''}")
        and str(test.status).lower() in {"passed", "success", "succeeded", "ok"}
        for test in execution.structured_evidence.tests
    )


def _has_real_integration_or_smoke_proof(execution: ExecutionResult) -> bool:
    for test in execution.structured_evidence.tests:
        text = f"{test.name} {test.command or ''} {test.output_excerpt or ''}"
        if str(test.status).lower() in {"passed", "success", "succeeded", "ok"} and _is_runtime_evidence_text(text):
            return True
    for command in execution.structured_evidence.commands_run:
        text = f"{command.command} {command.output_excerpt or ''}"
        if command.exit_code == 0 and _is_runtime_evidence_text(text):
            return True
    return False


def _is_runtime_evidence_text(text: str) -> bool:
    lowered = text.lower()
    if any(marker in lowered for marker in ("bash -n", "sh -n", "syntax check", "script exists", "found script", "integration project build", "test project build", "build integration tests", "compiled integration tests")):
        return False
    build_only = any(marker in lowered for marker in ("cmake --build", "mvn compile", "gradle assemble", "./gradlew assemble", "go build", "npm run build"))
    runtime_marker = any(marker in lowered for marker in ("smoke", "integration", "e2e", "end-to-end", "handshake", "freeplane", "grpc", "runtime proof"))
    actual_execution = any(marker in lowered for marker in ("pytest", "go test", "cargo test", "mvn test", "gradle test", "./gradlew test", "npm test", "run_smoke", "run smoke", "run_integration", "run integration", "smoke test"))
    return runtime_marker and (actual_execution or not build_only)


def _qa_review_from_verification(*, task_id: str, result: VerificationResult, report: QAExecutionReport, execution: ExecutionResult | None = None) -> QAReview:
    env_blockers = [item.name for item in report.items if item.status == "blocked"]
    repairable_execution_failures = _execution_repair_failure_summaries(execution)
    code_failure_text = " ".join([result.summary, *result.checks_failed, *repairable_execution_failures]).lower()
    code_like = bool(repairable_execution_failures) or any(
        marker in code_failure_text
        for marker in ("build", "compile", "compiler", "cs0", "dotnet test", "unit test", "test failed", "execution failure")
    )
    environment_like = bool(env_blockers)
    environment_like = environment_like or bool(result.missing_setup_steps)
    environment_like = environment_like or any(level in {"integration", "smoke", "e2e"} for level in result.missing_test_levels)
    environment_like = environment_like or any(
        token in str(item).lower()
        for item in result.missing_obligations
        for token in ("environment", "install", "bootstrap", "runtime prerequisite", "dependency", "freeplane")
    )
    if result.passed:
        status = "pass"
    elif code_like:
        status = "fail_code"
    elif environment_like:
        status = "fail_env"
    else:
        status = "fail_requirements"
    failing = [*result.checks_failed, *env_blockers, *result.missing_test_levels, *result.missing_setup_steps, *repairable_execution_failures]
    return QAReview(task_id=task_id, status=status, summary=result.summary, failing_checks=_unique(failing), environment_blockers=_unique(env_blockers))

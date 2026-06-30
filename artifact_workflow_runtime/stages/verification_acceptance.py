from __future__ import annotations

from .common import *


class VerificationAcceptanceStageMixin:
    async def verify_node(self, state: WorkflowState) -> dict[str, Any]:
            services = self.services
            readiness_gate = self.readiness_gate
            readiness_gate.require(state, "verify", "task", "plan", "execution_result", "context_packet")
            task = Task.model_validate(state["task"])
            await _emit(services, "stage_started", "verify", "Verifying execution evidence", task_id=task.id)
            plan = ExecutionPlan.model_validate(state["plan"])
            execution = ExecutionResult.model_validate(state["execution_result"])
            publish = PublishResult.model_validate(state["publish_result"]) if state.get("publish_result") else None
            artifact_ids = list(state.get("artifact_ids") or [])
            request = VerificationRequest(
                execution_result_id=execution.id,
                execution_family=plan.execution_family,
                backend=BackendKind.DIRECT_LLM,
                mode=VerificationMode.EVIDENCE_REVIEW,
                prompt="evidence_verification",
                artifact_ids=artifact_ids,
                checks=list(plan.verification_checks),
                metadata={"mode": "evidence_only"},
            )
            check_requests: list[dict[str, Any]] = []
            check_results: list[dict[str, Any]] = []
            if not execution.ok:
                parsed = EvidenceVerification(
                    passed=False,
                    summary="Execution did not produce usable evidence, so verification failed.",
                    checks_passed=[],
                    checks_failed=list(plan.verification_checks),
                    missing_evidence=["usable execution evidence"],
                    confidence="high" if execution.transport_error else "medium",
                    reasoning="Verification is blocked because execution evidence was empty or transport-corrupted.",
                    pr_detected=False,
                    pr_checks_waited=False,
                    pr_checks_passed=[],
                    pr_checks_failed=[],
                    pr_checks_pending=[],
                    missing_obligations=["usable execution evidence"],
                    completion_status="blocked",
                )
                raw_text = parsed.model_dump_json(indent=2)
                verification_artifact = services.artifact_store.add_json("verification_assessment", parsed.model_dump(mode="json"))
                artifact_ids.append(verification_artifact.id)
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
                result = VerificationResult(
                    request_id=request.id,
                    passed=parsed.passed,
                    summary=parsed.summary,
                    evidence_text=raw_text,
                    artifacts=[verification_artifact],
                    checks_passed=parsed.checks_passed,
                    checks_failed=parsed.checks_failed,
                    missing_evidence=parsed.missing_evidence,
                    confidence=parsed.confidence,
                    verifier_backend="evidence_guard",
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
            else:
                context_packet_raw = state.get("context_packet")
                if context_packet_raw is None:
                    raise RuntimeError("context_packet missing")
                context_packet = ContextPacket.model_validate(context_packet_raw)
                kernel = services.runtime_kernel or RuntimeKernel()
                strategy = kernel.verification_strategy(
                    plan=plan,
                    execution=execution,
                    publish=publish,
                    per_check_routing_enabled=bool(services.model_routing and services.model_routing.verification_checks),
                )
                if strategy.requires_world_check:
                    prompt = (
                        "You are performing a bounded world verification packet for the controller.\n"
                        "Do not choose the next workflow step. Do not expand task scope. Do not publish.\n"
                        "Run only the checks requested by the controller and report commands, outputs, statuses, blockers, and missing evidence.\n\n"
                        f"Task: {task.description}\n\n"
                        f"ContextPacket:\n{context_packet.text}\n\n"
                        f"Execution summary: {execution.summary}\n"
                        f"Checks: {plan.verification_checks}\n"
                    )
                    request = VerificationRequest(
                        execution_result_id=execution.id,
                        execution_family=plan.execution_family,
                        backend=BackendKind.OPENHANDS,
                        mode=VerificationMode.WORLD_CHECK,
                        prompt=prompt,
                        artifact_ids=artifact_ids,
                        checks=list(plan.verification_checks),
                        allowed_inputs=["filesystem", "shell", "git", "test_runtime", "context_packet_text"],
                        forbidden_inputs=["change_workflow_decision", "declare_task_completed_or_accepted", "expand_task_scope", "edit_files", "write_files", "fix_code", "repair", "commit", "push", "git push", "git push --force", "git tag", "git merge", "git rebase", "create_pr", "open_pull_request", "publish", "release", "mutate_without_explicit_check_need"],
                        expected_outputs=["commands_run", "check_statuses", "outputs", "blockers", "missing_evidence"],
                        metadata={"mode": "world_check", "controller_reason": strategy.reason, "model_slot": "verify", "model_override": _openhands_model_for(services, "verify")},
                    )
                    result = await services.openhands_adapter.verify(request)
                    artifact_ids.extend(artifact.id for artifact in result.artifacts)
                elif strategy.per_check:
                    request, result, artifact_ids, check_requests, check_results = await _run_check_routed_verification(
                        services,
                        task=task,
                        plan=plan,
                        execution=execution,
                        publish=publish,
                        context_packet=context_packet,
                        base_request=request,
                        artifact_ids=artifact_ids,
                    )
                else:
                    llm_request = LLMRequest(
                        kind="verification",
                        prompt=build_verification_prompt(task, context_packet, plan, execution, publish),
                        task_id=task.id,
                        task_text=task.description,
                        context_packet_id=context_packet.id,
                        input_artifact_ids=list(artifact_ids),
                        instructions=["review structured artifacts and evidence text only", "separate missing evidence from failed checks"],
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
                    request = VerificationRequest(
                        execution_result_id=execution.id,
                        execution_family=plan.execution_family,
                        backend=BackendKind.DIRECT_LLM,
                        mode=VerificationMode.EVIDENCE_REVIEW,
                        prompt=llm_request.prompt,
                        artifact_ids=artifact_ids,
                        checks=list(plan.verification_checks),
                        metadata={"mode": "evidence_only", "llm_request_id": llm_request.id},
                    )
                    result = VerificationResult(
                        request_id=request.id,
                        passed=parsed.passed,
                        summary=parsed.summary,
                        evidence_text=llm_result.raw_text,
                        artifacts=[verification_artifact, llm_artifact],
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
            kernel = services.runtime_kernel or RuntimeKernel()
            obligations = ObligationAnalysis.model_validate(state["obligations"]) if state.get("obligations") else None
            loop_decision = kernel.evaluate_pipeline_reentry(
                source_stage="verify",
                plan=plan,
                obligations=obligations,
                verification=result,
                publish=publish,
                loop_decisions=_pipeline_loop_decisions(state),
            )
            selected_next = _reentry_target(loop_decision) or "acceptance"
            await _emit(
                services,
                "stage_completed",
                "verify",
                "Verification completed",
                passed=result.passed,
                confidence=result.confidence,
                checks_passed=len(result.checks_passed),
                checks_failed=len(result.checks_failed),
                missing_evidence=list(result.missing_evidence),
                missing_test_levels=list(result.missing_test_levels),
                missing_obligations=list(result.missing_obligations),
                pr_detected=result.pr_detected,
                pr_checks_waited=result.pr_checks_waited,
                pr_checks_failed=list(result.pr_checks_failed),
                pr_checks_pending=list(result.pr_checks_pending),
                completion_status=result.completion_status,
                pipeline_reentry=selected_next if selected_next != "acceptance" else None,
                reentry_trigger=loop_decision.trigger_kind.value,
            )
            update = {
                "verification_request": request.model_dump(mode="json"),
                "verification_check_requests": check_requests,
                "verification_check_results": check_results,
                "verification_result": result.model_dump(mode="json"),
                "pipeline_loop_decisions": _append_pipeline_loop_decision(state, loop_decision),
                "artifact_ids": artifact_ids,
                "status": "verified",
                "transitions": _append_transition(state, "verify", "verified", f"Verification completed with status {result.completion_status}", [artifact.id for artifact in result.artifacts]),
                "controller_decisions": _append_controller_decision(state, kernel.controller_decision(stage="verify", selected_next_stage=selected_next, reason=loop_decision.reason if selected_next != "acceptance" else "Verification result recorded; acceptance gate must decide final completion.")),
            }
            if selected_next != "acceptance":
                update.update(_clear_for_reentry(selected_next))
            return update

    def verify_next(self, state: WorkflowState) -> str:
            services = self.services
            readiness_gate = self.readiness_gate
            decisions = state.get("pipeline_loop_decisions") or []
            if decisions:
                decision = PipelineLoopDecision.model_validate(decisions[-1])
                if decision.source_stage == "verify":
                    target = _reentry_target(decision)
                    if target in {"research", "observe", "build_context", "done_contract", "obligations", "plan", "finalize"}:
                        return target
            return "acceptance"

    async def acceptance_node(self, state: WorkflowState) -> dict[str, Any]:
            services = self.services
            readiness_gate = self.readiness_gate
            readiness_gate.require(state, "acceptance", "task", "acceptance_contract")
            task = Task.model_validate(state["task"])
            await _emit(services, "stage_started", "acceptance", "Evaluating acceptance obligations", task_id=task.id)
            contract = TaskAcceptanceContract.model_validate(state["acceptance_contract"])
            execution = ExecutionResult.model_validate(state["execution_result"]) if state.get("execution_result") else None
            verification = VerificationResult.model_validate(state["verification_result"]) if state.get("verification_result") else None
            publish = PublishResult.model_validate(state["publish_result"]) if state.get("publish_result") else None
            decision = (services.runtime_kernel or RuntimeKernel()).evaluate_acceptance(
                contract=contract,
                execution=execution,
                verification=verification,
                publish=publish,
            )
            artifact = services.artifact_store.add_json("acceptance_decision", decision.model_dump(mode="json"), metadata={"task_id": task.id, "contract_id": contract.id})
            updated_verification = verification.model_copy(update={"acceptance_status": decision.status, "obligation_results": decision.obligation_results}) if verification is not None else None
            await _emit(
                services,
                "stage_completed",
                "acceptance",
                "Acceptance gate evaluated",
                accepted=decision.accepted,
                acceptance_status=decision.status.value,
                final_workflow_status=decision.final_workflow_status,
                blocking_results=[item.model_dump(mode="json") for item in decision.obligation_results if item.status.value != "passed"],
                artifact_id=artifact.id,
            )
            kernel = services.runtime_kernel or RuntimeKernel()
            plan = ExecutionPlan.model_validate(state["plan"]) if state.get("plan") else None
            lifecycle_decision = kernel.next_after_acceptance(
                plan=plan,
                acceptance=decision,
                execution=execution,
                verification=verification,
                publish=publish,
                acceptance_contract=contract,
            ) if plan is not None else None
            selected_next = lifecycle_decision.graph_next if lifecycle_decision is not None else "finalize"
            loop_decision = kernel.evaluate_pipeline_reentry(
                source_stage="acceptance",
                plan=plan,
                obligations=ObligationAnalysis.model_validate(state["obligations"]) if state.get("obligations") else None,
                verification=verification,
                acceptance=decision,
                publish=publish,
                loop_decisions=_pipeline_loop_decisions(state),
            )
            reentry_target = _reentry_target(loop_decision)
            if reentry_target is not None:
                selected_next = reentry_target
            update = {
                "acceptance_decision": decision.model_dump(mode="json"),
                **({"verification_result": updated_verification.model_dump(mode="json")} if updated_verification is not None else {}),
                "pipeline_loop_decisions": _append_pipeline_loop_decision(state, loop_decision),
                "artifact_ids": _append_artifact_id(state.get("artifact_ids"), artifact.id),
                "status": "acceptance_evaluated",
                "transitions": _append_transition(state, "acceptance", "acceptance_evaluated", f"Acceptance gate resolved as {decision.status.value}", [artifact.id]),
                **({"lifecycle_decisions": _append_lifecycle_decision(state, lifecycle_decision)} if lifecycle_decision is not None else {}),
                "controller_decisions": _append_controller_decision(state, kernel.controller_decision(stage="acceptance", selected_next_stage=selected_next, reason=(loop_decision.reason if reentry_target is not None else (lifecycle_decision.reason if lifecycle_decision is not None else decision.summary)))),
            }
            if reentry_target is not None:
                update.update(_clear_for_reentry(reentry_target))
            return update

    def acceptance_next(self, state: WorkflowState) -> str:
            services = self.services
            readiness_gate = self.readiness_gate
            loop_decisions = state.get("pipeline_loop_decisions") or []
            if loop_decisions:
                loop = PipelineLoopDecision.model_validate(loop_decisions[-1])
                if loop.source_stage == "acceptance":
                    target = _reentry_target(loop)
                    if target in {"research", "observe", "build_context", "done_contract", "obligations", "plan", "finalize"}:
                        return target
            decisions = state.get("lifecycle_decisions") or []
            if decisions:
                last = decisions[-1]
                if isinstance(last, dict) and last.get("event") == "acceptance_evaluated":
                    next_stage = str(last.get("graph_next") or "finalize")
                    return next_stage if next_stage in {"publish", "finalize"} else "finalize"
            return "finalize"

    async def finalize_node(self, state: WorkflowState) -> dict[str, Any]:
            services = self.services
            readiness_gate = self.readiness_gate
            readiness_gate.require(state, "finalize", "task")
            task = Task.model_validate(state["task"])
            await _emit(services, "stage_started", "finalize", "Assembling final report", task_id=task.id)
            report = services.final_report_builder.build(
                task=task,
                classification=TaskClassification.model_validate(state["classification"]) if state.get("classification") else None,
                route=RoutingDecision.model_validate(state["route_decision"]) if state.get("route_decision") else None,
                obligations=ObligationAnalysis.model_validate(state["obligations"]) if state.get("obligations") else None,
                done_contract=state.get("done_contract"),
                environment_plan=state.get("environment_plan"),
                plan=ExecutionPlan.model_validate(state["plan"]) if state.get("plan") else None,
                policy=PolicyDecision.model_validate(state["policy_decision"]) if state.get("policy_decision") else None,
                approval=ApprovalRequest.model_validate(state["approval_request"]) if state.get("approval_request") else None,
                research=ObservationResult.model_validate(state["research_result"]) if state.get("research_result") else None,
                observation=ObservationResult.model_validate(state["observation_result"]) if state.get("observation_result") else None,
                execution=ExecutionResult.model_validate(state["execution_result"]) if state.get("execution_result") else None,
                publish=PublishResult.model_validate(state["publish_result"]) if state.get("publish_result") else None,
                repair_results=[RepairResult.model_validate(item) for item in (state.get("repair_results") or [])],
                verification=VerificationResult.model_validate(state["verification_result"]) if state.get("verification_result") else None,
                qa_plan=state.get("qa_plan"),
                qa_execution_report=state.get("qa_execution_report"),
                qa_review_result=state.get("qa_review_result"),
                acceptance_contract=TaskAcceptanceContract.model_validate(state["acceptance_contract"]) if state.get("acceptance_contract") else None,
                acceptance_decision=AcceptanceDecision.model_validate(state["acceptance_decision"]) if state.get("acceptance_decision") else None,
                artifact_ids=list(state.get("artifact_ids") or []),
            )
            artifact = services.artifact_store.add_json("final_report", report.model_dump(mode="json"))
            await _emit(services, "stage_completed", "finalize", "Final report ready", status=report.status, artifact_id=artifact.id)
            return {
                "final_report": report.model_dump(mode="json"),
                "artifact_ids": _append_artifact_id(state.get("artifact_ids"), artifact.id),
                "status": report.status,
                "transitions": _append_transition(state, "finalize", report.status, "Final report assembled", [artifact.id]),
            }

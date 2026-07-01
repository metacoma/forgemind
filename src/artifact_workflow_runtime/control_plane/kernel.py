from __future__ import annotations

from dataclasses import dataclass
import re

from artifact_workflow_runtime.models import Capability, VerificationMode
from artifact_workflow_runtime.models import (
    AcceptanceDecision,
    AcceptanceObligation,
    AcceptanceObligationKind,
    AcceptanceObligationStatus,
    AcceptanceStatus,
    ApprovalRequest,
    BlockerKind,
    EnvironmentBlocker,
    ExecutionFamily,
    ExecutionPlan,
    ExecutionResult,
    ExecutionStatus,
    ObligationAnalysis,
    ObservationResult,
    PolicyDecision,
    PublishResult,
    RepairResult,
    RoutingDecision,
    Task,
    TaskAcceptanceContract,
    TaskClassification,
    VerificationObligationResult,
    VerificationResult,
)
from artifact_workflow_runtime.models.state import ControllerDecision, WorkflowStateSnapshot
from artifact_workflow_runtime.lifecycle import (
    LifecycleEvent,
    LifecycleFacts,
    LifecycleMachine,
    LifecycleStage,
    LifecycleTransitionDecision,
    PipelineLoopBudget,
    PipelineLoopDecision,
    PipelineLoopTriggerKind,
    PipelineReentryTarget,
    PolicyViolation,
)
from artifact_workflow_runtime.policy import PolicyEngine
from artifact_workflow_runtime.policy.evidence import EvidenceGate
from artifact_workflow_runtime.decomposition import DecompositionPlan, DecompositionProgressDecision, progression_decision


@dataclass(frozen=True, slots=True)
class StateReadiness:
    ready: bool
    missing_state_fields: list[str]
    reason: str


@dataclass(frozen=True, slots=True)
class VerificationStrategy:
    mode: VerificationMode
    per_check: bool
    requires_world_check: bool
    reason: str


class RuntimeKernel:
    """Controller decision kernel used by the LangGraph runtime.

    LangGraph executes nodes and persists state transitions; this object owns the
    workflow decisions that should not live in OpenHands prompts. OpenHands can
    return hints/evidence, but this kernel decides which workflow edge is taken.
    """

    def __init__(self, *, evidence_gate: EvidenceGate | None = None, lifecycle_machine: LifecycleMachine | None = None) -> None:
        self.evidence_gate = evidence_gate or EvidenceGate()
        self.lifecycle_machine = lifecycle_machine or LifecycleMachine()


    def fact_readiness(self, snapshot: WorkflowStateSnapshot) -> StateReadiness:
        missing = snapshot.require("classification", "route_decision")
        route = snapshot.route_decision
        if route is not None:
            if route.needs_fresh_external_research and snapshot.research_result is None:
                missing.append("research_result")
            if (route.needs_repository_observation or route.needs_world_observation) and snapshot.observation_result is None:
                missing.append("observation_result")
        return StateReadiness(not missing, missing, "Required observed facts are present." if not missing else "Controller cannot proceed until required facts are observed.")

    def planning_readiness(self, snapshot: WorkflowStateSnapshot) -> StateReadiness:
        missing = snapshot.require("task", "classification", "route_decision", "context_packet", "obligations")
        fact_readiness = self.fact_readiness(snapshot)
        missing = list(dict.fromkeys([*missing, *fact_readiness.missing_state_fields]))
        return StateReadiness(not missing, missing, "Context and obligations are ready for planning." if not missing else "Planning is blocked by missing typed state fields.")

    def execution_readiness(self, snapshot: WorkflowStateSnapshot) -> StateReadiness:
        missing = snapshot.require("plan", "policy_decision")
        if snapshot.policy_decision is not None:
            if snapshot.policy_decision.blocked:
                missing.append("policy_allowed")
            if snapshot.policy_decision.requires_approval and (snapshot.approval_request is None or snapshot.approval_request.approved is not True):
                missing.append("approval_request.approved")
        return StateReadiness(not missing, list(dict.fromkeys(missing)), "Execution is allowed by policy/approval." if not missing else "Execution is blocked by policy, approval, or missing plan state.")

    def verification_readiness(self, snapshot: WorkflowStateSnapshot) -> StateReadiness:
        missing = snapshot.require("plan", "execution_result")
        return StateReadiness(not missing, missing, "Execution evidence is present for verification." if not missing else "Verification is blocked until execution evidence exists.")

    def controller_decision(self, *, stage: str, selected_next_stage: str, reason: str, required_state_fields: list[str] | None = None, missing_state_fields: list[str] | None = None) -> ControllerDecision:
        return ControllerDecision(
            stage=stage,
            selected_next_stage=selected_next_stage,
            reason=reason,
            required_state_fields=required_state_fields or [],
            missing_state_fields=missing_state_fields or [],
        )

    def next_after_route(self, decision: RoutingDecision) -> str:
        if decision.needs_fresh_external_research:
            return "research"
        if decision.needs_repository_observation or decision.needs_world_observation:
            return "observe"
        return "build_context"

    def next_after_research(self, decision: RoutingDecision) -> str:
        if decision.needs_repository_observation or decision.needs_world_observation:
            return "observe"
        return "build_context"

    def can_plan(self, *, route: RoutingDecision, research: ObservationResult | None, observation: ObservationResult | None) -> tuple[bool, list[str]]:
        missing: list[str] = []
        if route.needs_fresh_external_research and research is None:
            missing.append("research_result")
        if (route.needs_repository_observation or route.needs_world_observation) and observation is None:
            missing.append("observation_result")
        return not missing, missing

    def evaluate_policy(
        self,
        *,
        classification: TaskClassification,
        route: RoutingDecision,
        plan: ExecutionPlan,
        policy_engine: PolicyEngine,
        research: ObservationResult | None,
        observation: ObservationResult | None,
    ) -> PolicyDecision:
        reasons: list[str] = []
        mismatch = _plan_intent_mismatch(classification, plan)
        if mismatch:
            reasons.append(mismatch)
        reasons.extend(self.evidence_gate.evaluate(route=route, plan=plan, research=research, observation=observation))
        if reasons:
            return PolicyDecision(
                allowed=False,
                blocked=True,
                requires_approval=False,
                reasons=reasons,
                execution_family=plan.execution_family,
                capabilities=list(dict.fromkeys([*classification.capabilities, *plan.capabilities])),
            )
        return policy_engine.decide(classification, plan)

    @staticmethod
    def next_after_policy(decision: PolicyDecision | dict[str, object]) -> str:
        blocked = decision.blocked if isinstance(decision, PolicyDecision) else bool(decision.get("blocked"))
        requires_approval = decision.requires_approval if isinstance(decision, PolicyDecision) else bool(decision.get("requires_approval"))
        if blocked:
            return "finalize"
        if requires_approval:
            return "approval"
        return "workspace_prepare"

    @staticmethod
    def next_after_approval(approval: ApprovalRequest | dict[str, object] | None) -> str:
        if approval is None:
            return "finalize"
        approved = approval.approved if isinstance(approval, ApprovalRequest) else approval.get("approved")
        return "workspace_prepare" if approved else "finalize"

    def next_after_execution(self, plan: ExecutionPlan, execution: ExecutionResult) -> str:
        # LangGraph no longer jumps from execute directly to publish/verify. The
        # lifecycle machine must review execute evidence and policy guards first.
        return "execution_review"

    def review_execution(
        self,
        *,
        plan: ExecutionPlan,
        execution: ExecutionResult,
        acceptance_contract: TaskAcceptanceContract | None = None,
    ) -> LifecycleTransitionDecision:
        facts = self.lifecycle_facts(
            plan=plan,
            execution=execution,
            acceptance_contract=acceptance_contract,
        )
        return self.lifecycle_machine.transition(
            from_stage=LifecycleStage.EXECUTING,
            event=LifecycleEvent.EXECUTION_FINISHED,
            facts=facts,
        )

    def next_after_execution_review(self, decision: LifecycleTransitionDecision) -> str:
        return decision.graph_next

    def evaluate_decomposition_progression(
        self,
        *,
        decomposition_plan: DecompositionPlan | None,
        active_strategy: str | None,
        current_packet_id: str | None,
    ) -> DecompositionProgressDecision | None:
        if decomposition_plan is None:
            return None
        return progression_decision(
            decomposition_plan,
            active_strategy=active_strategy,
            current_packet_id=current_packet_id,
        )

    def next_after_acceptance(
        self,
        *,
        plan: ExecutionPlan,
        acceptance: AcceptanceDecision,
        execution: ExecutionResult | None,
        verification: VerificationResult | None,
        publish: PublishResult | None,
        acceptance_contract: TaskAcceptanceContract | None = None,
    ) -> LifecycleTransitionDecision:
        facts = self.lifecycle_facts(
            plan=plan,
            execution=execution,
            verification=verification,
            publish=publish,
            acceptance=acceptance,
            acceptance_contract=acceptance_contract,
        )
        return self.lifecycle_machine.transition(
            from_stage=LifecycleStage.ACCEPTANCE,
            event=LifecycleEvent.ACCEPTANCE_EVALUATED,
            facts=facts,
        )

    def review_publish(
        self,
        *,
        plan: ExecutionPlan,
        execution: ExecutionResult | None,
        publish: PublishResult,
        acceptance_contract: TaskAcceptanceContract | None = None,
        repair_attempt_count: int = 0,
        max_repair_attempts: int = 2,
    ) -> LifecycleTransitionDecision:
        facts = self.lifecycle_facts(
            plan=plan,
            execution=execution,
            publish=publish,
            acceptance_contract=acceptance_contract,
            repair_attempt_count=repair_attempt_count,
            max_repair_attempts=max_repair_attempts,
        )
        return self.lifecycle_machine.transition(
            from_stage=LifecycleStage.PUBLISHING,
            event=LifecycleEvent.PUBLISH_FINISHED,
            facts=facts,
        )

    def lifecycle_facts(
        self,
        *,
        plan: ExecutionPlan | None = None,
        acceptance_contract: TaskAcceptanceContract | None = None,
        execution: ExecutionResult | None = None,
        verification: VerificationResult | None = None,
        publish: PublishResult | None = None,
        acceptance: AcceptanceDecision | None = None,
        repair_attempt_count: int = 0,
        max_repair_attempts: int = 2,
        reentry_required: bool = False,
        reentry_target_stage: PipelineReentryTarget = PipelineReentryTarget.CONTINUE,
        reentry_trigger_kind: PipelineLoopTriggerKind = PipelineLoopTriggerKind.NONE,
        reentry_budget_exhausted: bool = False,
        pipeline_loop_count: int = 0,
        trigger_loop_count: int = 0,
        source_stage_loop_count: int = 0,
        pipeline_loop_global_limit: int = 3,
        pipeline_loop_per_trigger_limit: int = 1,
        pipeline_loop_per_source_stage_limit: int = 2,
    ) -> LifecycleFacts:
        publish_required = _publish_required(plan) if plan is not None else False
        mutation_task = bool(plan and (plan.requires_mutation or plan.must_change_world or plan.expected_repo_changes))
        mandatory_verification_required = mutation_task or _requires_integration_verification(plan)
        environment_blocked = _environment_blocker(execution, verification, publish, required_for="lifecycle") is not None
        verification_ran, verification_passed, verification_failed = _check_status(execution, verification, publish, _verification_terms(plan))
        mandatory_verification_satisfied = bool(verification_passed and not verification_failed) if mandatory_verification_required else True
        if acceptance is not None and _acceptance_has_only_publish_obligation_missing(acceptance):
            mandatory_verification_satisfied = True
        execute_pr_created = _execute_pr_created(execution) if execution is not None else False
        execute_git_push = _execute_git_push(execution) if execution is not None else False
        execute_git_commit = _execute_git_commit(execution) if execution is not None else False
        execution_status = _derive_execution_status(execution) if execution is not None else ExecutionStatus.FAILED
        execution_has_blockers = bool(_non_deferred_blockers(execution.structured_evidence) if execution is not None else [])
        execution_stage_failed = bool(execution and execution.stage_failure is not None)
        publish_stage_failed = bool(publish and publish.stage_failure is not None)
        stage_failure_kind = None
        if execution_stage_failed and execution and execution.stage_failure is not None:
            stage_failure_kind = execution.stage_failure.failure_kind.value
        elif publish_stage_failed and publish and publish.stage_failure is not None:
            stage_failure_kind = publish.stage_failure.failure_kind.value
        execution_succeeded = bool(execution and execution.ok and not execution_stage_failed and execution_status == ExecutionStatus.SUCCEEDED and not execution_has_blockers)
        publish_failed_checks = _publish_failed_checks(publish)
        publish_has_blockers = bool(publish and publish.structured_evidence.blockers)
        publish_forbidden_action_detected = _publish_forbidden_action_detected(publish)
        return LifecycleFacts(
            plan=plan,
            acceptance_contract=acceptance_contract,
            execution=execution,
            verification=verification,
            publish=publish,
            acceptance=acceptance,
            publish_required=publish_required,
            publish_done=publish is not None and publish.ok and not publish_failed_checks and not publish_has_blockers,
            publish_failed_checks=publish_failed_checks,
            publish_has_blockers=publish_has_blockers,
            publish_forbidden_action_detected=publish_forbidden_action_detected,
            repair_attempt_count=repair_attempt_count,
            max_repair_attempts=max_repair_attempts,
            mutation_task=mutation_task,
            mandatory_verification_required=mandatory_verification_required,
            mandatory_verification_satisfied=mandatory_verification_satisfied,
            mandatory_verification_blocked=environment_blocked,
            mandatory_verification_missing=mandatory_verification_required and not verification_ran,
            environment_blocked=environment_blocked,
            execution_succeeded=execution_succeeded,
            execution_blocked=execution_status in {ExecutionStatus.BLOCKED, ExecutionStatus.FAILED} or execution_stage_failed,
            execution_has_blockers=execution_has_blockers,
            execution_stage_failed=execution_stage_failed,
            publish_stage_failed=publish_stage_failed,
            stage_failure_kind=stage_failure_kind,
            execute_pr_created=execute_pr_created,
            execute_git_push=execute_git_push,
            execute_git_commit=execute_git_commit,
            execute_forbidden_action_detected=execute_pr_created or execute_git_push or execute_git_commit,
            reentry_required=reentry_required,
            reentry_target_stage=reentry_target_stage,
            reentry_trigger_kind=reentry_trigger_kind,
            reentry_budget_exhausted=reentry_budget_exhausted,
            pipeline_loop_count=pipeline_loop_count,
            trigger_loop_count=trigger_loop_count,
            source_stage_loop_count=source_stage_loop_count,
            pipeline_loop_global_limit=pipeline_loop_global_limit,
            pipeline_loop_per_trigger_limit=pipeline_loop_per_trigger_limit,
            pipeline_loop_per_source_stage_limit=pipeline_loop_per_source_stage_limit,
        )

    def acceptance_from_lifecycle_violation(
        self,
        *,
        contract: TaskAcceptanceContract,
        execution: ExecutionResult | None,
        decision: LifecycleTransitionDecision,
    ) -> AcceptanceDecision:
        results = [
            VerificationObligationResult(
                obligation_id=f"lifecycle_{idx}",
                obligation_name=violation.code,
                kind=AcceptanceObligationKind.REQUIRED_EVIDENCE_PRESENT,
                status=AcceptanceObligationStatus.BLOCKED,
                reason=violation.message,
                evidence_artifact_ids=violation.evidence_artifact_ids,
                blocker_kind=violation.blocker_kind or BlockerKind.POLICY_BLOCKED,
            )
            for idx, violation in enumerate(decision.violations or [])
        ] or [
            VerificationObligationResult(
                obligation_id="lifecycle_transition_denied",
                obligation_name="Lifecycle transition denied",
                kind=AcceptanceObligationKind.REQUIRED_EVIDENCE_PRESENT,
                status=AcceptanceObligationStatus.BLOCKED,
                reason=decision.reason,
                blocker_kind=BlockerKind.POLICY_BLOCKED,
            )
        ]
        failure_codes = {item.code for item in (decision.violations or [])}
        producer_failed = any("no_usable_result" in code for code in failure_codes)
        return AcceptanceDecision(
            contract_id=contract.id,
            status=AcceptanceStatus.NEEDS_HUMAN_REVIEW,
            accepted=False,
            execution_status=_derive_execution_status(execution) if execution is not None else ExecutionStatus.FAILED,
            final_workflow_status="agent_failed" if producer_failed else "control_plane_violation",
            summary=("OpenHands stage did not return usable operational evidence; workflow stopped before verification/acceptance could claim success." if producer_failed else decision.reason),
            obligation_results=results,
        )

    def build_acceptance_contract(
        self,
        *,
        task: Task,
        classification: TaskClassification,
        plan: ExecutionPlan,
        obligations: ObligationAnalysis | None = None,
    ) -> TaskAcceptanceContract:
        """Derive mandatory completion gates from typed plan/obligations.

        This contract is the acceptance source of truth. Verification can supply
        evidence, but finalization cannot mark success unless these blocking
        obligations pass.
        """

        obligation_items: list[AcceptanceObligation] = []

        def add(kind: AcceptanceObligationKind, name: str, *, checks: list[str] | None = None, source: str = "controller", env: list[str] | None = None) -> None:
            if any(existing.kind == kind and existing.name == name for existing in obligation_items):
                return
            obligation_items.append(
                AcceptanceObligation(
                    kind=kind,
                    name=name,
                    required=True,
                    blocking=True,
                    source=source,
                    checks=checks or [],
                    required_environment=env or [],
                )
            )

        requires_mutation = bool(plan.requires_mutation or plan.must_change_world or classification.task_intent in {"implement", "modify"})
        if requires_mutation:
            add(AcceptanceObligationKind.CODE_CHANGED, "Repository/world mutation evidence exists", checks=list(plan.expected_repo_changes))

        all_test_text = _lower_join([*plan.required_test_levels, *plan.verification_checks, *plan.success_criteria])
        if any(marker in all_test_text for marker in ("build", "compile", "cmake", "gradle", "mvn", "make")):
            add(AcceptanceObligationKind.BUILD_OR_COMPILE_SUCCEEDED, "Build/compile obligation passed", checks=list(plan.required_test_levels))

        if plan.required_test_levels or plan.verification_checks:
            add(AcceptanceObligationKind.RELEVANT_TESTS_RUN, "Relevant verification checks were run", checks=[*plan.required_test_levels, *plan.verification_checks])
            add(AcceptanceObligationKind.RELEVANT_TESTS_PASSED, "Relevant verification checks passed", checks=[*plan.required_test_levels, *plan.verification_checks])

        if any(marker in all_test_text for marker in ("integration", "e2e", "end-to-end", "freeplane", "grpc smoke")):
            add(AcceptanceObligationKind.INTEGRATION_TESTS_RUN, "Integration verification was run", checks=[*plan.required_test_levels, *plan.verification_checks])
            add(AcceptanceObligationKind.INTEGRATION_TESTS_PASSED, "Integration verification passed", checks=[*plan.required_test_levels, *plan.verification_checks])

        env_prereqs = list(plan.environment_notes)
        if obligations is not None:
            env_prereqs.extend(obligations.required_environment_conditions)
            env_prereqs.extend(obligations.required_setup_steps)
        env_prereqs = _unique_str(env_prereqs)
        if env_prereqs or any(marker in all_test_text for marker in ("integration", "freeplane", "x11", "display")):
            add(
                AcceptanceObligationKind.ENVIRONMENT_PREREQUISITES_SATISFIED,
                "Required verification environment/prerequisites were available",
                checks=env_prereqs,
                env=env_prereqs,
            )

        if obligations is not None:
            if obligations.required_documentation_updates:
                add(AcceptanceObligationKind.DOCUMENTATION_UPDATED, "Required documentation updates completed", checks=list(obligations.required_documentation_updates), source="obligation_discovery")
            if obligations.required_examples_updates:
                add(AcceptanceObligationKind.EXAMPLES_UPDATED, "Required examples/snippets updates completed", checks=list(obligations.required_examples_updates), source="obligation_discovery")
            if obligations.required_ci_updates:
                add(AcceptanceObligationKind.CI_OR_BUILD_UPDATED, "Required CI/build updates completed", checks=list(obligations.required_ci_updates), source="obligation_discovery")
            if obligations.required_codegen_or_build_updates:
                add(AcceptanceObligationKind.CODEGEN_OR_TOOLING_UPDATED, "Required codegen/tooling/build updates completed", checks=list(obligations.required_codegen_or_build_updates), source="obligation_discovery")
            if obligations.affected_surfaces or obligations.discovered_impacts:
                add(AcceptanceObligationKind.WORK_SURFACE_COMPLETE, "Discovered work surface obligations completed", checks=[*obligations.affected_surfaces, *[impact.summary for impact in obligations.discovered_impacts if impact.blocking]], source="obligation_discovery")

        if _publish_required(plan):
            add(AcceptanceObligationKind.PUBLISH_OBLIGATIONS_SATISFIED, "Publish/commit/push obligations satisfied", checks=list(plan.publication_steps))

        if not obligation_items:
            add(AcceptanceObligationKind.REQUIRED_EVIDENCE_PRESENT, "Required evidence is present", checks=list(plan.verification_checks))

        return TaskAcceptanceContract(
            task_id=task.id,
            execution_family=plan.execution_family,
            requires_mutation=requires_mutation,
            mutation_requires_verification=requires_mutation,
            obligations=obligation_items,
            required_environment_prerequisites=env_prereqs,
        )

    def evaluate_acceptance(
        self,
        *,
        contract: TaskAcceptanceContract,
        execution: ExecutionResult | None,
        verification: VerificationResult | None,
        publish: PublishResult | None = None,
    ) -> AcceptanceDecision:
        if execution is None:
            result = VerificationObligationResult(
                obligation_id="missing_execution",
                obligation_name="Execution evidence exists",
                kind=AcceptanceObligationKind.REQUIRED_EVIDENCE_PRESENT,
                status=AcceptanceObligationStatus.NOT_RUN,
                reason="No execution result is available for acceptance.",
                blocker_kind=BlockerKind.MISSING_EVIDENCE,
            )
            return AcceptanceDecision(
                contract_id=contract.id,
                status=AcceptanceStatus.NEEDS_HUMAN_REVIEW,
                accepted=False,
                execution_status=ExecutionStatus.FAILED,
                final_workflow_status="needs_human_review",
                summary="Acceptance cannot be evaluated because execution evidence is missing.",
                obligation_results=[result],
            )

        results = [self._evaluate_obligation(obligation, execution=execution, verification=verification, publish=publish) for obligation in contract.obligations]
        blocking_results = [item for item in results if _contract_obligation_by_id(contract, item.obligation_id).blocking]
        accepted = bool(blocking_results) and all(item.status == AcceptanceObligationStatus.PASSED for item in blocking_results)
        env_blockers = [item.environment_blocker for item in results if item.environment_blocker is not None]
        statuses = {item.status for item in blocking_results}

        execution_status = _derive_execution_status(execution)
        if accepted:
            status = AcceptanceStatus.ACCEPTED
            final_status = "completed"
            summary = "All required acceptance obligations passed."
        elif env_blockers:
            status = AcceptanceStatus.NEEDS_ENVIRONMENT
            final_status = "needs_environment"
            summary = "Acceptance is blocked by missing required verification environment or runtime prerequisites."
        elif AcceptanceObligationStatus.BLOCKED in statuses:
            status = AcceptanceStatus.BLOCKED
            final_status = "blocked"
            summary = "Acceptance is blocked by at least one required obligation."
        elif AcceptanceObligationStatus.FAILED in statuses:
            status = AcceptanceStatus.REJECTED
            final_status = "partially_completed" if _has_mutation_evidence(execution) else "failed"
            summary = "Acceptance rejected because at least one required verification obligation failed."
        else:
            status = AcceptanceStatus.NEEDS_HUMAN_REVIEW
            final_status = "needs_human_review"
            summary = "Acceptance cannot be granted because at least one required obligation was not run or lacks evidence."

        return AcceptanceDecision(
            contract_id=contract.id,
            status=status,
            accepted=accepted,
            execution_status=execution_status,
            final_workflow_status=final_status,
            summary=summary,
            obligation_results=results,
            blockers=env_blockers,
        )

    def _evaluate_obligation(
        self,
        obligation: AcceptanceObligation,
        *,
        execution: ExecutionResult,
        verification: VerificationResult | None,
        publish: PublishResult | None,
    ) -> VerificationObligationResult:
        artifact_ids = _evidence_artifact_ids(execution, verification, publish)
        env_blocker = _environment_blocker(execution, verification, publish, required_for=obligation.name)
        if env_blocker is not None and obligation.kind in {
            AcceptanceObligationKind.ENVIRONMENT_PREREQUISITES_SATISFIED,
            AcceptanceObligationKind.INTEGRATION_TESTS_RUN,
            AcceptanceObligationKind.INTEGRATION_TESTS_PASSED,
            AcceptanceObligationKind.RELEVANT_TESTS_RUN,
            AcceptanceObligationKind.RELEVANT_TESTS_PASSED,
        }:
            return VerificationObligationResult(
                obligation_id=obligation.id,
                obligation_name=obligation.name,
                kind=obligation.kind,
                status=AcceptanceObligationStatus.BLOCKED,
                reason=env_blocker.summary,
                evidence_artifact_ids=artifact_ids,
                blocker_kind=env_blocker.kind,
                environment_blocker=env_blocker,
            )

        if obligation.kind == AcceptanceObligationKind.CODE_CHANGED:
            changed = _has_mutation_evidence(execution)
            return _obligation_result(obligation, changed, "Mutation evidence found." if changed else "No changed-file or mutation summary evidence found.", artifact_ids)

        if obligation.kind == AcceptanceObligationKind.BUILD_OR_COMPILE_SUCCEEDED:
            ran, passed, failed = _check_status(execution, verification, publish, ("build", "compile", "cmake", "gradle", "mvn", "make"))
            return _check_result(obligation, ran, passed, failed, artifact_ids, "Build/compile")

        if obligation.kind == AcceptanceObligationKind.RELEVANT_TESTS_RUN:
            ran, _passed, _failed = _check_status(execution, verification, publish, ())
            return _run_result(obligation, ran, artifact_ids, "Relevant tests/checks")

        if obligation.kind == AcceptanceObligationKind.RELEVANT_TESTS_PASSED:
            ran, passed, failed = _check_status(execution, verification, publish, ())
            return _check_result(obligation, ran, passed, failed, artifact_ids, "Relevant tests/checks")

        if obligation.kind == AcceptanceObligationKind.INTEGRATION_TESTS_RUN:
            ran, _passed, _failed = _check_status(execution, verification, publish, ("integration", "e2e", "end-to-end", "freeplane", "grpc smoke"))
            return _run_result(obligation, ran, artifact_ids, "Integration tests")

        if obligation.kind == AcceptanceObligationKind.INTEGRATION_TESTS_PASSED:
            ran, passed, failed = _check_status(execution, verification, publish, ("integration", "e2e", "end-to-end", "freeplane", "grpc smoke"))
            return _check_result(obligation, ran, passed, failed, artifact_ids, "Integration tests")

        if obligation.kind == AcceptanceObligationKind.ENVIRONMENT_PREREQUISITES_SATISFIED:
            return _obligation_result(obligation, True, "No structured environment blocker was reported.", artifact_ids)

        if obligation.kind in {
            AcceptanceObligationKind.DOCUMENTATION_UPDATED,
            AcceptanceObligationKind.EXAMPLES_UPDATED,
            AcceptanceObligationKind.CI_OR_BUILD_UPDATED,
            AcceptanceObligationKind.CODEGEN_OR_TOOLING_UPDATED,
            AcceptanceObligationKind.WORK_SURFACE_COMPLETE,
        }:
            ok = _obligation_has_named_evidence(obligation, execution, verification, publish)
            return _obligation_result(obligation, ok, "Discovered work-surface evidence found." if ok else "Discovered work-surface obligation lacks explicit evidence.", artifact_ids)

        if obligation.kind == AcceptanceObligationKind.PUBLISH_OBLIGATIONS_SATISFIED:
            if verification is not None and (verification.commit_required or verification.push_required):
                ok = (not verification.commit_required or verification.commit_done) and (not verification.push_required or verification.push_done)
            else:
                ok = publish is not None and publish.ok
            return _obligation_result(obligation, ok, "Publish obligations satisfied." if ok else "Publish/commit/push obligations are missing or incomplete.", artifact_ids)

        ok = bool(verification and (verification.passed or verification.checks_passed)) or bool(execution.evidence_bundle and execution.evidence_bundle.ok)
        return _obligation_result(obligation, ok, "Required evidence exists." if ok else "Required evidence is missing.", artifact_ids)

    def evaluate_pipeline_reentry(
        self,
        *,
        source_stage: str,
        plan: ExecutionPlan | None = None,
        obligations: ObligationAnalysis | None = None,
        verification: VerificationResult | None = None,
        acceptance: AcceptanceDecision | None = None,
        publish: PublishResult | None = None,
        loop_decisions: list[PipelineLoopDecision | dict[str, object]] | None = None,
        budget: PipelineLoopBudget | None = None,
    ) -> PipelineLoopDecision:
        """Decide whether the workflow must re-enter an earlier pipeline stage.

        This is the pipeline-wide loop controller. Local repair is still handled
        by publish_review -> repair, but newly discovered work surfaces, missing
        discovery, missing context, or deeper CI/setup/doc obligations are routed
        back through observe/build_context/obligations/plan under explicit budget.
        """

        budget = budget or PipelineLoopBudget()
        prior = [_coerce_loop_decision(item) for item in (loop_decisions or [])]
        trigger, target, reason, missing_evidence, missing_obligations = _detect_reentry_trigger(
            source_stage=source_stage,
            plan=plan,
            obligations=obligations,
            verification=verification,
            acceptance=acceptance,
            publish=publish,
        )
        if trigger == PipelineLoopTriggerKind.NONE:
            return PipelineLoopDecision(
                source_stage=source_stage,
                target_stage=PipelineReentryTarget.CONTINUE,
                trigger_kind=trigger,
                reason="No pipeline-wide re-entry trigger detected.",
                automatic=False,
                allowed=True,
                loop_count=len(prior),
                global_limit=budget.global_limit,
                per_trigger_limit=budget.per_trigger_limit,
                per_source_stage_limit=budget.per_source_stage_limit,
            )

        trigger_count = sum(1 for item in prior if item.trigger_kind == trigger)
        source_count = sum(1 for item in prior if item.source_stage == source_stage)
        exhausted = len(prior) >= budget.global_limit or trigger_count >= budget.per_trigger_limit or source_count >= budget.per_source_stage_limit
        facts = self.lifecycle_facts(
            plan=plan,
            verification=verification,
            publish=publish,
            acceptance=acceptance,
            reentry_required=True,
            reentry_target_stage=target,
            reentry_trigger_kind=trigger,
            reentry_budget_exhausted=exhausted,
            pipeline_loop_count=len(prior),
            trigger_loop_count=trigger_count,
            source_stage_loop_count=source_count,
            pipeline_loop_global_limit=budget.global_limit,
            pipeline_loop_per_trigger_limit=budget.per_trigger_limit,
            pipeline_loop_per_source_stage_limit=budget.per_source_stage_limit,
        )
        policy = self.lifecycle_machine.policy_evaluator.evaluate("can_reenter", facts)
        allowed = policy.allowed and not exhausted
        return PipelineLoopDecision(
            source_stage=source_stage,
            target_stage=target if allowed else PipelineReentryTarget.FINALIZE,
            trigger_kind=trigger,
            reason=reason if allowed else f"Pipeline re-entry requested for {trigger.value}, but loop budget/policy denied it.",
            allowed=allowed,
            automatic=allowed,
            missing_evidence=missing_evidence,
            missing_obligations=missing_obligations,
            loop_count=len(prior),
            trigger_count=trigger_count,
            source_stage_count=source_count,
            global_limit=budget.global_limit,
            per_trigger_limit=budget.per_trigger_limit,
            per_source_stage_limit=budget.per_source_stage_limit,
            budget_exhausted=exhausted,
            policy_decision=policy,
        )

    def verification_strategy(
        self,
        *,
        plan: ExecutionPlan,
        execution: ExecutionResult,
        publish: PublishResult | None = None,
        per_check_routing_enabled: bool = False,
    ) -> VerificationStrategy:
        if not execution.ok:
            return VerificationStrategy(
                mode=VerificationMode.EVIDENCE_REVIEW,
                per_check=False,
                requires_world_check=False,
                reason="Execution failed or returned unusable evidence; controller will use evidence guard verification.",
            )
        if execution.evidence_bundle is not None and _non_deferred_blockers(execution.evidence_bundle.structured):
            env_blocker = _environment_blocker(execution, None, publish, required_for="verification")
            return VerificationStrategy(
                mode=VerificationMode.EVIDENCE_REVIEW,
                per_check=False,
                requires_world_check=False,
                reason=(
                    "Structured execution evidence contains an environment blocker; acceptance must classify needs_environment."
                    if env_blocker is not None
                    else "Structured execution evidence contains non-publish blockers; evidence review should classify missing/failed requirements before finalization."
                ),
            )
        verification_text = " ".join([*plan.verification_checks, *plan.required_test_levels, plan.execution_environment, *plan.required_setup_steps, *plan.environment_notes]).lower()
        requires_world_check = any(
            marker in verification_text
            for marker in ("world_check", "real_world", "postcheck_in_environment", "cluster live", "host live", "kubectl live", "ansible live", "ssh live", "integration", "e2e", "end-to-end", "freeplane", "grpc smoke")
        ) and publish is None
        if requires_world_check:
            return VerificationStrategy(
                mode=VerificationMode.WORLD_CHECK,
                per_check=False,
                requires_world_check=True,
                reason="Plan declares verification that requires runtime/world access after execution evidence.",
            )
        return VerificationStrategy(
            mode=VerificationMode.EVIDENCE_REVIEW,
            per_check=bool(per_check_routing_enabled and plan.verification_checks),
            requires_world_check=False,
            reason="Verification can be completed as Direct LLM evidence review over artifacts/context.",
        )



def _coerce_loop_decision(item: PipelineLoopDecision | dict[str, object]) -> PipelineLoopDecision:
    if isinstance(item, PipelineLoopDecision):
        return item
    return PipelineLoopDecision.model_validate(item)


def _detect_reentry_trigger(
    *,
    source_stage: str,
    plan: ExecutionPlan | None,
    obligations: ObligationAnalysis | None,
    verification: VerificationResult | None,
    acceptance: AcceptanceDecision | None,
    publish: PublishResult | None,
) -> tuple[PipelineLoopTriggerKind, PipelineReentryTarget, str, list[str], list[str]]:
    text_items: list[str] = []
    missing_evidence: list[str] = []
    missing_obligations: list[str] = []
    if verification is not None:
        missing_evidence.extend(verification.missing_evidence)
        missing_obligations.extend(verification.missing_obligations)
        missing_obligations.extend(verification.missing_test_levels)
        missing_obligations.extend(verification.missing_setup_steps)
        text_items.extend([verification.summary, *verification.missing_evidence, *verification.missing_obligations, *verification.missing_test_levels, *verification.missing_setup_steps, *verification.checks_failed])
    if acceptance is not None:
        for result in acceptance.obligation_results:
            if result.status != AcceptanceObligationStatus.PASSED:
                missing_obligations.append(result.obligation_name)
                text_items.append(f"{result.kind.value} {result.obligation_name} {result.reason}")
    if publish is not None:
        text_items.extend([publish.summary, publish.evidence_text, *[b.summary for b in publish.structured_evidence.blockers]])
    text = _lower_join(text_items)

    if not text.strip():
        return PipelineLoopTriggerKind.NONE, PipelineReentryTarget.CONTINUE, "No re-entry trigger text was present.", [], []
    if any(marker in text for marker in ("official docs", "current docs", "release notes", "api changed", "version mismatch", "unknown api")):
        return PipelineLoopTriggerKind.MISSING_RESEARCH_EVIDENCE, PipelineReentryTarget.RESEARCH, "New external/current documentation evidence is required before replanning.", missing_evidence, missing_obligations
    if any(marker in text for marker in ("repo observation", "inspect repository", "missing repository", "repo topology", "existing pattern unknown")):
        return PipelineLoopTriggerKind.MISSING_REPOSITORY_OBSERVATION, PipelineReentryTarget.OBSERVE, "Repository observation is missing or insufficient; re-enter observe.", missing_evidence, missing_obligations
    if any(marker in text for marker in ("context packet insufficient", "missing context", "context missing")):
        return PipelineLoopTriggerKind.MISSING_CONTEXT, PipelineReentryTarget.BUILD_CONTEXT, "ContextPacket is insufficient; rebuild context from artifacts.", missing_evidence, missing_obligations
    gap_markers = ("missing", "required", "requires", "need", "needs", "discovered", "impact", "gap", "incomplete", "omitted", "lacks", "not present", "not updated", "should update", "must update")
    docs_markers = ("documentation", "readme", "docs", "user-facing docs", "developer docs", "api docs", "migration notes")
    if any(marker in text for marker in docs_markers) and any(marker in text for marker in gap_markers):
        return PipelineLoopTriggerKind.DOCS_IMPACT_DISCOVERED, PipelineReentryTarget.OBLIGATIONS, "Documentation impact was discovered after initial planning; re-enter obligation discovery.", missing_evidence, missing_obligations
    example_markers = ("example", "sample", "snippet")
    if any(marker in text for marker in example_markers) and any(marker in text for marker in gap_markers):
        return PipelineLoopTriggerKind.EXAMPLES_IMPACT_DISCOVERED, PipelineReentryTarget.OBLIGATIONS, "Examples/snippets impact was discovered after initial planning; re-enter obligation discovery.", missing_evidence, missing_obligations
    ci_markers = ("ci config", "gitlab ci", "workflow file", "pipeline", "build script", "github actions")
    ci_gap_markers = ("missing", "need", "needs", "required", "update", "add", "configure", "not configured", "deeper")
    if any(marker in text for marker in ci_markers) and any(marker in text for marker in ci_gap_markers):
        return PipelineLoopTriggerKind.CI_BUILD_IMPACT_DISCOVERED, PipelineReentryTarget.OBLIGATIONS, "CI/build pipeline impact requires rediscovery and replanning.", missing_evidence, missing_obligations
    codegen_markers = ("codegen", "generated code", "proto generation", "protoc", "tooling")
    if any(marker in text for marker in codegen_markers) and any(marker in text for marker in gap_markers):
        return PipelineLoopTriggerKind.CODEGEN_BUILD_IMPACT_DISCOVERED, PipelineReentryTarget.OBLIGATIONS, "Codegen/tooling/build impact requires rediscovery and replanning.", missing_evidence, missing_obligations
    if any(marker in text for marker in ("new integration surface", "integration scope", "binding surface", "client surface", "public api surface")):
        return PipelineLoopTriggerKind.INTEGRATION_SCOPE_DISCOVERED, PipelineReentryTarget.OBLIGATIONS, "A broader integration/work surface was discovered; re-enter obligation discovery.", missing_evidence, missing_obligations
    if any(marker in text for marker in ("setup gap", "missing setup step", "missing dependency install", "test harness setup")):
        return PipelineLoopTriggerKind.SETUP_GAP_DISCOVERED, PipelineReentryTarget.OBLIGATIONS, "A setup/test-harness gap was discovered; re-enter obligation discovery.", missing_evidence, missing_obligations
    if any(marker in text for marker in ("plan incomplete", "planner missed", "missing plan step", "scope deeper than repair")):
        return PipelineLoopTriggerKind.PLAN_INCOMPLETE, PipelineReentryTarget.PLAN, "The current plan is incomplete; re-enter planning.", missing_evidence, missing_obligations
    if source_stage == "publish_review" and any(marker in text for marker in ("deeper", "not just repair", "missing setup", "missing ci", "missing docs", "missing codegen")):
        return PipelineLoopTriggerKind.PUBLISH_DEEPER_PLANNING_REQUIRED, PipelineReentryTarget.OBLIGATIONS, "Publish/check evidence revealed deeper missing work; re-enter discovery instead of local repair.", missing_evidence, missing_obligations
    return PipelineLoopTriggerKind.NONE, PipelineReentryTarget.CONTINUE, "No pipeline-wide re-entry trigger detected.", missing_evidence, missing_obligations


_ALLOWED_INTENTS = {"implement", "modify", "investigate", "document", "verify"}


def _effective_task_intent(classification: TaskClassification) -> str:
    intent = (classification.task_intent or "").strip().lower()
    return intent if intent in _ALLOWED_INTENTS else "investigate"


def _plan_intent_mismatch(classification: TaskClassification, plan: ExecutionPlan) -> str | None:
    expected = _effective_task_intent(classification)
    raw_actual = (plan.task_intent or "").strip().lower()
    raw_deliverable = (plan.deliverable_kind or "").strip().lower()
    text = " ".join([plan.summary, *plan.steps, *plan.success_criteria]).lower()
    implementation_markers = ("implement", "add", "modify", "edit", "write code", "create", "update build", "run test", "compile", "fix")
    has_implementation_markers = any(marker in text for marker in implementation_markers)
    actual = raw_actual if raw_actual in _ALLOWED_INTENTS else ""
    if actual in {"", "investigate"} and (plan.requires_mutation or plan.must_change_world or has_implementation_markers):
        actual = "implement"
    deliverable = raw_deliverable
    if deliverable in {"", "analysis"} and (plan.requires_mutation or plan.must_change_world or has_implementation_markers):
        deliverable = "repository_changes" if classification.execution_family.value == "repository_change" else "changes"
    if expected in {"implement", "modify"}:
        if actual not in {"implement", "modify"}:
            return f"Planner degraded a {expected} task into {actual or 'unknown'} intent."
        if deliverable in {"analysis", "documentation"}:
            return f"Planner produced {deliverable} deliverable for a {expected} task instead of real changes."
        if not plan.requires_mutation and not plan.must_change_world and not has_implementation_markers:
            return f"Planner marked a {expected} task as non-mutating, which conflicts with the requested outcome."
        analysis_markers = ("analyze", "design", "document", "outline", "instructions", "review", "draft plan")
        has_analysis = any(marker in text for marker in analysis_markers)
        if has_analysis and not has_implementation_markers:
            return "Planner produced an analysis-only plan for an implementation task."
    return None


def _publish_required(plan: ExecutionPlan) -> bool:
    return bool(plan.require_commit or plan.require_push or Capability.REPO_CREATE_PR in plan.capabilities or plan.publication_steps)


def _unique_str(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out


def _lower_join(items: list[str]) -> str:
    return " ".join(str(item).lower() for item in items if str(item).strip())


def _contract_obligation_by_id(contract: TaskAcceptanceContract, obligation_id: str) -> AcceptanceObligation:
    for obligation in contract.obligations:
        if obligation.id == obligation_id:
            return obligation
    return AcceptanceObligation(kind=AcceptanceObligationKind.REQUIRED_EVIDENCE_PRESENT, name=obligation_id)


def _derive_execution_status(execution: ExecutionResult) -> ExecutionStatus:
    if execution.execution_status != ExecutionStatus.SUCCEEDED:
        return execution.execution_status
    if not execution.ok:
        return ExecutionStatus.FAILED if execution.transport_error else ExecutionStatus.BLOCKED
    blockers = _non_deferred_blockers(execution.structured_evidence)
    if blockers:
        return ExecutionStatus.PARTIAL if _has_mutation_evidence(execution) else ExecutionStatus.BLOCKED
    return ExecutionStatus.SUCCEEDED


def _has_mutation_evidence(execution: ExecutionResult) -> bool:
    evidence = execution.structured_evidence
    return bool(evidence.mutation_summary.changed or evidence.files_changed)


def _all_structured_blockers(execution: ExecutionResult | None, verification: VerificationResult | None, publish: PublishResult | None) -> list[tuple[str, object]]:
    blockers: list[tuple[str, object]] = []
    for source_name, result in (("execution", execution), ("verification", verification), ("publish", publish)):
        if result is None:
            continue
        evidence = getattr(result, "structured_evidence", None)
        if evidence is not None:
            blockers.extend((source_name, blocker) for blocker in evidence.blockers)
    return blockers


def _environment_blocker(execution: ExecutionResult | None, verification: VerificationResult | None, publish: PublishResult | None, *, required_for: str) -> EnvironmentBlocker | None:
    for source_name, blocker in _all_structured_blockers(execution, verification, publish):
        kind = getattr(blocker, "blocker_kind", BlockerKind.GENERIC)
        summary = getattr(blocker, "summary", "")
        if kind in {BlockerKind.MISSING_ENVIRONMENT_DEPENDENCY, BlockerKind.MISSING_RUNTIME_PREREQUISITE, BlockerKind.INTEGRATION_ENVIRONMENT_UNAVAILABLE}:
            return EnvironmentBlocker(
                kind=kind,
                summary=f"{source_name}: {summary}",
                missing_dependency=_guess_missing_dependency(summary),
                required_for=required_for,
                evidence_artifact_ids=list(getattr(blocker, "artifact_ids", []) or []),
            )
        lowered = str(summary).lower()
        if any(marker in lowered for marker in ("freeplane", "x11", "display", "not installed", "not found", "missing dependency", "environment unavailable")):
            inferred = BlockerKind.INTEGRATION_ENVIRONMENT_UNAVAILABLE if any(marker in lowered for marker in ("freeplane", "x11", "display", "integration")) else BlockerKind.MISSING_ENVIRONMENT_DEPENDENCY
            return EnvironmentBlocker(
                kind=inferred,
                summary=f"{source_name}: {summary}",
                missing_dependency=_guess_missing_dependency(summary),
                required_for=required_for,
                evidence_artifact_ids=list(getattr(blocker, "artifact_ids", []) or []),
            )
    if verification is not None:
        inferred_items = [
            *verification.missing_setup_steps,
            *verification.missing_test_levels,
            *verification.missing_obligations,
            verification.summary,
            *verification.missing_evidence,
            *verification.checks_failed,
        ]
        inferred_text = " | ".join(str(item) for item in inferred_items if item).lower()
        if any(marker in inferred_text for marker in ("install", "dependency", "bootstrap", "runtime prerequisite", "not installed", "docker", "freeplane", "x11", "display", "integration")):
            blocker_kind = BlockerKind.INTEGRATION_ENVIRONMENT_UNAVAILABLE if any(marker in inferred_text for marker in ("freeplane", "x11", "display", "integration")) else BlockerKind.MISSING_ENVIRONMENT_DEPENDENCY
            return EnvironmentBlocker(
                kind=blocker_kind,
                summary=f"verification: {verification.summary or 'missing environment/runtime prerequisite evidence'}",
                missing_dependency=_guess_missing_dependency(inferred_text),
                required_for=required_for,
                evidence_artifact_ids=_evidence_artifact_ids(execution, verification, publish),
            )
    return None


def _guess_missing_dependency(summary: str) -> str | None:
    lowered = summary.lower()
    for name in ("freeplane", "xvfb", "x11", "docker", "kubectl", "helm", "argocd"):
        if name in lowered:
            return name
    return None


def _evidence_artifact_ids(execution: ExecutionResult | None, verification: VerificationResult | None, publish: PublishResult | None) -> list[str]:
    ids: list[str] = []
    for result in (execution, verification, publish):
        if result is None:
            continue
        ids.extend(getattr(result, "primary_evidence_artifact_ids", []) or [])
        raw = getattr(result, "raw_evidence_artifact_id", None)
        if raw:
            ids.append(raw)
        bundle = getattr(result, "evidence_bundle", None)
        if bundle is not None:
            ids.extend(bundle.artifact_ids)
    return _unique_str(ids)


def _check_status(execution: ExecutionResult | None, verification: VerificationResult | None, publish: PublishResult | None, terms: tuple[str, ...]) -> tuple[bool, bool, bool]:
    observed: list[tuple[str, str]] = []
    for result in (execution, verification, publish):
        if result is None:
            continue
        evidence = getattr(result, "structured_evidence", None)
        if evidence is not None:
            for test in evidence.tests:
                observed.append((f"{test.name} {test.output_excerpt or ''}".lower(), str(test.status).lower()))
        if verification is not None and result is verification:
            for name in verification.checks_passed:
                observed.append((str(name).lower(), "passed"))
            for name in verification.checks_failed:
                observed.append((str(name).lower(), "failed"))
            for name in verification.missing_evidence:
                observed.append((str(name).lower(), "missing"))
    if terms:
        observed = [(name, status) for name, status in observed if any(term in name for term in terms)]
    run_statuses = {"passed", "failed", "blocked", "success", "succeeded", "ok", "error"}
    missing_statuses = {"missing", "not_run", "not run", "unknown"}
    ran = any(status in run_statuses for _name, status in observed)
    failed = any(status in {"failed", "blocked", "error"} for _name, status in observed) or (not ran and any(status in missing_statuses for _name, status in observed))
    passed = ran and not failed and any(status in {"passed", "success", "succeeded", "ok"} for _name, status in observed)
    return ran, passed, failed


def _obligation_result(obligation: AcceptanceObligation, ok: bool, reason: str, artifact_ids: list[str]) -> VerificationObligationResult:
    return VerificationObligationResult(
        obligation_id=obligation.id,
        obligation_name=obligation.name,
        kind=obligation.kind,
        status=AcceptanceObligationStatus.PASSED if ok else AcceptanceObligationStatus.FAILED,
        reason=reason,
        evidence_artifact_ids=artifact_ids,
        blocker_kind=None if ok else BlockerKind.MISSING_EVIDENCE,
    )


def _run_result(obligation: AcceptanceObligation, ran: bool, artifact_ids: list[str], label: str) -> VerificationObligationResult:
    return VerificationObligationResult(
        obligation_id=obligation.id,
        obligation_name=obligation.name,
        kind=obligation.kind,
        status=AcceptanceObligationStatus.PASSED if ran else AcceptanceObligationStatus.NOT_RUN,
        reason=f"{label} were run." if ran else f"{label} were required but no run evidence was found.",
        evidence_artifact_ids=artifact_ids,
        blocker_kind=None if ran else BlockerKind.MISSING_EVIDENCE,
    )


def _check_result(obligation: AcceptanceObligation, ran: bool, passed: bool, failed: bool, artifact_ids: list[str], label: str) -> VerificationObligationResult:
    if not ran:
        return VerificationObligationResult(
            obligation_id=obligation.id,
            obligation_name=obligation.name,
            kind=obligation.kind,
            status=AcceptanceObligationStatus.NOT_RUN,
            reason=f"{label} were required but no run evidence was found.",
            evidence_artifact_ids=artifact_ids,
            blocker_kind=BlockerKind.MISSING_EVIDENCE,
        )
    if failed:
        return VerificationObligationResult(
            obligation_id=obligation.id,
            obligation_name=obligation.name,
            kind=obligation.kind,
            status=AcceptanceObligationStatus.FAILED,
            reason=f"{label} ran but failed or missing evidence was reported.",
            evidence_artifact_ids=artifact_ids,
            blocker_kind=BlockerKind.TEST_FAILURE,
        )
    return VerificationObligationResult(
        obligation_id=obligation.id,
        obligation_name=obligation.name,
        kind=obligation.kind,
        status=AcceptanceObligationStatus.PASSED if passed else AcceptanceObligationStatus.NOT_RUN,
        reason=f"{label} passed." if passed else f"{label} had run evidence but no passing result was found.",
        evidence_artifact_ids=artifact_ids,
        blocker_kind=None if passed else BlockerKind.MISSING_EVIDENCE,
    )



def _obligation_has_named_evidence(obligation: AcceptanceObligation, execution: ExecutionResult | None, verification: VerificationResult | None, publish: PublishResult | None) -> bool:
    terms = [str(item).lower() for item in obligation.checks if str(item).strip()]
    if not terms:
        terms = [obligation.name.lower(), obligation.kind.value.replace("_", " ")]
    haystack = _result_text(execution).lower() + "\n" + _result_text(verification).lower() + "\n" + _result_text(publish).lower()
    return any(term and term in haystack for term in terms)


def _publish_failed_checks(publish: PublishResult | None) -> bool:
    if publish is None:
        return False
    evidence = publish.structured_evidence
    for test in evidence.tests:
        status = str(test.status).lower()
        if status in {"failed", "error", "blocked"}:
            return True
        if test.passed is False:
            return True
    text = _result_text(publish).lower()
    return any(marker in text for marker in ("pr checks failed", "checks failed", "check failed", "github actions failed", "ci failed", "job failed", "workflow failed", "red"))


def _publish_forbidden_action_detected(publish: PublishResult | None) -> bool:
    if publish is None:
        return False
    evidence = publish.structured_evidence
    text = _result_text(publish).lower()
    if any(marker in text for marker in ("applied fix", "fixed ci", "fixed failing", "patched", "reimplemented", "modified source", "changed src", "updated src", "edited src")):
        return True
    source_like_changes = [
        item.path
        for item in evidence.files_changed
        if str(item.path).startswith(("src/", "lib/", "cmd/", "pkg/", "tests/"))
        or str(item.path).endswith((".py", ".go", ".rs", ".ts", ".js", ".tsx", ".jsx", ".cc", ".cpp", ".h", ".hpp", ".java", ".kt", ".proto", ".sh", ".yaml", ".yml", ".toml"))
    ]
    if source_like_changes:
        repair_words = ("fix", "patch", "modify", "change", "edit", "reimplement", "applied")
        return any(word in text for word in repair_words)
    return False


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _contains_positive_side_effect(text: str, *, positive: tuple[str, ...], negative: tuple[str, ...] = ()) -> bool:
    """Detect explicit side-effect claims while ignoring common negated reports."""

    lowered = text.lower()
    lines = [line.strip() for line in lowered.splitlines() if line.strip()] or [lowered]
    for line in lines:
        if negative and any(marker in line for marker in negative):
            continue
        if any(marker in line for marker in positive):
            return True
    return False


def _command_lines(result: object | None) -> list[str]:
    """Return actual shell commands reported by the producer evidence.

    Lifecycle side-effect detection must never scan prompts, acceptance
    contracts, summaries, missing-evidence prose, or command output. Otherwise a
    sentence like "push is forbidden" or "changes were not pushed" can be
    misclassified as a real push. Only explicit commands from commands_run are
    eligible for non-publish git/PR violations.
    """

    evidence = getattr(result, "structured_evidence", None) if result is not None else None
    if evidence is None:
        return []
    lines: list[str] = []
    for item in getattr(evidence, "commands_run", []) or []:
        command = str(getattr(item, "command", "") or "").strip()
        if command:
            lines.append(command)
    return lines


def _command_text(result: object | None) -> str:
    return "\n".join(_command_lines(result)).lower()


def _execute_pr_created(result: object | None) -> bool:
    command_text = _command_text(result)
    command_patterns = (
        r"(^|[;&|()]|\s)gh\s+pr\s+create\b",
        r"(^|[;&|()]|\s)hub\s+pull-request\b",
        r"(^|[;&|()]|\s)curl\b[^\n;&|]*api\.github\.com[^\n;&|]*/pulls\b",
    )
    return any(re.search(pattern, command_text) for pattern in command_patterns)


def _execute_git_push(result: object | None) -> bool:
    return bool(re.search(r"(^|[;&|()]|\s)git\s+push\b", _command_text(result)))


def _execute_git_commit(result: object | None) -> bool:
    return bool(re.search(r"(^|[;&|()]|\s)git\s+commit\b", _command_text(result)))


def _deferred_publish_blocker(summary: str) -> bool:
    text = summary.lower()
    if not text.strip():
        return False
    publish_terms = (
        "commit",
        "committed",
        "push",
        "pushed",
        "pull request",
        " pr",
        "pr ",
        "create_pr",
        "open_pull_request",
        "wait_pr_checks",
    )
    deferral_terms = ("forbidden", "deferred", "publish", "publisher", "not been", "not run yet", "has not", "missing evidence")
    return any(term in text for term in publish_terms) and any(term in text for term in deferral_terms)


def _non_deferred_blockers(evidence: object | None) -> list[object]:
    if evidence is None:
        return []
    blockers: list[object] = []
    for item in getattr(evidence, "blockers", []) or []:
        summary = str(getattr(item, "summary", "") or "")
        if _deferred_publish_blocker(summary):
            continue
        blockers.append(item)
    return blockers


def _result_text(result: object | None) -> str:
    if result is None:
        return ""
    parts = [str(getattr(result, "summary", "") or ""), str(getattr(result, "evidence_text", "") or "")]
    evidence = getattr(result, "structured_evidence", None)
    if evidence is not None:
        for item in getattr(evidence, "commands_run", []) or []:
            parts.append(str(getattr(item, "command", "") or ""))
            parts.append(str(getattr(item, "output_excerpt", "") or ""))
        for item in getattr(evidence, "blockers", []) or []:
            parts.append(str(getattr(item, "summary", "") or ""))
        for item in getattr(evidence, "files_changed", []) or []:
            parts.append(str(getattr(item, "summary", "") or ""))
            parts.append(str(getattr(item, "path", "") or ""))
            parts.append(str(getattr(item, "action", "") or ""))
        for item in getattr(evidence, "files_observed", []) or []:
            parts.append(str(getattr(item, "summary", "") or ""))
            parts.append(str(getattr(item, "path", "") or ""))
        for item in getattr(evidence, "extracted_facts", []) or []:
            parts.append(str(getattr(item, "subject", "") or ""))
            parts.append(str(getattr(item, "fact", "") or ""))
        for item in getattr(evidence, "diffs", []) or []:
            parts.append(str(getattr(item, "summary", "") or ""))
            parts.append(str(getattr(item, "path", "") or ""))
        for item in getattr(evidence, "tests", []) or []:
            parts.append(str(getattr(item, "name", "") or ""))
            parts.append(str(getattr(item, "command", "") or ""))
            parts.append(str(getattr(item, "status", "") or ""))
            parts.append(str(getattr(item, "output_excerpt", "") or ""))
        mutation = getattr(evidence, "mutation_summary", None)
        if mutation is not None:
            parts.append(str(getattr(mutation, "summary", "") or ""))
            parts.extend(str(path) for path in (getattr(mutation, "files_changed", []) or []))
        postcheck = getattr(evidence, "postcheck_summary", None)
        if postcheck is not None:
            parts.append(str(getattr(postcheck, "summary", "") or ""))
    return "\n".join(part for part in parts if part)


def _requires_integration_verification(plan: ExecutionPlan | None) -> bool:
    if plan is None:
        return False
    text = _lower_join([*plan.required_test_levels, *plan.verification_checks, *plan.success_criteria, *plan.required_setup_steps, *plan.environment_notes])
    return any(marker in text for marker in ("integration", "e2e", "end-to-end", "freeplane", "grpc smoke", "x11", "display"))


def _verification_terms(plan: ExecutionPlan | None) -> tuple[str, ...]:
    if _requires_integration_verification(plan):
        return ("integration", "e2e", "end-to-end", "freeplane", "grpc smoke", "x11", "display")
    return ()


def _acceptance_has_only_publish_obligation_missing(acceptance: AcceptanceDecision) -> bool:
    blocking = [item for item in acceptance.obligation_results if item.status != AcceptanceObligationStatus.PASSED]
    return bool(blocking) and all(item.kind == AcceptanceObligationKind.PUBLISH_OBLIGATIONS_SATISFIED for item in blocking)

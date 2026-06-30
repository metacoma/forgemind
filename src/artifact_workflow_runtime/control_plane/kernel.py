from __future__ import annotations

from dataclasses import dataclass

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
    PolicyViolation,
)
from artifact_workflow_runtime.policy import PolicyEngine
from artifact_workflow_runtime.policy.evidence import EvidenceGate


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
        return "execute"

    @staticmethod
    def next_after_approval(approval: ApprovalRequest | dict[str, object] | None) -> str:
        if approval is None:
            return "finalize"
        approved = approval.approved if isinstance(approval, ApprovalRequest) else approval.get("approved")
        return "execute" if approved else "finalize"

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

    def lifecycle_facts(
        self,
        *,
        plan: ExecutionPlan | None = None,
        acceptance_contract: TaskAcceptanceContract | None = None,
        execution: ExecutionResult | None = None,
        verification: VerificationResult | None = None,
        publish: PublishResult | None = None,
        acceptance: AcceptanceDecision | None = None,
    ) -> LifecycleFacts:
        publish_required = _publish_required(plan) if plan is not None else False
        mutation_task = bool(plan and (plan.requires_mutation or plan.must_change_world or plan.expected_repo_changes))
        mandatory_verification_required = mutation_task or _requires_integration_verification(plan)
        environment_blocked = _environment_blocker(execution, verification, publish, required_for="lifecycle") is not None
        verification_ran, verification_passed, verification_failed = _check_status(execution, verification, publish, _verification_terms(plan))
        mandatory_verification_satisfied = bool(verification_passed and not verification_failed) if mandatory_verification_required else True
        if acceptance is not None and _acceptance_has_only_publish_obligation_missing(acceptance):
            mandatory_verification_satisfied = True
        text = _result_text(execution).lower() if execution is not None else ""
        execute_pr_created = _contains_any(text, ("pull request", "pr #", "created pr", "opened pr", "github.com/", "/pull/"))
        execute_git_push = _contains_any(text, ("git push", "pushed branch", "pushed to", "origin/"))
        execute_git_commit = _contains_any(text, ("git commit", "commit ", "committed"))
        execution_status = _derive_execution_status(execution) if execution is not None else ExecutionStatus.FAILED
        execution_has_blockers = bool(execution and execution.structured_evidence.blockers)
        execution_succeeded = bool(execution and execution.ok and execution_status == ExecutionStatus.SUCCEEDED and not execution_has_blockers)
        return LifecycleFacts(
            plan=plan,
            acceptance_contract=acceptance_contract,
            execution=execution,
            verification=verification,
            publish=publish,
            acceptance=acceptance,
            publish_required=publish_required,
            publish_done=publish is not None and publish.ok,
            mutation_task=mutation_task,
            mandatory_verification_required=mandatory_verification_required,
            mandatory_verification_satisfied=mandatory_verification_satisfied,
            mandatory_verification_blocked=environment_blocked,
            mandatory_verification_missing=mandatory_verification_required and not verification_ran,
            environment_blocked=environment_blocked,
            execution_succeeded=execution_succeeded,
            execution_blocked=execution_status in {ExecutionStatus.BLOCKED, ExecutionStatus.FAILED},
            execution_has_blockers=execution_has_blockers,
            execute_pr_created=execute_pr_created,
            execute_git_push=execute_git_push,
            execute_git_commit=execute_git_commit,
            execute_forbidden_action_detected=execute_pr_created or execute_git_push or execute_git_commit,
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
        return AcceptanceDecision(
            contract_id=contract.id,
            status=AcceptanceStatus.NEEDS_HUMAN_REVIEW,
            accepted=False,
            execution_status=_derive_execution_status(execution) if execution is not None else ExecutionStatus.FAILED,
            final_workflow_status="control_plane_violation",
            summary=decision.reason,
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

        if obligation.kind == AcceptanceObligationKind.PUBLISH_OBLIGATIONS_SATISFIED:
            if verification is not None and (verification.commit_required or verification.push_required):
                ok = (not verification.commit_required or verification.commit_done) and (not verification.push_required or verification.push_done)
            else:
                ok = publish is not None and publish.ok
            return _obligation_result(obligation, ok, "Publish obligations satisfied." if ok else "Publish/commit/push obligations are missing or incomplete.", artifact_ids)

        ok = bool(verification and (verification.passed or verification.checks_passed)) or bool(execution.evidence_bundle and execution.evidence_bundle.ok)
        return _obligation_result(obligation, ok, "Required evidence exists." if ok else "Required evidence is missing.", artifact_ids)

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
        if execution.evidence_bundle is not None and execution.evidence_bundle.structured.blockers:
            env_blocker = _environment_blocker(execution, None, publish, required_for="verification")
            return VerificationStrategy(
                mode=VerificationMode.EVIDENCE_REVIEW,
                per_check=False,
                requires_world_check=False,
                reason=(
                    "Structured execution evidence contains an environment blocker; acceptance must classify needs_environment."
                    if env_blocker is not None
                    else "Structured execution evidence contains blockers; evidence review should classify missing/failed requirements before finalization."
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
    blockers = execution.structured_evidence.blockers
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


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _result_text(result: object | None) -> str:
    if result is None:
        return ""
    parts = [str(getattr(result, "summary", "") or ""), str(getattr(result, "evidence_text", "") or "")]
    evidence = getattr(result, "structured_evidence", None)
    if evidence is not None:
        parts.extend(item.summary for item in evidence.blockers)
        parts.extend((item.summary or item.path) for item in evidence.files_changed)
        parts.extend(item.name for item in evidence.tests)
    return "\n".join(parts)


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

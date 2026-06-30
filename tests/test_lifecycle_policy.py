from __future__ import annotations

import pytest

from artifact_workflow_runtime.control_plane import RuntimeKernel
from artifact_workflow_runtime.evidence import EvidenceExtractor
from artifact_workflow_runtime.lifecycle import LifecycleEvent, LifecycleMachine, LifecycleStage
from artifact_workflow_runtime.models import (
    AcceptanceDecision,
    AcceptanceObligation,
    AcceptanceObligationKind,
    AcceptanceObligationStatus,
    AcceptanceStatus,
    Capability,
    ExecutionFamily,
    ExecutionPlan,
    ExecutionResult,
    ExecutionStatus,
    PublishResult,
    TaskAcceptanceContract,
    VerificationObligationResult,
)


def _plan(*, publish: bool = True, integration: bool = True) -> ExecutionPlan:
    checks = ["run unit tests"]
    if integration:
        checks.append("run integration tests with Freeplane")
    return ExecutionPlan(
        summary="Add repository client",
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        task_intent="implement",
        deliverable_kind="repository_changes",
        capabilities=[Capability.REPO_WRITE, Capability.REPO_CREATE_PR] if publish else [Capability.REPO_WRITE],
        steps=["edit files", "run tests"],
        success_criteria=["client implemented", *checks],
        verification_checks=checks,
        requires_mutation=True,
        must_change_world=True,
        expected_repo_changes=["client files"],
        reasoning="Repository mutation must be verified before publish.",
    )


def _contract(plan: ExecutionPlan) -> TaskAcceptanceContract:
    return TaskAcceptanceContract(
        task_id="task_1",
        execution_family=plan.execution_family,
        requires_mutation=True,
        obligations=[
            AcceptanceObligation(kind=AcceptanceObligationKind.CODE_CHANGED, name="Code changed"),
            AcceptanceObligation(kind=AcceptanceObligationKind.RELEVANT_TESTS_RUN, name="Relevant tests run"),
            AcceptanceObligation(kind=AcceptanceObligationKind.RELEVANT_TESTS_PASSED, name="Relevant tests passed"),
            AcceptanceObligation(kind=AcceptanceObligationKind.INTEGRATION_TESTS_RUN, name="Integration tests run"),
            AcceptanceObligation(kind=AcceptanceObligationKind.INTEGRATION_TESTS_PASSED, name="Integration tests passed"),
            AcceptanceObligation(kind=AcceptanceObligationKind.PUBLISH_OBLIGATIONS_SATISFIED, name="Publish done"),
        ],
    )


def _execution(text: str, *, status: ExecutionStatus = ExecutionStatus.SUCCEEDED) -> ExecutionResult:
    structured = EvidenceExtractor().from_agent_output(text, changed_default=False)
    return ExecutionResult(
        request_id="exec_req_1",
        ok=True,
        execution_status=status,
        summary=text,
        evidence_text=text,
        structured_evidence=structured,
    )


def _publish(text: str) -> PublishResult:
    structured = EvidenceExtractor().from_agent_output(text, changed_default=False)
    return PublishResult(
        request_id="publish_req_1",
        ok=True,
        summary=text,
        evidence_text=text,
        structured_evidence=structured,
    )


def test_lifecycle_denies_execute_that_created_pr() -> None:
    kernel = RuntimeKernel()
    plan = _plan(publish=True)
    execution = _execution("Changed src/client.cc. Ran unit tests: passed. Created PR #42 before publish.")

    decision = kernel.review_execution(plan=plan, execution=execution, acceptance_contract=_contract(plan))

    assert decision.event == LifecycleEvent.EXECUTION_FINISHED
    assert decision.allowed is False
    assert decision.to_stage == LifecycleStage.CONTROL_PLANE_VIOLATION
    assert decision.graph_next == "finalize"
    assert {violation.code for violation in decision.violations} == {"execute_created_pr", "execute_forbidden_action"}


def test_lifecycle_routes_mandatory_integration_to_verify_before_publish() -> None:
    kernel = RuntimeKernel()
    plan = _plan(publish=True, integration=True)
    execution = _execution("Changed src/client.cc. Ran unit tests: passed. Integration tests were not run yet.")

    decision = kernel.review_execution(plan=plan, execution=execution, acceptance_contract=_contract(plan))

    assert decision.allowed is True
    assert decision.to_stage == LifecycleStage.VERIFYING
    assert decision.graph_next == "verify"
    assert "mandatory verification" in decision.reason.lower()


def test_lifecycle_allows_publish_after_only_publish_obligation_remains() -> None:
    kernel = RuntimeKernel()
    plan = _plan(publish=True, integration=False)
    execution = _execution("Changed src/client.cc. Ran unit tests: passed.")
    contract = _contract(plan)
    publish_obligation = next(item for item in contract.obligations if item.kind == AcceptanceObligationKind.PUBLISH_OBLIGATIONS_SATISFIED)
    passed_results = [
        VerificationObligationResult(
            obligation_id=item.id,
            obligation_name=item.name,
            kind=item.kind,
            status=AcceptanceObligationStatus.PASSED,
            reason="Satisfied before publish.",
        )
        for item in contract.obligations
        if item.kind != AcceptanceObligationKind.PUBLISH_OBLIGATIONS_SATISFIED
    ]
    passed_results.append(
        VerificationObligationResult(
            obligation_id=publish_obligation.id,
            obligation_name=publish_obligation.name,
            kind=publish_obligation.kind,
            status=AcceptanceObligationStatus.NOT_RUN,
            reason="Publish has not run yet.",
        )
    )
    acceptance = AcceptanceDecision(
        contract_id=contract.id,
        status=AcceptanceStatus.NEEDS_HUMAN_REVIEW,
        accepted=False,
        execution_status=ExecutionStatus.SUCCEEDED,
        final_workflow_status="needs_human_review",
        summary="Only publish obligation remains.",
        obligation_results=passed_results,
    )

    decision = kernel.next_after_acceptance(
        plan=plan,
        acceptance=acceptance,
        execution=execution,
        verification=None,
        publish=None,
        acceptance_contract=contract,
    )

    assert decision.allowed is True
    assert decision.to_stage == LifecycleStage.READY_TO_PUBLISH
    assert decision.graph_next == "publish"


def test_lifecycle_machine_fallback_is_strict_without_external_statemachine() -> None:
    machine = LifecycleMachine()
    plan = _plan(publish=True)
    execution = _execution("Changed src/client.cc. git push origin feature-branch")
    facts = RuntimeKernel().lifecycle_facts(plan=plan, acceptance_contract=_contract(plan), execution=execution)

    decision = machine.transition(from_stage=LifecycleStage.EXECUTING, event=LifecycleEvent.EXECUTION_FINISHED, facts=facts)

    assert decision.allowed is False
    assert "execute_pushed_git" in {violation.code for violation in decision.violations}



def test_lifecycle_routes_failed_publish_checks_to_repair() -> None:
    kernel = RuntimeKernel()
    plan = _plan(publish=True, integration=False)
    execution = _execution("Changed src/client.cc. Ran unit tests: passed.")
    publish = _publish("Created PR #42 and waited for PR checks. PR checks failed: ci/test failed.")

    decision = kernel.review_publish(
        plan=plan,
        execution=execution,
        publish=publish,
        acceptance_contract=_contract(plan),
        repair_attempt_count=0,
        max_repair_attempts=2,
    )

    assert decision.allowed is True
    assert decision.to_stage == LifecycleStage.REPAIRING
    assert decision.graph_next == "repair"


def test_lifecycle_denies_repair_after_attempt_budget() -> None:
    kernel = RuntimeKernel()
    plan = _plan(publish=True, integration=False)
    execution = _execution("Changed src/client.cc. Ran unit tests: passed.")
    publish = _publish("Created PR #42 and waited for PR checks. PR checks failed: ci/test failed.")

    decision = kernel.review_publish(
        plan=plan,
        execution=execution,
        publish=publish,
        acceptance_contract=_contract(plan),
        repair_attempt_count=2,
        max_repair_attempts=2,
    )

    assert decision.allowed is False
    assert decision.graph_next == "verify"
    assert "repair_attempt_limit_reached" in {violation.code for violation in decision.violations}


def test_lifecycle_denies_publisher_that_repairs_ci_inside_publish() -> None:
    kernel = RuntimeKernel()
    plan = _plan(publish=True, integration=False)
    execution = _execution("Changed src/client.cc. Ran unit tests: passed.")
    publish = _publish("Created PR #42. Applied fix in src/client.cc to fix CI and pushed follow-up commit.")

    decision = kernel.review_publish(
        plan=plan,
        execution=execution,
        publish=publish,
        acceptance_contract=_contract(plan),
        repair_attempt_count=0,
        max_repair_attempts=2,
    )

    assert decision.allowed is False
    assert decision.to_stage == LifecycleStage.CONTROL_PLANE_VIOLATION
    assert "publisher_repaired_or_reimplemented" in {violation.code for violation in decision.violations}


def test_lifecycle_machine_does_not_inherit_external_statemachine_runtime() -> None:
    """Regression for real python-statemachine installations.

    The lifecycle engine is programmatic and policy-backed. If it subclasses
    statemachine.StateMachine without declarative class-level states, controller
    construction fails with InvalidDefinition("There are no states or transitions").
    """
    assert all("statemachine" not in getattr(base, "__module__", "") for base in LifecycleMachine.__mro__[1:])
    assert LifecycleMachine().policy_evaluator is not None

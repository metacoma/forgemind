from __future__ import annotations

from artifact_workflow_runtime.control_plane.kernel import RuntimeKernel
from artifact_workflow_runtime.decomposition.models import (
    DecompositionOutcome,
    DecompositionPlan,
    ExecutionPacket,
    ExecutionPacketStatus,
    ExecutionPacketType,
)
from artifact_workflow_runtime.decomposition.runtime import progression_decision, status_from_execution_result
from artifact_workflow_runtime.done_contract import DoneContract, RuntimeProofPolicy
from artifact_workflow_runtime.environment import EnvironmentPlan, EnvironmentPlanItem
from artifact_workflow_runtime.models import (
    AcceptanceObligation,
    AcceptanceObligationKind,
    AcceptanceStatus,
    CommandRole,
    CommandEvidence,
    ExecutionFamily,
    ExecutionPlan,
    ExecutionResult,
    ExecutionStatus,
    StructuredEvidence,
    TestLevel,
    TestCheckEvidence,
    BlockerEvidence,
    BlockerKind,
    TaskAcceptanceContract,
)
from artifact_workflow_runtime.qa.models import QACheck, QAPlan
from artifact_workflow_runtime.qa.planner import QAPlanner
from artifact_workflow_runtime.qa.runner import DeterministicQARunner


def _execution_result(*, tests: list[TestCheckEvidence] | None = None, commands: list[CommandEvidence] | None = None, blockers: list[BlockerEvidence] | None = None, status: ExecutionStatus = ExecutionStatus.SUCCEEDED, ok: bool = True) -> ExecutionResult:
    return ExecutionResult(
        request_id="exec_req",
        ok=ok,
        execution_status=status,
        summary="summary",
        evidence_text="evidence",
        structured_evidence=StructuredEvidence(
            tests=tests or [],
            commands_run=commands or [],
            blockers=blockers or [],
        ),
    )


def test_qa_plan_requires_bootstrap_before_runtime_proof_when_repo_path_exists() -> None:
    env = EnvironmentPlan(
        task_id="task",
        items=[
            EnvironmentPlanItem(
                name="verification_runtime",
                required_for=["runtime_proof"],
                bootstrap_possible=True,
                bootstrap_command="./scripts/bootstrap.sh",
                bootstrap_source_kind="setup_script",
                runtime_probe_command="./scripts/smoke.sh",
                runtime_probe_source_kind="smoke_harness",
                failure_mode="bootstrap_then_retry",
            )
        ],
    )
    contract = DoneContract(
        task_id="task",
        primary_goal="prove runtime behavior",
        deliverables=["runtime_proof"],
        verification_policy=RuntimeProofPolicy(required=True),
    )
    plan = ExecutionPlan(summary="change", execution_family=ExecutionFamily.REPOSITORY_CHANGE, reasoning="test")

    qa_plan = QAPlanner().build_plan(task_id="task", execution_plan=plan, done_contract=contract, environment_plan=env)

    assert [(check.kind, check.command) for check in qa_plan.checks] == [
        ("bootstrap", "./scripts/bootstrap.sh"),
        ("runtime_proof", "./scripts/smoke.sh"),
    ]


def test_runtime_proof_blocks_syntax_check_surrogate(tmp_path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    smoke = scripts / "smoke.sh"
    smoke.write_text("#!/usr/bin/env bash\nexit 0\n")
    smoke.chmod(0o755)
    env = EnvironmentPlan(
        task_id="task",
        workspace_root=str(tmp_path),
        items=[EnvironmentPlanItem(name="runtime", runtime_probe_command="bash -n ./scripts/smoke.sh", runtime_probe_source_kind="other")],
    )
    qa_plan = QAPlan(task_id="task", checks=[QACheck(name="runtime_proof", kind="runtime_proof")])

    report = DeterministicQARunner().run(plan=qa_plan, environment_plan=env, cwd=str(tmp_path))

    assert report.items[0].status == "blocked"
    assert "typed runtime probe" in report.items[0].reason


def test_partial_environment_blocker_marks_packet_blocked_not_completed() -> None:
    result = _execution_result(
        status=ExecutionStatus.PARTIAL,
        ok=True,
        blockers=[
            BlockerEvidence(
                summary="integration environment unavailable; bootstrap required before retry",
                blocker_kind=BlockerKind.INTEGRATION_ENVIRONMENT_UNAVAILABLE,
            )
        ],
    )

    assert status_from_execution_result(result) == ExecutionPacketStatus.BLOCKED


def test_blocked_setup_packet_terminates_as_needs_environment_not_reexecute() -> None:
    plan = DecompositionPlan(
        plan_id="plan",
        task_summary="runtime proof",
        decomposition_reason="split setup and integration",
        packets=[
            ExecutionPacket(
                packet_id="setup-1",
                title="bootstrap runtime",
                goal="make runtime usable",
                scope="repo setup scripts",
                packet_type=ExecutionPacketType.SETUP,
                status=ExecutionPacketStatus.BLOCKED,
            ),
            ExecutionPacket(
                packet_id="integration-1",
                title="run smoke",
                goal="prove runtime behavior",
                scope="smoke path",
                packet_type=ExecutionPacketType.INTEGRATION,
                dependencies=["setup-1"],
            ),
        ],
    )

    decision = progression_decision(plan, current_packet_id="setup-1")

    assert decision.outcome == DecompositionOutcome.NEEDS_ENVIRONMENT
    assert decision.selected_next_stage == "finalize"
    assert decision.selected_next_packet_id is None
    assert decision.final_status_hint == "needs_environment"


def test_acceptance_rejects_integration_project_build_as_integration_pass() -> None:
    contract = TaskAcceptanceContract(
        task_id="task",
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        obligations=[
            AcceptanceObligation(kind=AcceptanceObligationKind.CODE_CHANGED, name="Code changed"),
            AcceptanceObligation(kind=AcceptanceObligationKind.INTEGRATION_TESTS_RUN, name="Integration ran"),
            AcceptanceObligation(kind=AcceptanceObligationKind.INTEGRATION_TESTS_PASSED, name="Integration passed"),
        ],
    )
    execution = _execution_result(
        tests=[TestCheckEvidence(name="integration tests project build", command="cmake --build build/integration-tests", status="passed", level=TestLevel.BUILD)]
    ).model_copy(update={"structured_evidence": StructuredEvidence(
        tests=[TestCheckEvidence(name="integration tests project build", command="cmake --build build/integration-tests", status="passed", level=TestLevel.BUILD)],
        # Mutation evidence must pass so the failure is specifically integration proof.
        commands_run=[],
    )})
    execution.structured_evidence.mutation_summary.changed = True

    decision = RuntimeKernel().evaluate_acceptance(contract=contract, execution=execution, verification=None)

    assert decision.accepted is False
    assert decision.status in {AcceptanceStatus.REJECTED, AcceptanceStatus.NEEDS_HUMAN_REVIEW}
    assert any(item.kind == AcceptanceObligationKind.INTEGRATION_TESTS_RUN and item.status.value == "not_run" for item in decision.obligation_results)


def test_environment_prerequisite_not_satisfied_by_script_existence_or_syntax_check() -> None:
    contract = TaskAcceptanceContract(
        task_id="task",
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        obligations=[
            AcceptanceObligation(
                kind=AcceptanceObligationKind.ENVIRONMENT_PREREQUISITES_SATISFIED,
                name="runtime environment ready",
                checks=["freeplane runtime"],
                required_environment=["freeplane"],
            )
        ],
    )
    execution = _execution_result(
        commands=[CommandEvidence(command="bash -n scripts/install.sh", exit_code=0, output_excerpt="syntax ok", role=CommandRole.OTHER)]
    )

    decision = RuntimeKernel().evaluate_acceptance(contract=contract, execution=execution, verification=None)

    assert decision.accepted is False
    assert decision.obligation_results[0].status.value == "failed"
    assert "script existence, syntax checks" in decision.obligation_results[0].reason

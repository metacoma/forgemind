from __future__ import annotations

from artifact_workflow_runtime.control_plane.kernel import RuntimeKernel
from artifact_workflow_runtime.control_plane.stage_filters import packet_scoped_execute_items
from artifact_workflow_runtime.decomposition import progression_decision, status_from_execution_result, update_packet_status
from artifact_workflow_runtime.decomposition.models import DecompositionOutcome, DecompositionPlan, ExecutionPacket, ExecutionPacketStatus, ExecutionPacketType
from artifact_workflow_runtime.environment import EnvironmentPlan, EnvironmentPlanItem, EnvironmentPlanReconciliation
from artifact_workflow_runtime.freshness import FreshnessGate, RetrievalMode
from artifact_workflow_runtime.models import (
    AcceptanceObligation,
    AcceptanceObligationKind,
    AcceptanceObligationStatus,
    BlockerEvidence,
    BlockerKind,
    CommandEvidence,
    ExecutionFamily,
    ExecutionPlan,
    ExecutionResult,
    ExecutionStatus,
    StructuredEvidence,
    Task,
    TaskAcceptanceContract,
    TestCheckEvidence,
)


def _execution(*, ok: bool = True, status: ExecutionStatus = ExecutionStatus.SUCCEEDED, commands=None, tests=None, blockers=None) -> ExecutionResult:
    return ExecutionResult(
        request_id="exec_req",
        ok=ok,
        execution_status=status,
        summary="execution summary",
        evidence_text="execution evidence",
        structured_evidence=StructuredEvidence(commands_run=commands or [], tests=tests or [], blockers=blockers or []),
    )


def test_failed_packet_superseded_by_successful_execute_preserves_history_and_does_not_finalize() -> None:
    plan = DecompositionPlan(
        plan_id="plan",
        task_summary="task",
        decomposition_reason="split",
        packets=[
            ExecutionPacket(
                packet_id="pkt-1",
                title="implementation",
                goal="implement slice",
                scope="repo",
                packet_type=ExecutionPacketType.IMPLEMENTATION,
                status=ExecutionPacketStatus.FAILED,
            ),
            ExecutionPacket(
                packet_id="pkt-2",
                title="docs",
                goal="update docs",
                scope="README",
                packet_type=ExecutionPacketType.DOCS,
                dependencies=["pkt-1"],
            ),
        ],
    )
    result = _execution(tests=[TestCheckEvidence(name="dotnet test", command="dotnet test tests/App.Tests.csproj", status="passed", passed=True)])

    updated, history = update_packet_status(
        plan,
        packet_id="pkt-1",
        new_status=status_from_execution_result(result),
        reason="successful retry",
        stage="execute",
        execution_result_id=result.id,
    )
    decision = progression_decision(updated, current_packet_id="pkt-1")

    assert history.previous_status == ExecutionPacketStatus.FAILED
    assert history.new_status == ExecutionPacketStatus.COMPLETED
    assert updated.packets[0].metadata["superseded_by_execution_result_id"] == result.id
    assert decision.outcome == DecompositionOutcome.RUNNABLE_PACKET
    assert decision.selected_next_packet_id == "pkt-2"
    assert decision.selected_next_stage == "execute"


def test_environment_blocker_marks_packet_blocked_but_deferred_publish_non_action_does_not_block_execute() -> None:
    env_result = _execution(
        status=ExecutionStatus.PARTIAL,
        blockers=[BlockerEvidence(summary="Integration environment unavailable: Freeplane cannot start in this container", blocker_kind=BlockerKind.INTEGRATION_ENVIRONMENT_UNAVAILABLE)],
    )
    publish_deferred_result = _execution(
        blockers=[BlockerEvidence(summary="No git commit, push, or PR created — per packet constraints and forbidden execute actions", blocker_kind=BlockerKind.POLICY_BLOCKED)],
    )

    assert status_from_execution_result(env_result) == ExecutionPacketStatus.BLOCKED
    assert status_from_execution_result(publish_deferred_result) == ExecutionPacketStatus.COMPLETED


def test_environment_plan_reconciles_dotnet_bootstrap_and_probe_but_leaves_freeplane_unusable() -> None:
    plan = EnvironmentPlan(
        task_id="task",
        items=[
            EnvironmentPlanItem(name=".NET 8.0 SDK installed and available in PATH", required_for=["build"], bootstrap_command="/workspace/project/dotnet-install.sh", runtime_probe_command="dotnet --version"),
            EnvironmentPlanItem(name="Freeplane runtime", required_for=["runtime_proof"], runtime_probe_command="misc/scripts/run-freeplane-csharp-smoke-test.sh"),
        ],
    )
    execution = _execution(
        commands=[
            CommandEvidence(command="bash /workspace/project/dotnet-install.sh --channel 8.0 --install-dir /home/openhands/.dotnet --no-path", exit_code=0, output_excerpt="installed"),
            CommandEvidence(command="dotnet --version", exit_code=0, output_excerpt="8.0.422"),
        ],
        blockers=[BlockerEvidence(summary="Integration/smoke tests cannot run in this container because Freeplane runtime is unavailable", blocker_kind=BlockerKind.INTEGRATION_ENVIRONMENT_UNAVAILABLE)],
    )

    reconciled, changes = EnvironmentPlanReconciliation().reconcile_execution(plan, execution, evidence_artifact_ids=["artifact-1"])

    assert changes
    dotnet = reconciled.items[0]
    freeplane = reconciled.items[1]
    assert dotnet.bootstrap_attempted is True
    assert dotnet.bootstrap_status == "success"
    assert dotnet.runtime_usable is True
    assert dotnet.runtime_probe_command == "dotnet --version"
    assert dotnet.resolved_version == "8.0.422"
    assert "artifact-1" in dotnet.evidence_artifact_ids
    assert freeplane.runtime_usable is False


def test_freshness_gate_triggers_docs_plus_versions_for_dotnet_grpc_nuget_task() -> None:
    decision = FreshnessGate().decide(Task(description="Add a C# .NET gRPC client library using Grpc.Net.Client, Google.Protobuf, Grpc.Tools, NuGet packages, xUnit, Moq, and GitHub Actions setup-dotnet workflow."))

    assert decision.freshness_required is True
    assert decision.retrieval_mode == RetrievalMode.DOCS_PLUS_VERSIONS
    assert decision.docs_resolution_required is True
    assert decision.version_resolution_required is True
    assert "tooling_current_syntax" in decision.triggered_by


def test_setup_packet_prompt_scope_excludes_full_implementation_docs_and_ci_steps() -> None:
    packet = ExecutionPacket(packet_id="setup", title="setup runtime", goal="install SDK and probe tools", scope="bootstrap only", packet_type=ExecutionPacketType.SETUP)
    scoped = packet_scoped_execute_items(
        [
            "Install .NET SDK and run dotnet --version",
            "Implement the full C# gRPC client library",
            "Update README examples",
            "Add GitHub Actions workflow",
        ],
        packet,
    )

    assert scoped == ["Install .NET SDK and run dotnet --version"]


def test_expected_diff_and_build_only_do_not_satisfy_runtime_or_force_publish_failure() -> None:
    contract = TaskAcceptanceContract(
        task_id="task",
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        obligations=[
            AcceptanceObligation(kind=AcceptanceObligationKind.BUILD_OR_COMPILE_SUCCEEDED, name="build passed"),
            AcceptanceObligation(kind=AcceptanceObligationKind.INTEGRATION_TESTS_RUN, name="smoke ran"),
            AcceptanceObligation(kind=AcceptanceObligationKind.PUBLISH_OBLIGATIONS_SATISFIED, name="publish done"),
        ],
    )
    execution = _execution(
        tests=[TestCheckEvidence(name="dotnet build FreeplaneGrpcClient.sln", command="dotnet build FreeplaneGrpcClient.sln", status="passed", passed=True)],
    )

    decision = RuntimeKernel().evaluate_acceptance(contract=contract, execution=execution, verification=None, publish=None)
    statuses = {item.kind: item.status for item in decision.obligation_results}

    assert statuses[AcceptanceObligationKind.BUILD_OR_COMPILE_SUCCEEDED] == AcceptanceObligationStatus.PASSED
    assert statuses[AcceptanceObligationKind.INTEGRATION_TESTS_RUN] == AcceptanceObligationStatus.NOT_RUN
    assert statuses[AcceptanceObligationKind.PUBLISH_OBLIGATIONS_SATISFIED] == AcceptanceObligationStatus.NOT_RUN
    assert decision.final_workflow_status != "publish_failed"

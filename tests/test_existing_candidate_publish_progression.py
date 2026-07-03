from artifact_workflow_runtime.control_plane import RuntimeKernel
from artifact_workflow_runtime.decomposition.models import ExecutionPacket, ExecutionPacketStatus, ExecutionPacketType
from artifact_workflow_runtime.models import (
    BlockerEvidence,
    BlockerKind,
    ExecutionFamily,
    ExecutionPlan,
    ExecutionResult,
    ExecutionStatus,
    MutationSummary,
    StructuredEvidence,
    TestCheckEvidence,
    TestLevel,
)
from artifact_workflow_runtime.stages.execution import (
    _augment_with_existing_workspace_mutation,
    _packet_status_from_typed_execution_result,
)


def test_existing_workspace_candidate_counts_as_mutation_and_completes_implementation_packet() -> None:
    result = ExecutionResult(
        request_id="exec_req_1",
        ok=True,
        execution_status=ExecutionStatus.SUCCEEDED,
        summary="verified existing candidate",
        evidence_text="{}",
        structured_evidence=StructuredEvidence(
            blockers=[
                BlockerEvidence(
                    summary="Missing evidence: downstream unit/integration results",
                    blocker_kind=BlockerKind.MISSING_EVIDENCE,
                )
            ],
            tests=[
                TestCheckEvidence(
                    name="build",
                    level=TestLevel.BUILD,
                    status="success",
                    passed=True,
                    command="dotnet build",
                )
            ],
            mutation_summary=MutationSummary(changed=False, summary="no direct edits reported"),
        ),
    )
    packet = ExecutionPacket(
        packet_id="pkt_impl",
        packet_type=ExecutionPacketType.IMPLEMENTATION,
        title="impl",
        goal="goal",
        scope="scope",
        strategy_id="default",
    )
    augmented = _augment_with_existing_workspace_mutation(
        result,
        packet=packet,
        workspace_snapshot={"changed_paths": ["grpc/csharp/Freeplane.Grpc.csproj", "grpc/csharp/src/FreeplaneClient.cs"]},
        workspace_reconciliation={"adopt_existing_work": True, "delivery_mode": "complete_existing_candidate"},
    )
    assert augmented.structured_evidence.mutation_summary.changed is True
    assert {item.path for item in augmented.structured_evidence.files_changed} >= {
        "grpc/csharp/Freeplane.Grpc.csproj",
        "grpc/csharp/src/FreeplaneClient.cs",
    }
    status = _packet_status_from_typed_execution_result(
        augmented,
        packet=packet,
        required_test_levels=["build", "unit", "integration"],
    )
    assert status == ExecutionPacketStatus.COMPLETED


def test_publish_denial_routes_to_verify_without_policy_violation() -> None:
    kernel = RuntimeKernel()
    plan = ExecutionPlan(
        summary="publish candidate",
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        require_commit=True,
        reasoning="test",
    )
    execution = ExecutionResult(
        request_id="exec_req_publish",
        ok=True,
        execution_status=ExecutionStatus.SUCCEEDED,
        summary="candidate still needs verification before publish",
        evidence_text="{}",
        structured_evidence=StructuredEvidence(
            blockers=[
                BlockerEvidence(
                    summary="Missing evidence: integration results",
                    blocker_kind=BlockerKind.MISSING_EVIDENCE,
                )
            ],
            mutation_summary=MutationSummary(changed=True, files_changed=["grpc/csharp/src/FreeplaneClient.cs"]),
        ),
    )
    decision = kernel.review_execution(plan=plan, execution=execution)
    assert decision.graph_next == "verify"
    assert decision.allowed is True
    assert "Publish policy denied direct publish" in decision.reason

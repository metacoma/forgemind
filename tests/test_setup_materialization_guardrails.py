from __future__ import annotations

from pathlib import Path

from artifact_workflow_runtime.control_plane.reconcile import _passed_obligations
from artifact_workflow_runtime.decomposition import DecompositionPlanner, ExecutionPacketType
from artifact_workflow_runtime.done_contract import DoneContract, EnvironmentRequirement, RuntimeProofPolicy
from artifact_workflow_runtime.environment import EnvironmentDiscovery, EnvironmentPlan, EnvironmentPlanItem
from artifact_workflow_runtime.models import (
    AcceptanceObligation,
    AcceptanceObligationKind,
    BlockerEvidence,
    BlockerKind,
    ContextPacket,
    ExecutionFamily,
    FileEvidence,
    FileRole,
    ObservationResult,
    StructuredEvidence,
    Task,
    TaskAcceptanceContract,
)
from artifact_workflow_runtime.models.state import WorkflowStateSnapshot
from artifact_workflow_runtime.stages.execution import _environment_materialization_block


def test_environment_discovery_prefers_observed_repo_scripts_over_guessed_freeplane_bootstrap(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    misc = repo / "misc" / "scripts"
    misc.mkdir(parents=True)
    (misc / "run-freeplane-csharp-smoke-test.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
    (misc / "_check_grpc_map.py").write_text("print('ok')\n")

    task = Task(description="Run C# smoke tests against Freeplane")
    contract = DoneContract(
        task_id=task.id,
        primary_goal="runtime proof",
        deliverables=["runtime_proof"],
        verification_policy=RuntimeProofPolicy(required=True),
        environment_requirements=[EnvironmentRequirement(name="freeplane_runtime", mode="bootstrap_if_needed", source="repo_supported")],
    )
    context = ContextPacket(task_id=task.id, text="Use misc/scripts/run-freeplane-csharp-smoke-test.sh when present.")
    observation = ObservationResult(
        request_id="obs-env",
        ok=True,
        summary="Observed repo-supported smoke harness and runtime probe.",
        evidence_text="",
        structured_evidence=StructuredEvidence(
            files_observed=[
                FileEvidence(path=str(misc / "run-freeplane-csharp-smoke-test.sh"), role=FileRole.SMOKE_HARNESS),
                FileEvidence(path=str(misc / "_check_grpc_map.py"), role=FileRole.RUNTIME_PROBE_SCRIPT),
            ]
        ),
    )

    env = EnvironmentDiscovery().build_plan(
        task=task,
        done_contract=contract,
        context_packet=context,
        workspace_branch="awrt/test",
        workspace_root=str(repo),
        repo_root=str(repo),
        observation=observation,
    )

    item = env.items[0]
    assert item.bootstrap_command == "./misc/scripts/run-freeplane-csharp-smoke-test.sh"
    assert item.bootstrap_resolution == "observed_repo_path"
    assert item.bootstrap_source == "repo_supported"
    assert item.runtime_probe_command in {"./misc/scripts/run-freeplane-csharp-smoke-test.sh", "./misc/scripts/_check_grpc_map.py"}
    assert item.bootstrap_source_kind in {"smoke_harness", "runtime_probe_script"}


def test_passed_obligations_do_not_infer_integration_or_smoke_from_script_names_alone() -> None:
    observation = ObservationResult(
        request_id="obs-1",
        ok=True,
        summary="Observed modify_mindmap example and smoke scripts in the repository. No tests were executed.",
        evidence_text="Files observed: misc/scripts/run-freeplane-csharp-smoke-test.sh and grpc/csharp/examples/test_json_roundtrip.cs",
        structured_evidence=StructuredEvidence(),
    )

    passed = _passed_obligations(observation)

    assert "integration" not in passed
    assert "smoke" not in passed


def test_planner_emits_setup_packet_when_environment_gap_is_observed() -> None:
    acceptance = TaskAcceptanceContract(
        task_id="task_1",
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        obligations=[
            AcceptanceObligation(kind=AcceptanceObligationKind.ENVIRONMENT_PREREQUISITES_SATISFIED, name="runtime env"),
            AcceptanceObligation(kind=AcceptanceObligationKind.INTEGRATION_TESTS_RUN, name="integration run"),
        ],
    )
    observation = ObservationResult(
        request_id="obs-setup",
        ok=True,
        summary="runtime blocked",
        evidence_text="missing java and xvfb",
        structured_evidence=StructuredEvidence(
            blockers=[
                BlockerEvidence(summary="Java runtime not available in this container", blocker_kind=BlockerKind.MISSING_ENVIRONMENT_DEPENDENCY),
            ]
        ),
    )
    snapshot = WorkflowStateSnapshot(task=Task(description="Run integration tests that require runtime bootstrap"), observation_result=observation)
    plan = DecompositionPlanner().build_plan(
        task=snapshot.task,
        strategy_id="mvp_first",
        acceptance_contract=acceptance,
        snapshot=snapshot,
    )

    assert plan.packets[0].packet_type == ExecutionPacketType.SETUP


def test_execute_materialization_block_prefers_observed_bootstrap_and_runtime_probe() -> None:
    env = EnvironmentPlan(
        task_id="task_1",
        items=[
            EnvironmentPlanItem(
                name="freeplane_runtime",
                bootstrap_possible=True,
                bootstrap_source="repo_supported",
                bootstrap_resolution="observed_repo_path",
                bootstrap_command="./misc/scripts/run-freeplane-csharp-smoke-test.sh",
                runtime_probe_resolution="observed_repo_path",
                runtime_probe_command="./misc/scripts/_check_grpc_map.py",
            )
        ],
    )

    block = _environment_materialization_block(env, packet=None)

    assert "run-freeplane-csharp-smoke-test.sh" in block["prompt_block"]
    assert any("Attempt repository-supported bootstrap" in step for step in block["suggested_steps"])
    assert any("prove runtime usability" in step for step in block["suggested_steps"])

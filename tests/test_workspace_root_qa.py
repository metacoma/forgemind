from __future__ import annotations

from pathlib import Path

import pytest

from artifact_workflow_runtime.environment import EnvironmentPlan, EnvironmentPlanItem
from artifact_workflow_runtime.models import ExecutionResult, FileEvidence, StructuredEvidence, Task
from artifact_workflow_runtime.models.state import CoreWorkflowStage, WorkflowStateSnapshot, required_fields_for_stage
from artifact_workflow_runtime.qa import DeterministicQARunner, QACheck, QAPlan
from artifact_workflow_runtime.state.workspace import infer_workspace_root_from_execution, infer_workspace_root_from_text, workspace_root_from_state


def test_workspace_root_is_inferred_from_explicit_task_path() -> None:
    assert infer_workspace_root_from_text("Clone repo into /workspace/freeplane_plugin_grpc and work there") == "/workspace/freeplane_plugin_grpc"


def test_workspace_root_is_inferred_from_execution_absolute_file_paths() -> None:
    result = ExecutionResult(
        request_id="exec_req",
        ok=True,
        summary="changed files",
        evidence_text="",
        structured_evidence=StructuredEvidence(
            files_changed=[
                FileEvidence(
                    path="/workspace/freeplane_plugin_grpc/grpc/csharp/tests/IntegrationTests.cs",
                    action="modified",
                )
            ]
        ),
    )

    assert infer_workspace_root_from_execution(result) == "/workspace/freeplane_plugin_grpc"


def test_workspace_root_from_state_prefers_execution_evidence_over_process_cwd() -> None:
    task = Task(description="Add client")
    result = ExecutionResult(
        request_id="exec_req",
        ok=True,
        summary="built in /workspace/freeplane_plugin_grpc/grpc/csharp",
        evidence_text="",
        structured_evidence=StructuredEvidence(
            files_changed=[FileEvidence(path="/workspace/freeplane_plugin_grpc/.github/workflows/ci.yml", action="modified")]
        ),
    )
    state = {"task": task.model_dump(mode="json"), "execution_result": result.model_dump(mode="json")}

    assert workspace_root_from_state(state) == "/workspace/freeplane_plugin_grpc"


def test_qa_runner_checks_ci_config_under_workspace_root_not_current_directory(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    workflows = repo / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("name: ci\n")
    other_cwd = tmp_path / "controller-cwd"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)

    plan = QAPlan(task_id="task_1", checks=[QACheck(name="ci_config_check", kind="ci_config_check")])
    env = EnvironmentPlan(task_id="task_1", workspace_root=str(repo))

    report = DeterministicQARunner().run(plan=plan, environment_plan=env)

    assert report.workspace_root == str(repo)
    assert report.items[0].status == "passed"
    assert "ci.yml" in report.items[0].output
    assert str(repo) in report.items[0].reason


def test_qa_runner_blocks_when_workspace_root_is_not_accessible(tmp_path) -> None:
    missing = tmp_path / "missing-repo"
    plan = QAPlan(task_id="task_1", checks=[QACheck(name="ci_config_check", kind="ci_config_check")])
    env = EnvironmentPlan(task_id="task_1", workspace_root=str(missing))

    report = DeterministicQARunner().run(plan=plan, environment_plan=env)

    assert report.items[0].status == "blocked"
    assert "not accessible" in report.items[0].reason


def test_runtime_proof_missing_relative_script_is_environment_blocker(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    plan = QAPlan(task_id="task_1", checks=[QACheck(name="runtime_proof", kind="runtime_proof")])
    env = EnvironmentPlan(
        task_id="task_1",
        workspace_root=str(repo),
        items=[EnvironmentPlanItem(name="freeplane_runtime", bootstrap_command="./scripts/install_freeplane.sh")],
    )

    report = DeterministicQARunner().run(plan=plan, environment_plan=env)

    assert report.items[0].status == "blocked"
    assert "bootstrap path existence is not runtime proof" in report.items[0].reason


def test_qa_execute_stage_requires_workspace_root_in_canonical_contract() -> None:
    assert "workspace_root" in required_fields_for_stage(CoreWorkflowStage.QA_EXECUTE)

    task = Task(description="x")
    snapshot = WorkflowStateSnapshot(task=task, qa_plan=QAPlan(task_id=task.id))
    with pytest.raises(ValueError, match="workspace_root"):
        snapshot.assert_ready_for_stage(CoreWorkflowStage.QA_EXECUTE)

from __future__ import annotations

from pathlib import Path

import pytest

from artifact_workflow_runtime.evaluation import ScenarioRunner, compare_reports, load_scenario_spec, load_scenarios
from artifact_workflow_runtime.evaluation.cli import build_parser
from artifact_workflow_runtime.evaluation.models import EvaluationRunReport, ScenarioRunRequest, ScenarioRunResult


def test_load_single_and_directory_scenarios() -> None:
    single = load_scenario_spec(Path("scenarios/repo_basic/repo_feature_simple.yaml"))
    assert single.scenario_id == "repo_feature_simple"

    scenarios = load_scenarios(Path("scenarios/repo_basic"))
    ids = {item.scenario_id for item in scenarios}
    assert "repo_feature_simple" in ids
    assert "repo_feature_with_docs" in ids


@pytest.mark.asyncio
async def test_scenario_runner_produces_scorecard_and_outputs(tmp_path) -> None:
    runner = ScenarioRunner()
    spec = load_scenario_spec(Path("scenarios/repo_basic/repo_feature_simple.yaml"))
    result = await runner.run_scenario(spec, ScenarioRunRequest(scenario_id=spec.scenario_id, artifact_dir=str(tmp_path)))

    assert result.terminal_status == "completed"
    assert result.scorecard is not None and result.scorecard.passed is True
    assert (tmp_path / spec.scenario_id / "scenario_result.json").exists()
    assert "commands_run" in result.required_evidence_found


@pytest.mark.asyncio
async def test_run_pack_writes_json_and_markdown_reports(tmp_path) -> None:
    runner = ScenarioRunner()
    report = await runner.run_pack(Path("scenarios/repo_basic"), artifact_dir=str(tmp_path))

    assert report.summary.scenario_count >= 3
    assert (tmp_path / "repo_basic.json").exists()
    assert (tmp_path / "repo_basic.md").exists()

    loaded = EvaluationRunReport.model_validate_json((tmp_path / "repo_basic.json").read_text(encoding="utf-8"))
    assert loaded.summary.pack_id == "repo_basic"


@pytest.mark.asyncio
async def test_compare_reports_detects_improvement(tmp_path) -> None:
    runner = ScenarioRunner()
    before = await runner.run_pack(Path("scenarios/repo_basic"), artifact_dir=str(tmp_path / "before"))
    after = before.model_copy(deep=True)
    improved = after.scenario_results[0]
    improved.terminal_status = "completed"
    if improved.scorecard is not None:
        improved.scorecard.overall_score += 5
    after.summary = after.summary.model_copy(update={"completion_rate": min(before.summary.completion_rate + 0.1, 1.0)})
    comparison = compare_reports(before, after)

    assert comparison.pack_id == after.pack_id
    assert comparison.overall_delta >= 0


def test_eval_cli_parser_accepts_expected_subcommands() -> None:
    parser = build_parser()
    args = parser.parse_args(["run", "scenarios/repo_basic/repo_feature_simple.yaml", "--artifact-dir", "eval_runs"])
    assert args.command == "run"
    assert args.artifact_dir == "eval_runs"

    args = parser.parse_args(["run-pack", "scenarios/repo_basic"])
    assert args.command == "run-pack"

    args = parser.parse_args(["compare", "before.json", "after.json"])
    assert args.command == "compare"

    args = parser.parse_args(["report", "report.json"])
    assert args.command == "report"


def test_live_scenario_metadata_loads() -> None:
    spec = load_scenario_spec(Path("scenarios/live/canonical_blocked_env.yaml"))

    assert spec.live_environment_profile == "isolated_repo_with_missing_runtime"
    assert spec.requires_live_repo is True
    assert spec.requires_live_openhands is True
    assert spec.safe_for_live is True
    assert spec.allowed_terminal_statuses_for_mode("live") == ["needs_environment", "blocked"]
    assert "blockers" in spec.required_evidence_for_mode("live")


@pytest.mark.asyncio
async def test_live_mode_without_endpoint_is_gated(tmp_path) -> None:
    runner = ScenarioRunner()
    spec = load_scenario_spec(Path("scenarios/live/canonical_blocked_env.yaml"))
    result = await runner.run_scenario(spec, ScenarioRunRequest(scenario_id=spec.scenario_id, artifact_dir=str(tmp_path), mode="live"))

    assert result.execution_mode == "live"
    assert result.terminal_status == "live_gated"
    assert any("direct LLM endpoint" in reason for reason in result.fail_reasons)
    assert any("OpenHands endpoint" in reason for reason in result.fail_reasons)
    assert (tmp_path / spec.scenario_id / "scenario_result.json").exists()


@pytest.mark.asyncio
async def test_live_runner_builds_real_runtime_controller_request(tmp_path, monkeypatch) -> None:
    from artifact_workflow_runtime.artifacts import ArtifactStore
    from artifact_workflow_runtime.evaluation import runner as runner_module
    from artifact_workflow_runtime.models import FinalReport

    captured: dict[str, object] = {}

    class FakeLiveController:
        def __init__(self, artifact_dir: str) -> None:
            self.artifact_store = ArtifactStore(artifact_dir)
            self.artifact_store.add_json("workflow_checkpoint", {"stage": "observe"}, metadata={"stage": "observe"})
            self.artifact_store.add_json("workflow_checkpoint", {"stage": "execute"}, metadata={"stage": "execute"})
            self.artifact_store.add_json("decomposition_plan", {"packets": [{"packet_type": "implementation"}]})
            self.artifact_store.add_json("execution_result", {"structured_evidence": {"commands_run": [{"command": "pytest", "exit_code": 0}], "files_changed": [{"path": "src/app.py"}], "tests": [{"name": "pytest", "passed": True}]}})

        async def run(self, task):
            captured["task_description"] = task.description
            captured["task_metadata"] = task.metadata
            return FinalReport(task_id=task.id, status="completed", summary="live completed")

    def fake_build_controller(**kwargs):
        captured.update(kwargs)
        return FakeLiveController(kwargs["artifact_dir"])

    monkeypatch.setattr(runner_module, "build_controller", fake_build_controller)
    spec = load_scenario_spec(Path("scenarios/repo_basic/repo_feature_simple.yaml"))
    result = await ScenarioRunner().run_scenario(
        spec,
        ScenarioRunRequest(
            scenario_id=spec.scenario_id,
            artifact_dir=str(tmp_path),
            mode="live",
            direct_llm_endpoint="http://llm.local/v1",
            openhands_endpoint="http://openhands.local",
            approve_live=True,
        ),
    )

    assert captured["direct_llm_endpoint"] == "http://llm.local/v1"
    assert captured["openhands_endpoint"] == "http://openhands.local"
    assert captured["reuse"] is False
    assert result.execution_mode == "live"
    assert result.terminal_status == "completed"
    assert result.live_run_id is not None
    assert result.live_artifact_dir is not None
    assert "commands_run" in result.required_evidence_found
    assert "isolated repository benchmark" in str(captured["task_description"])


@pytest.mark.asyncio
async def test_scripted_mode_still_works_with_mode_metadata(tmp_path) -> None:
    runner = ScenarioRunner()
    spec = load_scenario_spec(Path("scenarios/repo_basic/repo_feature_simple.yaml"))
    result = await runner.run_scenario(spec, ScenarioRunRequest(scenario_id=spec.scenario_id, artifact_dir=str(tmp_path), mode="scripted"))

    assert result.execution_mode == "scripted"
    assert result.terminal_status == "completed"
    assert result.scorecard is not None and result.scorecard.passed is True


def test_live_false_success_missing_evidence_is_hard_failure() -> None:
    spec = load_scenario_spec(Path("scenarios/live/canonical_blocked_env.yaml"))
    result = ScenarioRunResult(
        scenario_id=spec.scenario_id,
        terminal_status="completed",
        runtime_status="completed",
        execution_mode="live",
        final_report={"task_id": "task_live", "status": "completed", "summary": "build only"},
        required_evidence_found=["commands_run"],
    )

    from artifact_workflow_runtime.evaluation import score_scenario_run

    scorecard = score_scenario_run(spec, result)
    assert scorecard.passed is False
    assert any("completed" in failure for failure in scorecard.hard_failures)


def test_eval_cli_parser_accepts_live_mode_options() -> None:
    parser = build_parser()
    args = parser.parse_args([
        "run",
        "scenarios/repo_basic/repo_feature_simple.yaml",
        "--mode",
        "live",
        "--direct-llm-endpoint",
        "http://llm.local/v1",
        "--openhands-endpoint",
        "http://openhands.local",
        "--approve-live",
    ])

    assert args.command == "run"
    assert args.mode == "live"
    assert args.approve_live is True


def test_compare_and_report_include_modes() -> None:
    from artifact_workflow_runtime.evaluation.reporting import render_markdown_report

    before = EvaluationRunReport(
        pack_id="mixed",
        execution_mode="scripted",
        scenario_results=[],
        summary={
            "pack_id": "mixed",
            "scenario_count": 0,
            "passed_count": 0,
            "completion_rate": 0.0,
            "acceptance_pass_rate": 0.0,
            "false_success_rate": 0.0,
            "average_loops": 0.0,
            "average_packets": 0.0,
            "average_repairs": 0.0,
            "average_duration_seconds": 0.0,
            "mode_counts": {"scripted": 0},
        },
    )
    after = before.model_copy(update={"execution_mode": "live", "summary": before.summary.model_copy(update={"mode_counts": {"live": 0}})})
    comparison = compare_reports(before, after)
    markdown = render_markdown_report(after)

    assert comparison.before_mode == "scripted"
    assert comparison.after_mode == "live"
    assert "Mode: **live**" in markdown

@pytest.mark.asyncio
async def test_live_runner_accepts_runtime_config_file(tmp_path, monkeypatch) -> None:
    from artifact_workflow_runtime.artifacts import ArtifactStore
    from artifact_workflow_runtime.evaluation import runner as runner_module
    from artifact_workflow_runtime.models import FinalReport

    config = tmp_path / "runtime.yaml"
    config.write_text(
        "direct_llm:\n  endpoint: http://llm-from-config/v1\n  model: direct-model\nopenhands:\n  endpoint: http://openhands-from-config\n  model: oh-model\napprove_live: true\n",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    class FakeLiveController:
        def __init__(self, artifact_dir: str) -> None:
            self.artifact_store = ArtifactStore(artifact_dir)

        async def run(self, task):
            return FinalReport(task_id=task.id, status="completed", summary="ok")

    def fake_build_controller(**kwargs):
        captured.update(kwargs)
        return FakeLiveController(kwargs["artifact_dir"])

    monkeypatch.setattr(runner_module, "build_controller", fake_build_controller)
    spec = load_scenario_spec(Path("scenarios/repo_basic/repo_feature_simple.yaml"))
    result = await ScenarioRunner().run_scenario(
        spec,
        ScenarioRunRequest(
            scenario_id=spec.scenario_id,
            artifact_dir=str(tmp_path / "runs"),
            mode="live",
            runtime_config_path=str(config),
        ),
    )

    assert result.terminal_status == "completed"
    assert captured["direct_llm_endpoint"] == "http://llm-from-config/v1"
    assert captured["direct_llm_model"] == "direct-model"
    assert captured["openhands_endpoint"] == "http://openhands-from-config"
    assert captured["openhands_model"] == "oh-model"

from __future__ import annotations

from pathlib import Path

import pytest

from artifact_workflow_runtime.evaluation import ScenarioRunner, compare_reports, load_scenario_spec, load_scenarios
from artifact_workflow_runtime.evaluation.cli import build_parser
from artifact_workflow_runtime.evaluation.models import EvaluationRunReport, ScenarioRunRequest


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

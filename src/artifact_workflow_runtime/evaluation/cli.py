from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .compare import compare_reports
from .loader import load_scenario_spec
from .models import EvaluationRunReport, ScenarioRunRequest
from .reporting import render_markdown_report
from .runner import ScenarioRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="artifact-workflow-eval")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run a single scenario spec")
    run.add_argument("scenario")
    run.add_argument("--artifact-dir", default="eval_runs")
    run.add_argument("--model-routing-config", default=None)
    run.add_argument("--timeout-seconds", type=int, default=120)
    run.add_argument("--auto-approve", action="store_true")

    run_pack = sub.add_parser("run-pack", help="Run a directory of scenarios")
    run_pack.add_argument("path")
    run_pack.add_argument("--artifact-dir", default="eval_runs")

    compare = sub.add_parser("compare", help="Compare two evaluation reports")
    compare.add_argument("before")
    compare.add_argument("after")

    report = sub.add_parser("report", help="Render markdown summary from evaluation report JSON")
    report.add_argument("input")
    return parser


async def _run_async(args: argparse.Namespace) -> int:
    runner = ScenarioRunner()
    if args.command == "run":
        spec = load_scenario_spec(args.scenario)
        result = await runner.run_scenario(
            spec,
            ScenarioRunRequest(
                scenario_id=spec.scenario_id,
                artifact_dir=args.artifact_dir,
                model_routing_config_path=args.model_routing_config,
                timeout_seconds=args.timeout_seconds,
                auto_approve=args.auto_approve,
            ),
        )
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0
    if args.command == "run-pack":
        report = await runner.run_pack(args.path, artifact_dir=args.artifact_dir)
        print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0
    if args.command == "compare":
        before = EvaluationRunReport.model_validate_json(Path(args.before).read_text(encoding="utf-8"))
        after = EvaluationRunReport.model_validate_json(Path(args.after).read_text(encoding="utf-8"))
        comparison = compare_reports(before, after)
        print(json.dumps(comparison.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0
    if args.command == "report":
        report = EvaluationRunReport.model_validate_json(Path(args.input).read_text(encoding="utf-8"))
        print(render_markdown_report(report), end="")
        return 0
    return 1


def main() -> None:
    raise SystemExit(asyncio.run(_run_async(build_parser().parse_args())))

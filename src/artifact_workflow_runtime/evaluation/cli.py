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


def _add_run_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--artifact-dir", default="eval_runs")
    parser.add_argument("--mode", choices=["scripted", "live"], default="scripted")
    parser.add_argument("--runtime-config", default=None)
    parser.add_argument("--model-routing-config", default=None)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--auto-approve", action="store_true")
    parser.add_argument("--approve-live", action="store_true", help="Allow scenarios marked requires_approval_for_live")
    parser.add_argument("--allow-live-network", action="store_true")
    parser.add_argument("--allow-live-host", action="store_true")
    parser.add_argument("--allow-live-publish", action="store_true")
    parser.add_argument("--direct-llm-endpoint", default=None)
    parser.add_argument("--direct-llm-model", default=None)
    parser.add_argument("--direct-llm-api-key", default=None)
    parser.add_argument("--openhands-endpoint", default=None)
    parser.add_argument("--openhands-model", default=None)
    parser.add_argument("--openhands-api-key", default=None)
    parser.add_argument("--reuse-mode", choices=["isolated", "reuse"], default="isolated")
    parser.add_argument("--sandbox-id", default=None)
    parser.add_argument("--conversation-id", default=None)
    parser.add_argument("--strategy-selection-mode", default="rule_based")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="artifact-workflow-eval")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run a single scenario spec")
    run.add_argument("scenario")
    _add_run_options(run)

    run_pack = sub.add_parser("run-pack", help="Run a directory of scenarios")
    run_pack.add_argument("path")
    _add_run_options(run_pack)

    compare = sub.add_parser("compare", help="Compare two evaluation reports")
    compare.add_argument("before")
    compare.add_argument("after")

    report = sub.add_parser("report", help="Render markdown summary from evaluation report JSON")
    report.add_argument("input")
    return parser


def _request_for_spec(args: argparse.Namespace, scenario_id: str) -> ScenarioRunRequest:
    return ScenarioRunRequest(
        scenario_id=scenario_id,
        mode=args.mode,
        artifact_dir=args.artifact_dir,
        runtime_config_path=args.runtime_config,
        model_routing_config_path=args.model_routing_config,
        timeout_seconds=args.timeout_seconds,
        auto_approve=args.auto_approve,
        reuse_mode=args.reuse_mode,
        direct_llm_endpoint=args.direct_llm_endpoint,
        direct_llm_model=args.direct_llm_model,
        direct_llm_api_key=args.direct_llm_api_key,
        openhands_endpoint=args.openhands_endpoint,
        openhands_model=args.openhands_model,
        openhands_api_key=args.openhands_api_key,
        sandbox_id=args.sandbox_id,
        conversation_id=args.conversation_id,
        approve_live=args.approve_live,
        allow_live_network=args.allow_live_network,
        allow_live_host=args.allow_live_host,
        allow_live_publish=args.allow_live_publish,
        strategy_selection_mode=args.strategy_selection_mode,
    )


async def _run_async(args: argparse.Namespace) -> int:
    runner = ScenarioRunner()
    if args.command == "run":
        spec = load_scenario_spec(args.scenario)
        result = await runner.run_scenario(spec, _request_for_spec(args, spec.scenario_id))
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0
    if args.command == "run-pack":
        report = await runner.run_pack(args.path, artifact_dir=args.artifact_dir, request_factory=lambda spec: _request_for_spec(args, spec.scenario_id), mode=args.mode)
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

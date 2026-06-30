from __future__ import annotations

import argparse
import asyncio
import json

from artifact_workflow_runtime.models import Task
from artifact_workflow_runtime.runtime_factory import build_controller
from artifact_workflow_runtime.strategy import StrategySelectionMode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="artifact-workflow-run")
    parser.add_argument("--task", required=True, help="User task text")
    parser.add_argument("--title", default=None)
    parser.add_argument("--artifact-dir", default="run-artifacts")
    parser.add_argument("--config", default=None, help="YAML model routing config with stage-based direct_llm/openhands mappings")

    parser.add_argument(
        "--strategy-selection-mode",
        choices=[item.value for item in StrategySelectionMode],
        default=StrategySelectionMode.RULE_BASED.value,
        help="Strategy selection mode: rule_based, llm_assisted, or hybrid",
    )

    parser.add_argument("--direct-llm-endpoint", required=True)
    parser.add_argument("--direct-llm-model", required=True)
    parser.add_argument("--direct-llm-api-key", default=None)

    parser.add_argument("--openhands-endpoint", required=True)
    parser.add_argument("--openhands-model", required=True)
    parser.add_argument("--openhands-api-key", default=None)
    parser.add_argument("--reuse", action="store_true", help="Reuse an existing OpenHands sandbox for the same model instead of always starting fresh")
    parser.add_argument("--sandbox-id", default=None, help="Explicit OpenHands sandbox_id to run against")
    parser.add_argument("--conversation-id", default=None, help="Optional existing OpenHands conversation_id to continue")

    parser.add_argument("--auto-approve", action="store_true", help="Auto-approve policy-gated mutations")
    return parser


async def _run(args: argparse.Namespace) -> int:
    controller = build_controller(
        artifact_dir=args.artifact_dir,
        direct_llm_endpoint=args.direct_llm_endpoint,
        direct_llm_model=args.direct_llm_model,
        direct_llm_api_key=args.direct_llm_api_key,
        openhands_endpoint=args.openhands_endpoint,
        openhands_model=args.openhands_model,
        openhands_api_key=args.openhands_api_key,
        reuse=args.reuse,
        sandbox_id=args.sandbox_id,
        conversation_id=args.conversation_id,
        auto_approve=args.auto_approve,
        config_path=args.config,
        strategy_selection_mode=args.strategy_selection_mode,
    )
    task = Task(title=args.title, description=args.task)
    report = await controller.run(task)
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run(build_parser().parse_args())))

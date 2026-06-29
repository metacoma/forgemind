from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from artifact_workflow_runtime.controller import WorkflowController
from artifact_workflow_runtime.models import Task
from artifact_workflow_runtime.llm_backend import OpenAICompatibleLLMBackend
from artifact_workflow_runtime.openhands_adapter import OpenHandsAdapter, OpenHandsInstance
from artifact_workflow_runtime.policy import StaticApprovalProvider
from artifact_workflow_runtime.artifacts import ArtifactStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="artifact-workflow-run")
    parser.add_argument("--task", required=True, help="User task text")
    parser.add_argument("--title", default=None)
    parser.add_argument("--repository", default=None)
    parser.add_argument("--branch", default=None)
    parser.add_argument("--git-provider", default=None)
    parser.add_argument("--artifact-dir", default="run-artifacts")

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
    artifact_store = ArtifactStore(args.artifact_dir)
    llm = OpenAICompatibleLLMBackend(args.direct_llm_endpoint, args.direct_llm_model, api_key=args.direct_llm_api_key)
    openhands_instance = OpenHandsInstance(
        args.openhands_endpoint,
        api_key=args.openhands_api_key,
        default_model=args.openhands_model,
        reuse_sandbox=args.reuse,
        sandbox_id=args.sandbox_id,
        conversation_id=args.conversation_id,
    )
    openhands_adapter = OpenHandsAdapter(openhands_instance, artifact_store)
    controller = WorkflowController(
        llm_backend=llm,
        openhands_adapter=openhands_adapter,
        artifact_root=Path(args.artifact_dir),
        approval_provider=StaticApprovalProvider(approve=args.auto_approve, reviewer="cli"),
    )
    task = Task(title=args.title, description=args.task, repository=args.repository, branch=args.branch, git_provider=args.git_provider)
    report = await controller.run(task)
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run(build_parser().parse_args())))

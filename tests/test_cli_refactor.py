from __future__ import annotations

import pytest

from artifact_workflow_runtime.cli import build_parser


def _base_args() -> list[str]:
    return [
        "--task", "inspect repo metacoma/freeplane_plugin_grpc",
        "--direct-llm-endpoint", "http://llm",
        "--direct-llm-model", "openai/reasoner",
        "--openhands-endpoint", "http://openhands",
        "--openhands-model", "openai/executor",
    ]


def test_repository_flags_removed_from_cli() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args([*_base_args(), "--repository", "owner/repo"])


def test_task_text_is_the_only_target_channel() -> None:
    args = build_parser().parse_args(_base_args())
    assert args.task == "inspect repo metacoma/freeplane_plugin_grpc"
    assert not hasattr(args, "repository")
    assert not hasattr(args, "branch")
    assert not hasattr(args, "git_provider")

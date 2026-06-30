from __future__ import annotations

import pytest
from types import SimpleNamespace


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


def test_strategy_selection_mode_defaults_to_rule_based() -> None:
    args = build_parser().parse_args(_base_args())
    assert args.strategy_selection_mode == "rule_based"


def test_strategy_selection_mode_accepts_hybrid() -> None:
    args = build_parser().parse_args([*_base_args(), "--strategy-selection-mode", "hybrid"])
    assert args.strategy_selection_mode == "hybrid"


def test_strategy_selection_mode_rejects_invalid_value() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args([*_base_args(), "--strategy-selection-mode", "magic"])


@pytest.mark.asyncio
async def test_cli_passes_strategy_selection_mode_to_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from artifact_workflow_runtime import cli

    seen = {}

    class FakeController:
        async def run(self, task):
            return SimpleNamespace(model_dump=lambda mode="json": {"task_id": task.id, "status": "ok"})

    def fake_build_controller(**kwargs):
        seen.update(kwargs)
        return FakeController()

    monkeypatch.setattr(cli, "build_controller", fake_build_controller)
    args = build_parser().parse_args([*_base_args(), "--artifact-dir", str(tmp_path), "--strategy-selection-mode", "hybrid"])

    rc = await cli._run(args)

    assert rc == 0
    assert seen["strategy_selection_mode"] == "hybrid"

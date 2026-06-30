from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="artifact-workflow-tui")
    parser.add_argument("--task", default=None, help="Initial task text to prefill in the TUI")
    parser.add_argument("--artifact-dir", default="run-artifacts")
    parser.add_argument("--config", default=None, help="YAML model routing config with stage-based direct_llm/openhands mappings")

    parser.add_argument("--direct-llm-endpoint", default="http://127.0.0.1:4000/v1")
    parser.add_argument("--direct-llm-model", default="qwen36-35b")
    parser.add_argument("--direct-llm-api-key", default="sk-local")

    parser.add_argument("--openhands-endpoint", default="http://127.0.0.1:3000")
    parser.add_argument("--openhands-model", default="qwen36-35b")
    parser.add_argument("--openhands-api-key", default=None)
    parser.add_argument("--reuse", action="store_true", default=False)
    parser.add_argument("--sandbox-id", default=None)
    parser.add_argument("--conversation-id", default=None)

    parser.add_argument("--auto-approve", action="store_true", default=False)
    return parser


def main() -> None:
    try:
        from .app import run_tui
    except ModuleNotFoundError as exc:  # pragma: no cover - runtime guard
        if exc.name and exc.name.startswith("textual"):
            raise SystemExit(
                "Textual is not installed. Install the TUI extra with: pip install -e '.[tui]'"
            ) from exc
        raise

    args = build_parser().parse_args()
    initial_config = {
        "artifact_dir": args.artifact_dir,
        "config_path": args.config,
        "direct_llm_endpoint": args.direct_llm_endpoint,
        "direct_llm_model": args.direct_llm_model,
        "direct_llm_api_key": args.direct_llm_api_key,
        "openhands_endpoint": args.openhands_endpoint,
        "openhands_model": args.openhands_model,
        "openhands_api_key": args.openhands_api_key,
        "reuse": args.reuse,
        "sandbox_id": args.sandbox_id,
        "conversation_id": args.conversation_id,
        "auto_approve": args.auto_approve,
    }
    run_tui(initial_task=args.task, initial_config=initial_config)

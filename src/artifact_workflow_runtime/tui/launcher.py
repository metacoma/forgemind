from __future__ import annotations


def main() -> None:
    try:
        from .app import run_tui
    except ModuleNotFoundError as exc:  # pragma: no cover - runtime guard
        if exc.name and exc.name.startswith("textual"):
            raise SystemExit(
                "Textual is not installed. Install the TUI extra with: pip install -e '.[tui]'"
            ) from exc
        raise
    run_tui()

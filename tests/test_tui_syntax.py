from __future__ import annotations

import py_compile
from pathlib import Path


def test_tui_app_has_valid_python_syntax() -> None:
    app_path = Path(__file__).resolve().parents[1] / "src" / "artifact_workflow_runtime" / "tui" / "app.py"
    py_compile.compile(str(app_path), doraise=True)

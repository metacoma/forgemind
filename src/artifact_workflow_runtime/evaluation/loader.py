from __future__ import annotations

from pathlib import Path

import yaml

from .models import ScenarioSpec


def _load_path(path: Path) -> ScenarioSpec:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        data = yaml.safe_load(text)
    else:
        import json
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"Scenario file {path} did not contain a mapping")
    return ScenarioSpec.model_validate(data)


def load_scenario_spec(path: str | Path) -> ScenarioSpec:
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(file_path)
    return _load_path(file_path)


def load_scenarios(path: str | Path) -> list[ScenarioSpec]:
    root = Path(path)
    if root.is_file():
        return [load_scenario_spec(root)]
    if not root.exists():
        raise FileNotFoundError(root)
    scenarios: list[ScenarioSpec] = []
    for file_path in sorted(root.rglob("*")):
        if file_path.suffix.lower() not in {".yaml", ".yml", ".json"}:
            continue
        scenarios.append(_load_path(file_path))
    return scenarios

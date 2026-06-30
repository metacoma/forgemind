from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import os

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

DIRECT_LLM_SLOTS = ("classify", "route", "obligations", "plan", "verify")
OPENHANDS_SLOTS = ("observe", "research", "execute", "publish")


class ModelRoutingConfigError(ValueError):
    """Raised when the model routing YAML is invalid or unsupported."""


@dataclass(slots=True)
class ModelRoutingConfig:
    direct_llm: dict[str, str] = field(default_factory=dict)
    openhands: dict[str, str] = field(default_factory=dict)
    source_path: str | None = None

    def resolve_direct_llm(self, slot: str, default_model: str | None) -> str | None:
        value = _clean_model(self.direct_llm.get(slot))
        return value or default_model

    def resolve_openhands(self, slot: str, default_model: str | None) -> str | None:
        value = _clean_model(self.openhands.get(slot))
        return value or default_model

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "direct_llm": dict(self.direct_llm),
            "openhands": dict(self.openhands),
        }

    def summary_lines(self) -> list[str]:
        lines = []
        if self.source_path:
            lines.append(f"config: {self.source_path}")
        if self.direct_llm:
            lines.append("direct_llm:")
            for key in DIRECT_LLM_SLOTS:
                model = self.direct_llm.get(key)
                if model:
                    lines.append(f"  {key}: {model}")
        if self.openhands:
            lines.append("openhands:")
            for key in OPENHANDS_SLOTS:
                model = self.openhands.get(key)
                if model:
                    lines.append(f"  {key}: {model}")
        return lines or ["no per-stage model routing configured"]


def _clean_model(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_model_mapping(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for key, raw in value.items():
        slot = str(key).strip()
        model = _clean_model(raw)
        if slot and model:
            out[slot] = model
    return out


def _candidate_config_paths(explicit_path: str | None) -> list[Path]:
    candidates: list[Path] = []
    seen: set[str] = set()

    def add(path_text: str | None) -> None:
        if not path_text:
            return
        path = Path(path_text).expanduser()
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            return
        seen.add(key)
        candidates.append(path)

    add(explicit_path)
    for env_name in ("ARTIFACT_WORKFLOW_CONFIG", "FORGEMIND_CONFIG", "OPENHANDS_CONFIG"):
        add(os.getenv(env_name))
    for name in ("config.yml", "config.yaml", ".artifact-workflow-models.yml", ".forgemind-models.yml"):
        add(name)
    return candidates


def _raise_roles_error(candidate: Path) -> None:
    raise ModelRoutingConfigError(
        f"Unsupported model routing format in {candidate}: legacy 'roles:' mapping is no longer supported. "
        "Use stage-based 'direct_llm:' and 'openhands:' mappings instead."
    )


def load_model_routing_config(config_path: str | None = None) -> ModelRoutingConfig | None:
    if yaml is None:  # pragma: no cover - dependency guard
        return None
    for candidate in _candidate_config_paths(config_path):
        try:
            if not candidate.is_file():
                continue
            data = yaml.safe_load(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue

        if "roles" in data:
            _raise_roles_error(candidate)

        direct = _normalize_model_mapping(data.get("direct_llm"))
        openhands = _normalize_model_mapping(data.get("openhands"))

        models_block = data.get("models")
        if isinstance(models_block, dict):
            direct = {**_normalize_model_mapping(models_block.get("direct_llm")), **direct}
            openhands = {**_normalize_model_mapping(models_block.get("openhands")), **openhands}

        stages_block = data.get("stages")
        if isinstance(stages_block, dict):
            for slot, raw in stages_block.items():
                if not isinstance(raw, dict):
                    continue
                backend = str(raw.get("backend") or "").strip().lower()
                model = _clean_model(raw.get("model"))
                if not model:
                    continue
                if backend == "direct_llm":
                    direct[str(slot)] = model
                elif backend == "openhands":
                    openhands[str(slot)] = model

        resolved = ModelRoutingConfig(
            direct_llm=direct,
            openhands=openhands,
            source_path=str(candidate),
        )
        if resolved.direct_llm or resolved.openhands:
            return resolved
    return None

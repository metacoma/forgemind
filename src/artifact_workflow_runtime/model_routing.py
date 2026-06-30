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
VERIFICATION_CHECK_SLOTS = (
    "default",
    "build",
    "unit_tests",
    "integration_tests",
    "smoke_tests",
    "lint",
    "typecheck",
    "docs",
    "security",
    "pr_checks",
)


def normalize_verification_check_slot(check_name: object) -> str:
    """Map a human verification check name to a stable routing slot.

    Plans intentionally keep `verification_checks` human-readable. This helper
    creates a deterministic control-plane key so model routing does not depend
    on fragile exact prompt wording. Exact normalized custom keys are still
    supported by `ModelRoutingConfig.resolve_verification_check`.
    """
    text = str(check_name or "").strip().lower()
    normalized = "_".join(part for part in _split_key(text) if part)
    padded = f" {text.replace('-', ' ').replace('_', ' ')} "

    if any(marker in padded for marker in (" pr ", " pull request ", " github actions ", " ci ", " status checks ", " check run ")):
        return "pr_checks"
    if any(marker in padded for marker in (" integration ", " e2e ", " end to end ")):
        return "integration_tests"
    if any(marker in padded for marker in (" unit ", " pytest ", " go test ", " cargo test ")):
        return "unit_tests"
    if any(marker in padded for marker in (" smoke ", " sanity ")):
        return "smoke_tests"
    if any(marker in padded for marker in (" security ", " semgrep ", " trivy ", " sast ", " vulnerability ")):
        return "security"
    if any(marker in padded for marker in (" lint ", " ruff ", " eslint ", " flake8 ")):
        return "lint"
    if any(marker in padded for marker in (" typecheck ", " type check ", " mypy ", " tsc ", " pyright ")):
        return "typecheck"
    if any(marker in padded for marker in (" docs ", " documentation ", " readme ", " architecture ")):
        return "docs"
    if any(marker in padded for marker in (" build ", " compile ", " package ")):
        return "build"
    return normalized or "default"


def _split_key(value: str) -> list[str]:
    import re

    return re.split(r"[^a-z0-9]+", value.strip().lower())


class ModelRoutingConfigError(ValueError):
    """Raised when the model routing YAML is invalid or unsupported."""


@dataclass(slots=True)
class ModelRoutingConfig:
    direct_llm: dict[str, str] = field(default_factory=dict)
    openhands: dict[str, str] = field(default_factory=dict)
    verification_checks: dict[str, str] = field(default_factory=dict)
    source_path: str | None = None

    def resolve_direct_llm(self, slot: str, default_model: str | None) -> str | None:
        value = _clean_model(self.direct_llm.get(slot))
        return value or default_model

    def resolve_openhands(self, slot: str, default_model: str | None) -> str | None:
        value = _clean_model(self.openhands.get(slot))
        return value or default_model

    def resolve_verification_check(self, check_name: object, default_model: str | None) -> str | None:
        custom_key = "_".join(part for part in _split_key(str(check_name or "")))
        canonical_key = normalize_verification_check_slot(check_name)
        for key in (custom_key, canonical_key, "default"):
            value = _clean_model(self.verification_checks.get(key))
            if value:
                return value
        return self.resolve_direct_llm("verify", default_model)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "direct_llm": dict(self.direct_llm),
            "openhands": dict(self.openhands),
            "verification_checks": dict(self.verification_checks),
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
        if self.verification_checks:
            lines.append("verification_checks:")
            ordered_keys = [key for key in VERIFICATION_CHECK_SLOTS if key in self.verification_checks]
            ordered_keys.extend(sorted(key for key in self.verification_checks if key not in VERIFICATION_CHECK_SLOTS))
            for key in ordered_keys:
                lines.append(f"  {key}: {self.verification_checks[key]}")
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


def _normalize_verification_model_mapping(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for key, raw in value.items():
        model = _clean_model(raw)
        if not model:
            continue
        slot = normalize_verification_check_slot(key)
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
        verification_checks = _normalize_verification_model_mapping(data.get("verification_checks") or data.get("checks"))

        models_block = data.get("models")
        if isinstance(models_block, dict):
            direct = {**_normalize_model_mapping(models_block.get("direct_llm")), **direct}
            openhands = {**_normalize_model_mapping(models_block.get("openhands")), **openhands}
            verification_checks = {**_normalize_verification_model_mapping(models_block.get("verification_checks") or models_block.get("checks")), **verification_checks}

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
            verification_checks=verification_checks,
            source_path=str(candidate),
        )
        if resolved.direct_llm or resolved.openhands or resolved.verification_checks:
            return resolved
    return None

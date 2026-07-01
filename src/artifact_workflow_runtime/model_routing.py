from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import os

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

DEFAULT_CANONICAL_MODEL = "qwen36-35b"
SECONDARY_CANONICAL_MODEL = "qwen36-27b"
CANONICAL_MODEL_NAMES = (DEFAULT_CANONICAL_MODEL, SECONDARY_CANONICAL_MODEL)
OPENHANDS_TRANSPORT_PREFIX = "openai/"

DIRECT_LLM_SLOTS = (
    "classify",
    "route",
    "obligations",
    "plan",
    "verify",
    "execution_review",
    "acceptance",
    "finalize",
    "strategy",
)
OPENHANDS_SLOTS = (
    "observe",
    "research",
    "execute",
    "repair",
    "publish",
    "world_verify",
)
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
    """Map a human verification check name to a stable routing slot."""
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


def normalize_canonical_model_name(value: object) -> str | None:
    """Normalize config/user input into a canonical model id when possible.

    Canonical config values should be plain ids like ``qwen36-35b``. For
    compatibility, ``openai/qwen36-35b`` is accepted and normalized.
    Unknown values are preserved so callers can opt into custom models without
    changing the runtime code in multiple places.
    """

    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.startswith(OPENHANDS_TRANSPORT_PREFIX):
        candidate = text[len(OPENHANDS_TRANSPORT_PREFIX) :].strip()
        if candidate in CANONICAL_MODEL_NAMES:
            return candidate
    return text


def resolve_openhands_transport_model(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    canonical = normalize_canonical_model_name(text)
    if canonical is None:
        return None
    if canonical.startswith(OPENHANDS_TRANSPORT_PREFIX):
        return canonical
    return f"{OPENHANDS_TRANSPORT_PREFIX}{canonical}"


@dataclass(slots=True)
class ModelRoutingConfig:
    direct_llm: dict[str, str] = field(default_factory=dict)
    openhands: dict[str, str] = field(default_factory=dict)
    verification_checks: dict[str, str] = field(default_factory=dict)
    source_path: str | None = None
    default_direct_llm: str = DEFAULT_CANONICAL_MODEL
    default_openhands: str = DEFAULT_CANONICAL_MODEL
    default_verification_checks: str = DEFAULT_CANONICAL_MODEL

    def __post_init__(self) -> None:
        self.direct_llm = _normalize_model_mapping(self.direct_llm)
        self.openhands = _normalize_model_mapping(self.openhands)
        self.verification_checks = _normalize_verification_model_mapping(self.verification_checks)
        self.default_direct_llm = normalize_canonical_model_name(self.default_direct_llm) or DEFAULT_CANONICAL_MODEL
        self.default_openhands = normalize_canonical_model_name(self.default_openhands) or DEFAULT_CANONICAL_MODEL
        self.default_verification_checks = normalize_canonical_model_name(self.default_verification_checks) or DEFAULT_CANONICAL_MODEL

    @classmethod
    def defaults(cls) -> "ModelRoutingConfig":
        return cls()

    def resolve_direct_llm(self, slot: str, default_model: str | None = None) -> str:
        value = self.direct_llm.get(str(slot).strip())
        return normalize_canonical_model_name(value) or self.default_direct_llm or normalize_canonical_model_name(default_model) or DEFAULT_CANONICAL_MODEL

    def resolve_openhands(self, slot: str, default_model: str | None = None) -> str:
        value = self.openhands.get(str(slot).strip())
        return normalize_canonical_model_name(value) or self.default_openhands or normalize_canonical_model_name(default_model) or DEFAULT_CANONICAL_MODEL

    def resolve_openhands_transport(self, slot: str, default_model: str | None = None) -> str:
        return resolve_openhands_transport_model(self.resolve_openhands(slot, default_model)) or f"{OPENHANDS_TRANSPORT_PREFIX}{DEFAULT_CANONICAL_MODEL}"

    def resolve_verification_check(self, check_name: object, default_model: str | None = None) -> str:
        custom_key = "_".join(part for part in _split_key(str(check_name or "")))
        canonical_key = normalize_verification_check_slot(check_name)
        for key in (custom_key, canonical_key, "default"):
            value = normalize_canonical_model_name(self.verification_checks.get(key))
            if value:
                return value
        return self.default_verification_checks or normalize_canonical_model_name(default_model) or DEFAULT_CANONICAL_MODEL

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "default_direct_llm": self.default_direct_llm,
            "default_openhands": self.default_openhands,
            "default_verification_checks": self.default_verification_checks,
            "direct_llm": dict(self.direct_llm),
            "openhands": dict(self.openhands),
            "verification_checks": dict(self.verification_checks),
        }

    def summary_lines(self) -> list[str]:
        lines = []
        if self.source_path:
            lines.append(f"config: {self.source_path}")
        lines.append(f"defaults: direct_llm={self.default_direct_llm} openhands={self.default_openhands} verification_checks={self.default_verification_checks}")
        if self.direct_llm:
            lines.append("direct_llm:")
            for key in DIRECT_LLM_SLOTS:
                model = self.direct_llm.get(key)
                if model:
                    lines.append(f"  {key}: {model}")
            for key in sorted(k for k in self.direct_llm if k not in DIRECT_LLM_SLOTS):
                lines.append(f"  {key}: {self.direct_llm[key]}")
        if self.openhands:
            lines.append("openhands:")
            for key in OPENHANDS_SLOTS:
                model = self.openhands.get(key)
                if model:
                    lines.append(f"  {key}: {model}")
            for key in sorted(k for k in self.openhands if k not in OPENHANDS_SLOTS):
                lines.append(f"  {key}: {self.openhands[key]}")
        if self.verification_checks:
            lines.append("verification_checks:")
            ordered_keys = [key for key in VERIFICATION_CHECK_SLOTS if key in self.verification_checks]
            ordered_keys.extend(sorted(key for key in self.verification_checks if key not in VERIFICATION_CHECK_SLOTS))
            for key in ordered_keys:
                lines.append(f"  {key}: {self.verification_checks[key]}")
        return lines


def _normalize_model_mapping(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for key, raw in value.items():
        slot = str(key).strip()
        model = normalize_canonical_model_name(raw)
        if slot and model:
            out[slot] = model
    return out


def _normalize_verification_model_mapping(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for key, raw in value.items():
        model = normalize_canonical_model_name(raw)
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
                model = normalize_canonical_model_name(raw.get("model"))
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
        return resolved
    return None

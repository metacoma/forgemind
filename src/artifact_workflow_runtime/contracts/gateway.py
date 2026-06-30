from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from artifact_workflow_runtime.contracts.models import (
    ContractResult,
    ContractSpec,
    ContractViolation,
    ContractViolationSeverity,
)

T = TypeVar("T", bound=BaseModel)


class ContractViolationError(RuntimeError):
    """Controlled runtime error for invalid LLM contracts.

    This replaces raw Pydantic tracebacks in workflow execution. Callers can turn
    this into artifacts / human-review decisions without confusing schema drift
    with business logic failure.
    """

    def __init__(self, message: str, *, result: ContractResult) -> None:
        super().__init__(message)
        self.result = result


def extract_json(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        raise ValueError("LLM returned empty response")
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for idx, ch in enumerate(stripped):
            if ch not in "[{":
                continue
            try:
                obj, _ = decoder.raw_decode(stripped[idx:])
                return obj
            except json.JSONDecodeError:
                continue
        raise


class ContractGateway:
    """Schema-first gateway between Direct LLM JSON and engine typed models.

    Domain models stay canonical and strict. LLM schema drift is represented as a
    contract violation, optionally repaired by a bounded JSON-only retry.
    """

    def spec_for_model(self, response_model: type[BaseModel], *, max_repair_attempts: int = 1) -> ContractSpec:
        return ContractSpec(
            schema_id=f"{response_model.__module__}.{response_model.__name__}",
            response_model_name=response_model.__name__,
            json_schema=response_model.model_json_schema(),
            max_repair_attempts=max_repair_attempts,
        )

    def schema_prompt(self, spec: ContractSpec) -> str:
        schema_text = json.dumps(spec.json_schema, ensure_ascii=False, indent=2, sort_keys=True)
        return (
            "\n\n# Output contract\n"
            f"schema_id: {spec.schema_id}\n"
            f"schema_version: {spec.schema_version}\n"
            "Return exactly one JSON value matching this JSON schema. Do not include prose or markdown.\n"
            "JSON schema:\n"
            f"{schema_text}"
        )

    def validate_payload(self, payload: Any, response_model: type[T], spec: ContractSpec) -> tuple[T | None, ContractResult]:
        try:
            parsed = response_model.model_validate(payload)
        except ValidationError as exc:
            violations = self._violations_from_error(exc)
            return None, ContractResult(
                schema_id=spec.schema_id,
                schema_version=spec.schema_version,
                ok=False,
                raw_payload=payload,
                violations=violations,
            )
        return parsed, ContractResult(
            schema_id=spec.schema_id,
            schema_version=spec.schema_version,
            ok=True,
            raw_payload=payload,
            validated_payload=parsed.model_dump(mode="json"),
        )

    def build_repair_prompt(self, *, original_prompt: str, raw_text: str, result: ContractResult, spec: ContractSpec) -> str:
        violations = [violation.model_dump(mode="json") for violation in result.violations]
        schema_text = json.dumps(spec.json_schema, ensure_ascii=False, indent=2, sort_keys=True)
        return "\n".join([
            "# Contract repair request",
            "Your previous JSON did not satisfy the required contract.",
            spec.repair_instruction,
            "Return only valid JSON for the schema. Do not add prose or markdown.",
            "Do not change task meaning. Do not invent missing world facts.",
            "",
            "## Validation violations",
            json.dumps(violations, ensure_ascii=False, indent=2),
            "",
            "## Original raw response",
            raw_text,
            "",
            "## Original request context",
            original_prompt,
            "",
            "## Required JSON schema",
            schema_text,
        ])

    def failure_result(self, *, response_model: type[BaseModel], payload: Any, error: Exception) -> ContractResult:
        spec = self.spec_for_model(response_model)
        return ContractResult(
            schema_id=spec.schema_id,
            schema_version=spec.schema_version,
            ok=False,
            raw_payload=payload,
            violations=[
                ContractViolation(
                    path="$",
                    message=str(error),
                    expected=response_model.__name__,
                    actual=payload,
                    severity=ContractViolationSeverity.FATAL,
                )
            ],
        )

    @staticmethod
    def _violations_from_error(exc: ValidationError) -> list[ContractViolation]:
        violations: list[ContractViolation] = []
        for err in exc.errors(include_url=False):
            loc = ".".join(str(part) for part in err.get("loc", ())) or "$"
            actual = err.get("input")
            expected = str(err.get("type") or "schema")
            violations.append(
                ContractViolation(
                    path=loc,
                    message=str(err.get("msg") or "Validation failed"),
                    expected=expected,
                    actual=actual,
                    severity=ContractViolationSeverity.REPAIRABLE,
                )
            )
        return violations

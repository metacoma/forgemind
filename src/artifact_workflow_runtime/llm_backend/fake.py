from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, TypeVar

from pydantic import BaseModel

from artifact_workflow_runtime.contracts import ContractGateway, ContractViolationError
from artifact_workflow_runtime.models import BackendKind, LLMRequest, LLMResult

T = TypeVar("T", bound=BaseModel)


class ScriptedLLMBackend:
    """Deterministic Direct LLM test double.

    Scripts are keyed by `LLMRequest.kind`; each call consumes one payload.
    Invalid payloads are handled by the same ContractGateway as the real backend;
    if a second scripted payload exists, it is used as the bounded contract repair
    attempt instead of normalizing inside domain models.
    """

    def __init__(self, scripts: dict[str, list[dict[str, Any]]], *, model: str = "scripted-direct-llm", max_contract_repair_attempts: int = 1) -> None:
        self.scripts = {kind: list(items) for kind, items in scripts.items()}
        self.default_model = model
        self.calls: dict[str, list[LLMRequest]] = defaultdict(list)
        self.contract_gateway = ContractGateway()
        self.max_contract_repair_attempts = max_contract_repair_attempts

    async def complete_json(self, request: LLMRequest, response_model: type[T]) -> tuple[LLMResult, T]:
        if request.backend != BackendKind.DIRECT_LLM:
            raise ValueError(f"Scripted Direct LLM only accepts backend=direct_llm requests, got {request.backend}")
        self.calls[request.kind].append(request)
        payload = self._pop_payload(request.kind)
        spec = self.contract_gateway.spec_for_model(response_model, max_repair_attempts=self.max_contract_repair_attempts)
        parsed, contract_result = self.contract_gateway.validate_payload(payload, response_model, spec)
        attempts = 0
        repair_raw_texts: list[str] = []
        while parsed is None and attempts < spec.max_repair_attempts and self.scripts.get(request.kind):
            attempts += 1
            repair_payload = self._pop_payload(request.kind)
            repair_raw = json.dumps(repair_payload, ensure_ascii=False, indent=2)
            repair_raw_texts.append(repair_raw)
            parsed, contract_result = self.contract_gateway.validate_payload(repair_payload, response_model, spec)
            contract_result.repair_attempts = attempts
            contract_result.repaired = parsed is not None
            contract_result.repair_raw_texts = list(repair_raw_texts)
        if parsed is None:
            contract_result.repair_attempts = attempts
            contract_result.repair_raw_texts = list(repair_raw_texts)
            raise ContractViolationError(
                f"Scripted LLM response did not satisfy contract {spec.schema_id} after {attempts} repair attempt(s)",
                result=contract_result,
            )
        raw_text = json.dumps(parsed.model_dump(mode="json"), ensure_ascii=False, indent=2)
        result = LLMResult(
            request_id=request.id,
            ok=True,
            model=str(request.metadata.get("model_override") or self.default_model),
            backend="scripted_direct_llm",
            raw_text=raw_text,
            parsed=parsed.model_dump(mode="json"),
            contract_result=contract_result.model_dump(mode="json"),
        )
        return result, parsed

    def _pop_payload(self, kind: str) -> dict[str, Any]:
        queue = self.scripts.get(kind)
        if not queue:
            payload = self._default_payload(kind)
            if payload is None:
                raise RuntimeError(f"No scripted LLM response for {kind}")
            return payload
        return queue.pop(0)

    @staticmethod
    def _default_payload(kind: str) -> dict[str, Any] | None:
        if kind == "obligation_analysis":
            return {
                "required_test_levels": [],
                "required_setup_steps": [],
                "required_environment_conditions": ["docker_container"],
                "required_documentation_updates": [],
                "required_examples_updates": [],
                "required_ci_updates": [],
                "required_codegen_or_build_updates": [],
                "affected_surfaces": [],
                "adjacent_components": [],
                "discovered_impacts": [],
                "work_surface": None,
                "required_publish_actions": [],
                "completion_requirements": [],
                "blocker_conditions": [],
                "reasoning_summary": "No scripted obligation analysis was provided; using an empty test/publish obligation set for this test double.",
            }
        return None

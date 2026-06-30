from __future__ import annotations

import json
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from artifact_workflow_runtime.contracts import ContractGateway, ContractViolationError, extract_json
from artifact_workflow_runtime.models import BackendKind, LLMRequest, LLMResult

T = TypeVar("T", bound=BaseModel)


class OpenAICompatibleLLMBackend:
    def __init__(self, endpoint: str, model: str, *, api_key: str | None = None, timeout: float = 180.0, max_contract_repair_attempts: int = 1) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.default_model = model
        self.api_key = api_key
        self.timeout = timeout
        self.contract_gateway = ContractGateway()
        self.max_contract_repair_attempts = max_contract_repair_attempts

    async def complete_json(self, request: LLMRequest, response_model: type[T]) -> tuple[LLMResult, T]:
        if request.backend != BackendKind.DIRECT_LLM:
            raise ValueError(f"Direct LLM backend only accepts backend=direct_llm requests, got {request.backend}")
        forbidden = {item.strip().lower() for item in request.forbidden_inputs}
        missing_world_guards = {"filesystem", "shell", "git", "hosts", "kubernetes", "network_runtime_state"} - forbidden
        if missing_world_guards:
            raise ValueError(f"Direct LLM request is missing world-access guards: {sorted(missing_world_guards)}")
        model_name = str(request.metadata.get("model_override") or self.default_model)
        spec = self.contract_gateway.spec_for_model(response_model, max_repair_attempts=self.max_contract_repair_attempts)
        user_prompt = request.compiled_prompt() + self.contract_gateway.schema_prompt(spec)

        raw_text = await self._complete_raw(model_name=model_name, user_prompt=user_prompt)
        try:
            payload = extract_json(raw_text)
        except Exception as exc:
            contract_result = self.contract_gateway.failure_result(response_model=response_model, payload=raw_text, error=exc)
            parsed = None
        else:
            parsed, contract_result = self.contract_gateway.validate_payload(payload, response_model, spec)

        repair_raw_texts: list[str] = []
        attempts = 0
        while parsed is None and attempts < spec.max_repair_attempts and all(v.severity.value == "repairable" for v in contract_result.violations):
            attempts += 1
            repair_prompt = self.contract_gateway.build_repair_prompt(
                original_prompt=user_prompt,
                raw_text=raw_text,
                result=contract_result,
                spec=spec,
            )
            repair_text = await self._complete_raw(model_name=model_name, user_prompt=repair_prompt, repair=True)
            repair_raw_texts.append(repair_text)
            try:
                repair_payload = extract_json(repair_text)
            except Exception as exc:
                contract_result = self.contract_gateway.failure_result(response_model=response_model, payload=repair_text, error=exc)
                continue
            parsed, contract_result = self.contract_gateway.validate_payload(repair_payload, response_model, spec)
            contract_result.repair_attempts = attempts
            contract_result.repaired = parsed is not None
            contract_result.repair_raw_texts = list(repair_raw_texts)
            if parsed is not None:
                raw_text = repair_text
                break

        if parsed is None:
            contract_result.repair_attempts = attempts
            contract_result.repair_raw_texts = list(repair_raw_texts)
            raise ContractViolationError(
                f"LLM response did not satisfy contract {spec.schema_id} after {attempts} repair attempt(s)",
                result=contract_result,
            )

        result = LLMResult(
            request_id=request.id,
            ok=True,
            model=model_name,
            backend="openai_compatible",
            raw_text=raw_text,
            parsed=parsed.model_dump(mode="json"),
            contract_result=contract_result.model_dump(mode="json"),
        )
        return result, parsed

    async def _complete_raw(self, *, model_name: str, user_prompt: str, repair: bool = False) -> str:
        system = (
            "You are a precise contract-bound workflow component. Return only valid JSON matching the requested schema. "
            "Do not wrap the JSON in markdown fences."
        )
        if repair:
            system = (
                "You are a JSON contract repair component. Return only corrected JSON matching the provided schema. "
                "Do not add prose or markdown. Do not change task meaning."
            )
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
        }
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.endpoint}/chat/completions", headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        return _extract_content(data)


def _extract_content(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts).strip()
    return str(content or "").strip()


# Backwards-compatible import target for older tests/extensions.
def _extract_json(text: str) -> Any:
    return extract_json(text)

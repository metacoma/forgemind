from __future__ import annotations

import json
from typing import Any, Type

import httpx
from pydantic import BaseModel

from artifact_workflow_runtime.llm_backend.base import DirectLLMBackend
from artifact_workflow_runtime.models import LLMRequest, LLMResult


class OpenAICompatibleLLMBackend(DirectLLMBackend):
    name = "openai_compatible"

    def __init__(self, endpoint: str, model: str, api_key: str | None = None, timeout: float = 120.0) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    @property
    def headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def _raw_complete(self, prompt: str) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.endpoint}/chat/completions", headers=self.headers, json=payload)
            response.raise_for_status()
            data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("Direct LLM returned no choices")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(str(item.get("text") or "") for item in content if isinstance(item, dict))
        raise RuntimeError(f"Unsupported content shape: {content!r}")

    async def complete_json(self, request: LLMRequest, response_model: Type[BaseModel]) -> tuple[LLMResult, BaseModel]:
        raw_text = await self._raw_complete(request.prompt)
        parsed_payload = json.loads(raw_text)
        parsed_model = response_model.model_validate(parsed_payload)
        result = LLMResult(
            request_id=request.id,
            ok=True,
            model=self.model,
            backend=self.name,
            raw_text=raw_text,
            parsed=parsed_model.model_dump(mode="json"),
        )
        return result, parsed_model

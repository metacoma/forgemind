from __future__ import annotations

import json
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from artifact_workflow_runtime.models import LLMRequest, LLMResult

T = TypeVar("T", bound=BaseModel)


class OpenAICompatibleLLMBackend:
    def __init__(self, endpoint: str, model: str, *, api_key: str | None = None, timeout: float = 180.0) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.default_model = model
        self.api_key = api_key
        self.timeout = timeout

    async def complete_json(self, request: LLMRequest, response_model: type[T]) -> tuple[LLMResult, T]:
        model_name = str(request.metadata.get("model_override") or self.default_model)
        payload = {
            "model": model_name,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a precise workflow component. Return only valid JSON matching the requested schema. "
                        "Do not wrap the JSON in markdown fences."
                    ),
                },
                {"role": "user", "content": request.prompt},
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
        raw_text = _extract_content(data)
        parsed_payload = _extract_json(raw_text)
        parsed_model = response_model.model_validate(parsed_payload)
        result = LLMResult(
            request_id=request.id,
            ok=True,
            model=model_name,
            backend="openai_compatible",
            raw_text=raw_text,
            parsed=parsed_model.model_dump(mode="json"),
        )
        return result, parsed_model


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


def _extract_json(text: str) -> Any:
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

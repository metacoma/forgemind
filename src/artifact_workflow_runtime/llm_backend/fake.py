from __future__ import annotations

import json
from collections import defaultdict
from typing import Type

from pydantic import BaseModel

from artifact_workflow_runtime.llm_backend.base import DirectLLMBackend
from artifact_workflow_runtime.models import LLMRequest, LLMResult


class ScriptedLLMBackend(DirectLLMBackend):
    name = "scripted"

    def __init__(self, scripts: dict[str, list[dict]]) -> None:
        self.scripts = {key: list(value) for key, value in scripts.items()}
        self.seen_requests: dict[str, list[LLMRequest]] = defaultdict(list)

    async def complete_json(self, request: LLMRequest, response_model: Type[BaseModel]) -> tuple[LLMResult, BaseModel]:
        self.seen_requests[request.kind].append(request)
        queue = self.scripts.get(request.kind)
        if not queue:
            raise RuntimeError(f"No scripted response for request kind {request.kind!r}")
        payload = queue.pop(0)
        parsed_model = response_model.model_validate(payload)
        raw_text = json.dumps(payload, ensure_ascii=False)
        result = LLMResult(
            request_id=request.id,
            ok=True,
            model="scripted-model",
            backend=self.name,
            raw_text=raw_text,
            parsed=parsed_model.model_dump(mode="json"),
        )
        return result, parsed_model

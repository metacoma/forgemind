from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, TypeVar

from pydantic import BaseModel

from artifact_workflow_runtime.models import BackendKind, LLMRequest, LLMResult

T = TypeVar("T", bound=BaseModel)


class ScriptedLLMBackend:
    """Deterministic Direct LLM test double.

    Scripts are keyed by `LLMRequest.kind`; each call consumes exactly one
    payload and validates it against the requested pydantic response model.
    """

    def __init__(self, scripts: dict[str, list[dict[str, Any]]], *, model: str = "scripted-direct-llm") -> None:
        self.scripts = {kind: list(items) for kind, items in scripts.items()}
        self.default_model = model
        self.calls: dict[str, list[LLMRequest]] = defaultdict(list)

    async def complete_json(self, request: LLMRequest, response_model: type[T]) -> tuple[LLMResult, T]:
        if request.backend != BackendKind.DIRECT_LLM:
            raise ValueError(f"Scripted Direct LLM only accepts backend=direct_llm requests, got {request.backend}")
        self.calls[request.kind].append(request)
        queue = self.scripts.get(request.kind)
        if not queue:
            payload = self._default_payload(request.kind)
            if payload is None:
                raise RuntimeError(f"No scripted LLM response for {request.kind}")
        else:
            payload = queue.pop(0)
        parsed = response_model.model_validate(payload)
        raw_text = json.dumps(parsed.model_dump(mode="json"), ensure_ascii=False, indent=2)
        result = LLMResult(
            request_id=request.id,
            ok=True,
            model=str(request.metadata.get("model_override") or self.default_model),
            backend="scripted_direct_llm",
            raw_text=raw_text,
            parsed=parsed.model_dump(mode="json"),
        )
        return result, parsed

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

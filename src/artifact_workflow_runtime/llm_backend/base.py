from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Type

from pydantic import BaseModel

from artifact_workflow_runtime.models import LLMRequest, LLMResult


class DirectLLMBackend(ABC):
    name: str = "direct_llm"

    @abstractmethod
    async def complete_json(self, request: LLMRequest, response_model: Type[BaseModel]) -> tuple[LLMResult, BaseModel]:
        raise NotImplementedError

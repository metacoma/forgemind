from __future__ import annotations

from dataclasses import dataclass

from .client import OpenHandsClient, run_conversation_and_collect
from .models import OpenHandsRunResult


@dataclass(frozen=True)
class OpenHandsConversation:
    endpoint: str
    api_key: str | None
    client: OpenHandsClient
    conversation_id: str

    async def run_prompt(self, prompt: str, *, model: str | None = None, repository: str | None = None, branch: str | None = None, git_provider: str | None = None) -> OpenHandsRunResult:
        return await run_conversation_and_collect(
            endpoint=self.endpoint,
            api_key=self.api_key,
            prompt=prompt,
            llm_model=model,
            selected_repository=repository,
            selected_branch=branch,
            git_provider=git_provider,
        )

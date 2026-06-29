from __future__ import annotations

from .client import OpenHandsClient, find_reusable_sandbox_for_model, run_conversation_and_collect
from .models import OpenHandsRunResult


class OpenHandsInstance:
    def __init__(
        self,
        endpoint: str,
        *,
        api_key: str | None = None,
        default_model: str | None = None,
        reuse_sandbox: bool = False,
        sandbox_id: str | None = None,
        conversation_id: str | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.default_model = default_model
        self.reuse_sandbox = reuse_sandbox
        self.explicit_sandbox_id = sandbox_id
        self.explicit_conversation_id = conversation_id
        self._sandbox_cache: dict[str, str] = {}
        self._resolved_sandbox_id: str | None = sandbox_id

    async def _resolve_sandbox_id(self, *, model: str | None) -> str | None:
        if self.explicit_sandbox_id:
            return self.explicit_sandbox_id
        if not self.reuse_sandbox:
            return None
        if self._resolved_sandbox_id:
            return self._resolved_sandbox_id
        client = OpenHandsClient(self.endpoint, api_key=self.api_key)
        sandbox_id = await find_reusable_sandbox_for_model(
            client,
            model=model or self.default_model,
            sandbox_cache=self._sandbox_cache,
        )
        if sandbox_id:
            self._resolved_sandbox_id = sandbox_id
        return sandbox_id

    async def run(
        self,
        *,
        prompt: str,
        model: str | None = None,
        repository: str | None = None,
        branch: str | None = None,
        git_provider: str | None = None,
        title: str | None = None,
    ) -> OpenHandsRunResult:
        resolved_sandbox_id = await self._resolve_sandbox_id(model=model)
        result = await run_conversation_and_collect(
            endpoint=self.endpoint,
            api_key=self.api_key,
            prompt=prompt,
            llm_model=model or self.default_model,
            selected_repository=repository,
            selected_branch=branch,
            git_provider=git_provider,
            sandbox_id=resolved_sandbox_id,
            conversation_id=self.explicit_conversation_id,
            title=title,
        )
        if self.reuse_sandbox and result.start.sandbox_id:
            self._resolved_sandbox_id = result.start.sandbox_id
            resolved_model = model or self.default_model
            if resolved_model:
                self._sandbox_cache[resolved_model] = result.start.sandbox_id
        return result

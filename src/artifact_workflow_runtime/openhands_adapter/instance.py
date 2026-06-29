from __future__ import annotations

from .client import (
    AppConversationStart,
    OpenHandsClient,
    find_reusable_sandbox_for_model,
    run_conversation_and_collect,
    run_followup_message_and_collect,
)
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
        self._active_conversation: AppConversationStart | None = None
        self._seen_event_ids: set[str] = set()

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

    async def _run_followup(
        self,
        *,
        prompt: str,
        model: str | None = None,
    ) -> OpenHandsRunResult:
        conversation = self._active_conversation
        if conversation is None:
            raise RuntimeError("No active OpenHands conversation to continue")
        result = await run_followup_message_and_collect(
            endpoint=self.endpoint,
            api_key=self.api_key,
            conversation=conversation,
            prompt=prompt,
            known_event_ids=frozenset(self._seen_event_ids),
        )
        self._active_conversation = result.start
        self._seen_event_ids = set(result.seen_event_ids)
        if self.reuse_sandbox and result.start.sandbox_id:
            self._resolved_sandbox_id = result.start.sandbox_id
            resolved_model = model or self.default_model
            if resolved_model:
                self._sandbox_cache[resolved_model] = result.start.sandbox_id
        return result

    async def _run_new(
        self,
        *,
        prompt: str,
        model: str | None = None,
        title: str | None = None,
    ) -> OpenHandsRunResult:
        resolved_sandbox_id = await self._resolve_sandbox_id(model=model)
        result = await run_conversation_and_collect(
            endpoint=self.endpoint,
            api_key=self.api_key,
            prompt=prompt,
            llm_model=model or self.default_model,
            sandbox_id=resolved_sandbox_id,
            conversation_id=self.explicit_conversation_id,
            title=title,
        )
        self._active_conversation = result.start
        self._seen_event_ids = set(result.seen_event_ids)
        if self.reuse_sandbox and result.start.sandbox_id:
            self._resolved_sandbox_id = result.start.sandbox_id
            resolved_model = model or self.default_model
            if resolved_model:
                self._sandbox_cache[resolved_model] = result.start.sandbox_id
        return result

    async def run(
        self,
        *,
        prompt: str,
        model: str | None = None,
        title: str | None = None,
    ) -> OpenHandsRunResult:
        if self._active_conversation is not None:
            return await self._run_followup(prompt=prompt, model=model)
        return await self._run_new(prompt=prompt, model=model, title=title)

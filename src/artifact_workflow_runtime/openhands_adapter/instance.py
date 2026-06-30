from __future__ import annotations

from artifact_workflow_runtime.runtime_events import EventSink, emit_event
from artifact_workflow_runtime.model_routing import ModelRoutingConfig

from .client import (
    OpenHandsClient,
    find_reusable_sandbox_for_model,
    run_conversation_and_collect,
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
        event_sink: EventSink | None = None,
        model_routing: ModelRoutingConfig | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.default_model = default_model
        self.reuse_sandbox = reuse_sandbox
        self.explicit_sandbox_id = sandbox_id
        self.explicit_conversation_id = conversation_id
        self.event_sink = event_sink
        self.model_routing = model_routing
        self._sandbox_cache: dict[str, str] = {}
        self._resolved_sandbox_id: str | None = sandbox_id

    async def _resolve_sandbox_id(self, *, model: str | None) -> str | None:
        if self.explicit_sandbox_id:
            await emit_event(self.event_sink, "sandbox_pinned", "transport", "Using pinned sandbox id", {"sandbox_id": self.explicit_sandbox_id, "mode": "pinned"})
            return self.explicit_sandbox_id
        if not self.reuse_sandbox:
            await emit_event(self.event_sink, "sandbox_reuse_disabled", "transport", "Sandbox reuse disabled", {"mode": "fresh"})
            return None
        if self._resolved_sandbox_id:
            await emit_event(self.event_sink, "sandbox_reuse_cache_hit", "transport", "Using cached reusable sandbox", {"sandbox_id": self._resolved_sandbox_id, "mode": "reuse"})
            return self._resolved_sandbox_id
        client = OpenHandsClient(self.endpoint, api_key=self.api_key)
        sandbox_id = await find_reusable_sandbox_for_model(
            client,
            model=model or self.default_model,
            sandbox_cache=self._sandbox_cache,
        )
        if sandbox_id:
            self._resolved_sandbox_id = sandbox_id
            await emit_event(self.event_sink, "sandbox_reuse_found", "transport", "Found reusable sandbox", {"sandbox_id": sandbox_id, "model": model or self.default_model, "mode": "reuse"})
        else:
            await emit_event(self.event_sink, "sandbox_reuse_miss", "transport", "No reusable sandbox found; starting fresh", {"model": model or self.default_model, "mode": "fresh"})
        return sandbox_id

    async def _run_new(
        self,
        *,
        prompt: str,
        model: str | None = None,
        title: str | None = None,
    ) -> OpenHandsRunResult:
        resolved_sandbox_id = await self._resolve_sandbox_id(model=model)
        await emit_event(
            self.event_sink,
            "conversation_starting",
            "transport",
            "Starting new OpenHands conversation",
            {
                "sandbox_id": resolved_sandbox_id,
                "conversation_id": self.explicit_conversation_id,
                "model": model or self.default_model,
                "mode": "new",
                "reuse_sandbox": bool(resolved_sandbox_id),
            },
        )
        result = await run_conversation_and_collect(
            endpoint=self.endpoint,
            api_key=self.api_key,
            prompt=prompt,
            llm_model=model or self.default_model,
            sandbox_id=resolved_sandbox_id,
            conversation_id=self.explicit_conversation_id,
            title=title,
            event_sink=self.event_sink,
        )
        await emit_event(
            self.event_sink,
            "conversation_started",
            "transport",
            "OpenHands conversation started",
            {
                "conversation_id": result.start.conversation_id,
                "sandbox_id": result.start.sandbox_id,
                "last_status": result.status,
                "mode": "new",
                "websocket_url": result.start.conversation_url or result.start.agent_server_url or self.endpoint,
                "session_api_key": bool(result.start.session_api_key),
            },
        )
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
        return await self._run_new(prompt=prompt, model=model, title=title)

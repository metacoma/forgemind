from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlencode

import httpx
import websockets

from .models import AppConversationStart, JsonDict, OpenHandsRunResult

SECRET_FIELD_NAMES = {"secrets", "secret", "api_key", "token", "password", "key"}
TERMINAL_EXECUTION_STATES = {"finished", "error", "stuck", "waiting_for_confirmation"}


class OpenHandsError(RuntimeError):
    pass


class OpenHandsHTTPError(OpenHandsError):
    def __init__(self, method: str, path: str, response: httpx.Response) -> None:
        super().__init__(f"{method} {path} failed with {response.status_code}: {response.text[:500]}")
        self.response = response


def load_json_value(value: str, *, source: str = "JSON") -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise OpenHandsError(f"Invalid {source}: {exc.msg} at char {exc.pos}") from exc


def load_json_object(value: str, *, source: str = "JSON") -> JsonDict:
    data = load_json_value(value, source=source)
    if not isinstance(data, dict):
        raise OpenHandsError(f"{source} must be a JSON object")
    return data


def parse_key_value(raw: str, *, option: str) -> tuple[str, str]:
    if "=" not in raw:
        raise OpenHandsError(f"{option} expects KEY=VALUE")
    key, value = raw.split("=", 1)
    key = key.strip()
    if not key:
        raise OpenHandsError(f"{option} has empty KEY")
    return key, value


def set_if_not_none(payload: JsonDict, key: str, value: Any) -> None:
    if value is not None:
        payload[key] = value


def redact_secrets(value: Any, *, parent_key: str | None = None) -> Any:
    if isinstance(value, dict):
        out: JsonDict = {}
        for key, item in value.items():
            key_l = str(key).lower()
            if parent_key == "secrets" or any(marker in key_l for marker in SECRET_FIELD_NAMES):
                out[key] = "**********"
            else:
                out[key] = redact_secrets(item, parent_key=key_l)
        return out
    if isinstance(value, list):
        return [redact_secrets(item, parent_key=parent_key) for item in value]
    return value


def build_initial_message_from_prompt(prompt: str) -> JsonDict:
    return {"content": [{"type": "text", "text": prompt}]}


def build_app_conversation_payload(
    *,
    prompt: str | None = None,
    llm_model: str | None = None,
    selected_repository: str | None = None,
    selected_branch: str | None = None,
    git_provider: str | None = None,
    sandbox_id: str | None = None,
    conversation_id: str | None = None,
    title: str | None = None,
    payload_json: str | None = None,
    param_json: list[str] | None = None,
    secret: list[str] | None = None,
) -> JsonDict:
    payload: JsonDict = {}
    if payload_json:
        payload.update(load_json_object(payload_json, source="payload_json"))
    for raw in param_json or []:
        key, value = parse_key_value(raw, option="param_json")
        payload[key] = load_json_value(value, source=f"param_json {key}")
    set_if_not_none(payload, "llm_model", llm_model)
    set_if_not_none(payload, "selected_repository", selected_repository)
    set_if_not_none(payload, "selected_branch", selected_branch)
    set_if_not_none(payload, "git_provider", git_provider)
    set_if_not_none(payload, "sandbox_id", sandbox_id)
    set_if_not_none(payload, "conversation_id", conversation_id)
    set_if_not_none(payload, "title", title)
    if prompt is not None:
        payload["initial_message"] = build_initial_message_from_prompt(prompt)
    if secret:
        secrets: JsonDict = {}
        for raw in secret:
            key, value = parse_key_value(raw, option="secret")
            secrets[key] = value
        payload["secrets"] = secrets
    if not payload:
        raise OpenHandsError("No app-conversation fields were provided")
    if "initial_message" not in payload:
        raise OpenHandsError("No initial_message was provided")
    return payload


class OpenHandsClient:
    def __init__(self, endpoint: str, *, api_key: str | None = None, timeout: float = 60.0) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    @property
    def headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            headers["X-Session-API-Key"] = self.api_key
        return headers

    def _headers_with_session_key(self, session_api_key: str | None) -> dict[str, str]:
        headers = dict(self.headers)
        if session_api_key:
            headers["X-Session-API-Key"] = session_api_key
        return headers

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.endpoint}{path if path.startswith('/') else '/' + path}"

    async def _request(self, method: str, path: str, *, json_body: JsonDict | None = None, params: JsonDict | None = None, headers: dict[str, str] | None = None) -> JsonDict | list[Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(method, self._url(path), json=json_body, params=params, headers=headers or self.headers)
        except httpx.TimeoutException as exc:
            raise OpenHandsError(f"{method} {path} timed out after {self.timeout}s") from exc
        except httpx.TransportError as exc:
            raise OpenHandsError(f"{method} {path} transport error: {exc}") from exc
        if response.status_code >= 400:
            raise OpenHandsHTTPError(method, path, response)
        if not response.content:
            return {}
        try:
            data = response.json()
        except ValueError:
            content_type = response.headers.get("content-type", "")
            text_body = response.text
            if "application/json" in content_type.lower():
                raise OpenHandsError(
                    f"{method} {path} returned invalid JSON despite content-type {content_type!r}: {text_body[:500]}"
                )
            return {"raw_text": text_body, "content_type": content_type or None}
        if isinstance(data, (dict, list)):
            return data
        return {"value": data}

    async def create_app_conversation(self, payload: JsonDict) -> JsonDict:
        data = await self._request("POST", "/api/v1/app-conversations", json_body=payload)
        if not isinstance(data, dict):
            raise OpenHandsError(f"Unexpected response: {data!r}")
        return data

    async def get_start_task(self, task_id: str) -> JsonDict | None:
        data = await self._request("GET", "/api/v1/app-conversations/start-tasks", params={"ids": task_id})
        if isinstance(data, list):
            return data[0] if data else None
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            items = data["items"]
            return items[0] if items else None
        return data if isinstance(data, dict) else None

    async def wait_start_task_ready(self, task: JsonDict, *, poll_interval: float = 1.0, max_attempts: int = 120) -> JsonDict:
        task_id = str(task.get("id") or "")
        if not task_id:
            raise OpenHandsError("OpenHands did not return a task id")
        current = task
        for _ in range(max_attempts):
            if current.get("status") == "READY" and current.get("app_conversation_id"):
                return current
            if current.get("status") == "ERROR":
                raise OpenHandsError(f"OpenHands start task failed: {current}")
            await asyncio.sleep(poll_interval)
            next_task = await self.get_start_task(task_id)
            if next_task:
                current = next_task
        raise OpenHandsError(f"Timed out waiting for start task {task_id}")

    async def get_app_conversation(self, conversation_id: str) -> JsonDict | None:
        data = await self._request("GET", "/api/v1/app-conversations", params={"ids": conversation_id})
        if isinstance(data, list):
            return data[0] if data else None
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            items = data["items"]
            return items[0] if items else None
        return data if isinstance(data, dict) else None

    async def search_app_conversations(
        self,
        *,
        sandbox_id__eq: str | None = None,
        limit: int = 100,
        page_id: str | None = None,
        include_sub_conversations: bool = False,
    ) -> JsonDict:
        params: dict[str, str | int | bool] = {
            "limit": limit,
            "include_sub_conversations": include_sub_conversations,
        }
        if sandbox_id__eq:
            params["sandbox_id__eq"] = sandbox_id__eq
        if page_id:
            params["page_id"] = page_id
        data = await self._request("GET", "/api/v1/app-conversations/search", params=params)
        if isinstance(data, dict):
            return data
        return {"items": data}

    async def update_app_conversation(self, conversation_id: str, patch: JsonDict) -> JsonDict | None:
        if not patch:
            return None
        data = await self._request("PATCH", f"/api/v1/app-conversations/{conversation_id}", json_body=patch)
        return data if isinstance(data, dict) else None

    async def start_app_conversation(self, payload: JsonDict, *, poll_interval: float = 1.0, max_start_attempts: int = 120) -> AppConversationStart:
        start_task = await self.create_app_conversation(payload)
        ready = await self.wait_start_task_ready(start_task, poll_interval=poll_interval, max_attempts=max_start_attempts)
        conversation_id = str(ready.get("app_conversation_id") or "")
        if not conversation_id:
            raise OpenHandsError(f"Start task has no conversation id: {ready}")
        record = await self.get_app_conversation(conversation_id)
        if payload.get("title"):
            await self.update_app_conversation(conversation_id, {"title": payload["title"]})
            record = await self.get_app_conversation(conversation_id)
        record = record or {}
        return AppConversationStart(
            conversation_id=conversation_id,
            task_id=str(ready.get("id") or "") or None,
            status=str(ready.get("status") or "") or None,
            sandbox_id=record.get("sandbox_id"),
            agent_server_url=ready.get("agent_server_url") or record.get("agent_server_url"),
            conversation_url=ready.get("conversation_url") or record.get("conversation_url"),
            session_api_key=ready.get("session_api_key") or record.get("session_api_key"),
            raw_task=ready,
            raw_conversation=record,
        )

    async def _refresh_app_conversation_start_metadata(self, start: AppConversationStart) -> AppConversationStart:
        record = await self.get_app_conversation(start.conversation_id)
        if not record:
            return start
        return start.model_copy(update={
            "sandbox_id": record.get("sandbox_id") or start.sandbox_id,
            "agent_server_url": record.get("agent_server_url") or start.agent_server_url,
            "conversation_url": record.get("conversation_url") or start.conversation_url,
            "session_api_key": record.get("session_api_key") or start.session_api_key,
            "raw_conversation": record,
        })

    async def send_message_to_existing_conversation(self, start: AppConversationStart, prompt: str, *, run: bool = True) -> JsonDict | list[Any]:
        start = await self._refresh_app_conversation_start_metadata(start)
        path = f"/api/conversations/{start.conversation_id}/events"
        payload = {
            "role": "user",
            "content": [{"type": "text", "text": prompt}],
            "run": run,
        }
        return await self._request("POST", path, json_body=payload, headers=self._headers_with_session_key(start.session_api_key))

    async def fetch_final_text_fallback(self, start: AppConversationStart) -> str:
        path = f"/api/conversations/{start.conversation_id}/messages"
        try:
            data = await self._request("GET", path, headers=self._headers_with_session_key(start.session_api_key))
        except OpenHandsError:
            return ""
        if isinstance(data, dict):
            raw_text = data.get("raw_text")
            if isinstance(raw_text, str) and raw_text.strip():
                return raw_text.strip()
        messages = data if isinstance(data, list) else data.get("messages") if isinstance(data, dict) else []
        final = ""
        if isinstance(messages, list):
            for item in messages:
                text = extract_assistant_result_text(item if isinstance(item, dict) else {})
                if text.strip():
                    final = text
        return final

    def _ws_urls(self, start: AppConversationStart) -> list[str]:
        base = start.agent_server_url or self.endpoint
        if base.startswith("http://"):
            base = "ws://" + base[len("http://"):]
        elif base.startswith("https://"):
            base = "wss://" + base[len("https://"):]
        query = urlencode({"conversation_id": start.conversation_id})
        return [
            f"{base.rstrip('/')}/sockets/events/{start.conversation_id}?{query}",
            f"{base.rstrip('/')}/sockets/events/{start.conversation_id}",
        ]

    async def stream_v1_events(self, start: AppConversationStart, *, open_timeout: float = 20.0, retry_seconds: float = 30.0) -> AsyncIterator[JsonDict]:
        start = await self._refresh_app_conversation_start_metadata(start)
        deadline = asyncio.get_running_loop().time() + max(0.1, retry_seconds)
        last_exc: Exception | None = None
        while asyncio.get_running_loop().time() < deadline:
            for ws_url in self._ws_urls(start):
                try:
                    async with websockets.connect(ws_url, additional_headers=self._headers_with_session_key(start.session_api_key), open_timeout=open_timeout, max_size=10_000_000) as ws:
                        async for message in ws:
                            if isinstance(message, bytes):
                                message = message.decode("utf-8", errors="replace")
                            payload = json.loads(message)
                            if isinstance(payload, dict):
                                yield payload
                        return
                except Exception as exc:
                    last_exc = exc
                    continue
            await asyncio.sleep(0.2)
        raise OpenHandsError(f"Could not connect to websocket stream. Last error: {last_exc!r}")


def extract_message_text(event: JsonDict) -> str:
    llm_message = event.get("llm_message")
    if not isinstance(llm_message, dict):
        return ""
    content = llm_message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(str(item.get("text") or ""))
        return "".join(texts)
    return ""


def is_agent_message(event: JsonDict) -> bool:
    return event.get("kind") == "MessageEvent" and event.get("source") == "agent"


def extract_assistant_result_text(event: JsonDict) -> str:
    if is_agent_message(event):
        return extract_message_text(event)
    return ""


def extract_execution_status(event: JsonDict) -> str | None:
    if event.get("kind") == "ConversationStateUpdateEvent" and event.get("key") == "execution_status":
        value = event.get("value")
        return str(value) if value is not None else None
    return None


async def find_reusable_sandbox_for_model(
    client: OpenHandsClient,
    *,
    model: str | None,
    sandbox_cache: JsonDict | None = None,
) -> str | None:
    if model is None:
        return None
    cache = sandbox_cache if sandbox_cache is not None else {}
    cached = cache.get(model)
    if isinstance(cached, str) and cached:
        return cached

    page_id: str | None = None
    while True:
        try:
            result = await client.search_app_conversations(
                limit=100,
                page_id=page_id,
                include_sub_conversations=False,
            )
        except Exception:
            return None
        conversations = result.get("items", []) if isinstance(result, dict) else []
        for conv in conversations:
            if isinstance(conv, dict) and conv.get("llm_model") == model:
                sandbox_id = conv.get("sandbox_id")
                if isinstance(sandbox_id, str) and sandbox_id:
                    cache[model] = sandbox_id
                    return sandbox_id
        page_id = result.get("next_page_id") if isinstance(result, dict) else None
        if not page_id:
            break
    return None


async def run_conversation_and_collect(
    *,
    endpoint: str,
    api_key: str | None = None,
    prompt: str | None = None,
    llm_model: str | None = None,
    selected_repository: str | None = None,
    selected_branch: str | None = None,
    git_provider: str | None = None,
    title: str | None = None,
    sandbox_id: str | None = None,
    conversation_id: str | None = None,
    start_poll_interval: float = 1.0,
    websocket_open_timeout: float = 20.0,
    websocket_retry_seconds: float = 30.0,
    terminal_grace_seconds: float = 1.0,
) -> OpenHandsRunResult:
    client = OpenHandsClient(endpoint, api_key=api_key)
    payload = build_app_conversation_payload(
        prompt=prompt,
        llm_model=llm_model,
        selected_repository=selected_repository,
        selected_branch=selected_branch,
        git_provider=git_provider,
        sandbox_id=sandbox_id,
        conversation_id=conversation_id,
        title=title,
    )
    started = await client.start_app_conversation(payload, poll_interval=start_poll_interval)
    seen_event_ids: set[str] = set()
    final_text = ""
    final_status: str | None = None
    terminal_seen = False
    terminal_deadline: float | None = None
    event_iter = client.stream_v1_events(started, open_timeout=websocket_open_timeout, retry_seconds=websocket_retry_seconds)
    try:
        while True:
            timeout: float | None = None
            if terminal_seen and terminal_deadline is not None:
                timeout = max(0.0, terminal_deadline - asyncio.get_running_loop().time())
            try:
                if timeout is None:
                    event = await anext(event_iter)
                else:
                    event = await asyncio.wait_for(anext(event_iter), timeout=timeout)
            except (StopAsyncIteration, asyncio.TimeoutError, OpenHandsError):
                break
            event_id = event.get("id")
            if isinstance(event_id, str) and event_id:
                if event_id in seen_event_ids:
                    continue
                seen_event_ids.add(event_id)
            text = extract_assistant_result_text(event)
            if text.strip():
                final_text = text
                if terminal_seen:
                    break
            status = extract_execution_status(event)
            if status in TERMINAL_EXECUTION_STATES:
                final_status = status
                if final_text:
                    break
                terminal_seen = True
                terminal_deadline = asyncio.get_running_loop().time() + max(0.0, terminal_grace_seconds)
    finally:
        await event_iter.aclose()
    if not final_text.strip():
        final_text = await client.fetch_final_text_fallback(started)
    return OpenHandsRunResult(
        text=final_text.strip(),
        status=final_status,
        conversation_id=started.conversation_id,
        start=started,
        seen_event_ids=frozenset(seen_event_ids),
    )

from __future__ import annotations

import asyncio
import inspect
import json
import sys
from pathlib import Path
from typing import Any, AsyncIterator, Callable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
import websockets

from artifact_workflow_runtime.runtime_events import EventSink, emit_event

from .models import AppConversationStart, JsonDict, OpenHandsRunResult

EventCallback = Callable[[JsonDict], None]

SECRET_FIELD_NAMES = {"secrets", "secret", "api_key", "token", "password", "key"}


def _transport_note(event_sink: EventSink | None, kind: str, message: str, payload: JsonDict | None = None) -> None:
    if event_sink is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(emit_event(event_sink, kind, "transport", message, payload or {}))


def _make_transport_event_callback(event_sink: EventSink | None) -> EventCallback | None:
    if event_sink is None:
        return None

    def callback(event: JsonDict) -> None:
        if "_websocket" in event:
            ws_kind = str(event.get("_websocket") or "event")
            payload: JsonDict = {}
            if event.get("url"):
                payload["ws_url"] = str(event.get("url"))
            if ws_kind == "connect":
                _transport_note(event_sink, "websocket_connected", "WebSocket connected", payload)
            elif ws_kind == "disconnect":
                _transport_note(event_sink, "websocket_disconnected", "WebSocket disconnected", payload)
            else:
                _transport_note(event_sink, f"websocket_{ws_kind}", f"WebSocket {ws_kind}", payload)
            return

        kind = str(event.get("kind") or "")
        if kind == "ConversationStateUpdateEvent" and str(event.get("key") or "") == "execution_status":
            status = str(event.get("value") or "")
            _transport_note(event_sink, "execution_status", f"execution_status={status}", {"execution_status": status, "last_status": status})
            return
        if kind == "MessageEvent" and (str(event.get("source") or "") == "agent" or is_agent_message(event)):
            text = extract_message_text(event).strip()
            _transport_note(event_sink, "assistant_message", "Assistant message received", {"chars": len(text), "preview": _clip(text, max_chars=140)})
            return
        if kind == "ActionEvent":
            _transport_note(event_sink, "action_event", _action_line(event), {"tool_name": str(event.get("tool_name") or "")})
            return
        if kind == "ObservationEvent":
            _transport_note(event_sink, "observation_event", _observation_line(event), {"tool_name": str(event.get("tool_name") or "")})
            return

    return callback


def load_json_value(value: str, *, source: str = "JSON") -> Any:
    """Parse a JSON CLI value with clear OpenHandsError messages."""
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise OpenHandsError(f"Invalid {source}: {exc.msg} at char {exc.pos}") from exc


def load_json_file(path: str, *, source: str = "JSON file") -> Any:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise OpenHandsError(f"Could not read {source} {path!r}: {exc}") from exc
    return load_json_value(text, source=f"{source} {path!r}")


def load_json_object(value: str, *, source: str = "JSON") -> JsonDict:
    data = load_json_value(value, source=source)
    if not isinstance(data, dict):
        raise OpenHandsError(f"{source} must be a JSON object, got {type(data).__name__}")
    return data


def load_json_object_file(path: str, *, source: str = "JSON file") -> JsonDict:
    data = load_json_file(path, source=source)
    if not isinstance(data, dict):
        raise OpenHandsError(f"{source} {path!r} must be a JSON object, got {type(data).__name__}")
    return data


def load_json_array(value: str, *, source: str = "JSON") -> list[Any]:
    data = load_json_value(value, source=source)
    if not isinstance(data, list):
        raise OpenHandsError(f"{source} must be a JSON array, got {type(data).__name__}")
    return data


def load_json_array_file(path: str, *, source: str = "JSON file") -> list[Any]:
    data = load_json_file(path, source=source)
    if not isinstance(data, list):
        raise OpenHandsError(f"{source} {path!r} must be a JSON array, got {type(data).__name__}")
    return data


def parse_key_value(raw: str, *, option: str) -> tuple[str, str]:
    if "=" not in raw:
        raise OpenHandsError(f"{option} expects KEY=VALUE, got {raw!r}")
    key, value = raw.split("=", 1)
    key = key.strip()
    if not key:
        raise OpenHandsError(f"{option} has empty KEY in {raw!r}")
    return key, value


def set_if_not_none(payload: JsonDict, key: str, value: Any) -> None:
    if value is not None:
        payload[key] = value


def redact_secrets(value: Any, *, parent_key: str | None = None) -> Any:
    """Return a copy of payload safe enough for debug printing."""
    if isinstance(value, dict):
        redacted: JsonDict = {}
        for key, item in value.items():
            key_l = str(key).lower()
            if parent_key == "secrets" or any(marker in key_l for marker in SECRET_FIELD_NAMES):
                redacted[key] = "**********"
            else:
                redacted[key] = redact_secrets(item, parent_key=key_l)
        return redacted
    if isinstance(value, list):
        return [redact_secrets(item, parent_key=parent_key) for item in value]
    return value


def build_initial_message_from_prompt(prompt: str) -> JsonDict:
    return {"content": [{"type": "text", "text": prompt}]}


def build_app_conversation_payload(
    *,
    payload_file: str | None = None,
    payload_json: str | None = None,
    param_json: list[str] | None = None,
    sandbox_id: str | None = None,
    conversation_id: str | None = None,
    prompt: str | None = None,
    llm_model: str | None = None,
    initial_message_json: str | None = None,
    initial_message_file: str | None = None,
    system_message_suffix: str | None = None,
    processors_json: str | None = None,
    processors_file: str | None = None,
    selected_repository: str | None = None,
    selected_branch: str | None = None,
    git_provider: str | None = None,
    suggested_task_json: str | None = None,
    suggested_task_file: str | None = None,
    title: str | None = None,
    trigger: str | None = None,
    pr_number: list[int] | None = None,
    parent_conversation_id: str | None = None,
    agent_type: str | None = None,
    public: bool | None = None,
    plugins_json: str | None = None,
    plugins_file: str | None = None,
    plugin_json: list[str] | None = None,
    secrets_json: str | None = None,
    secrets_file: str | None = None,
    secret: list[str] | None = None,
) -> JsonDict:
    """Build POST /api/v1/app-conversations payload.

    The invariant is important: no AppConversationStartRequest field is added
    unless the user explicitly provided it through a CLI flag, payload JSON, or
    payload file. This avoids silently changing global/default OpenHands behavior.
    """
    payload: JsonDict = {}

    # Base payloads first; later flags intentionally override them.
    if payload_file:
        payload.update(load_json_object_file(payload_file, source="--payload-file"))
    if payload_json:
        payload.update(load_json_object(payload_json, source="--payload-json"))

    # Arbitrary top-level fields support future OpenHands API additions without
    # a new CLI release.
    for raw in param_json or []:
        key, value = parse_key_value(raw, option="--param-json")
        payload[key] = load_json_value(value, source=f"--param-json {key}")

    # Known scalar fields from AppConversationStartRequest.
    set_if_not_none(payload, "sandbox_id", sandbox_id)
    set_if_not_none(payload, "conversation_id", conversation_id)
    set_if_not_none(payload, "llm_model", llm_model)
    set_if_not_none(payload, "system_message_suffix", system_message_suffix)
    set_if_not_none(payload, "selected_repository", selected_repository)
    set_if_not_none(payload, "selected_branch", selected_branch)
    set_if_not_none(payload, "git_provider", git_provider)
    set_if_not_none(payload, "title", title)
    set_if_not_none(payload, "trigger", trigger)
    set_if_not_none(payload, "parent_conversation_id", parent_conversation_id)
    set_if_not_none(payload, "agent_type", agent_type)
    set_if_not_none(payload, "public", public)

    if pr_number is not None:
        payload["pr_number"] = pr_number

    # initial_message precedence: file > JSON > prompt > base payload.
    if prompt is not None:
        payload["initial_message"] = build_initial_message_from_prompt(prompt)
    if initial_message_json:
        payload["initial_message"] = load_json_value(initial_message_json, source="--initial-message-json")
    if initial_message_file:
        payload["initial_message"] = load_json_file(initial_message_file, source="--initial-message-file")

    if processors_json:
        payload["processors"] = load_json_array(processors_json, source="--processors-json")
    if processors_file:
        payload["processors"] = load_json_array_file(processors_file, source="--processors-file")

    if suggested_task_json:
        payload["suggested_task"] = load_json_object(suggested_task_json, source="--suggested-task-json")
    if suggested_task_file:
        payload["suggested_task"] = load_json_object_file(suggested_task_file, source="--suggested-task-file")

    plugins: list[Any] | None = None
    if plugins_json:
        plugins = load_json_array(plugins_json, source="--plugins-json")
    if plugins_file:
        plugins = load_json_array_file(plugins_file, source="--plugins-file")
    if plugin_json:
        if plugins is None:
            existing = payload.get("plugins")
            plugins = list(existing) if isinstance(existing, list) else []
        for idx, raw in enumerate(plugin_json, start=1):
            plugin = load_json_object(raw, source=f"--plugin-json #{idx}")
            plugins.append(plugin)
    if plugins is not None:
        payload["plugins"] = plugins

    secrets: JsonDict | None = None
    if secrets_json:
        secrets = load_json_object(secrets_json, source="--secrets-json")
    if secrets_file:
        secrets = load_json_object_file(secrets_file, source="--secrets-file")
    if secret:
        if secrets is None:
            existing = payload.get("secrets")
            secrets = dict(existing) if isinstance(existing, dict) else {}
        for raw in secret:
            key, value = parse_key_value(raw, option="--secret")
            secrets[key] = value
    if secrets is not None:
        payload["secrets"] = secrets

    if not payload:
        raise OpenHandsError(
            "No app-conversation fields were provided. Pass at least --prompt, "
            "--payload-json, --payload-file, or another conversation field."
        )
    if "initial_message" not in payload:
        # The API can theoretically start without an initial message in some
        # internal flows, but this CLI waits for an answer. Make the likely user
        # mistake obvious while still allowing an explicit null through
        # --payload-json '{"initial_message": null}' if they really need it.
        raise OpenHandsError(
            "No initial_message was provided. Use --prompt, --initial-message-json, "
            "--initial-message-file, --payload-json, or --payload-file."
        )
    return payload


class OpenHandsError(RuntimeError):
    pass


class OpenHandsHTTPError(OpenHandsError):
    def __init__(self, method: str, path: str, response: httpx.Response) -> None:
        self.method = method
        self.path = path
        self.status_code = response.status_code
        self.text = response.text
        self.allow = response.headers.get("allow")
        allow_suffix = f" allow={self.allow!r}" if self.allow else ""
        super().__init__(
            f"{method} {path} failed: HTTP {response.status_code}{allow_suffix}: {response.text}"
        )


TERMINAL_EXECUTION_STATES = {"finished", "error", "stuck", "waiting_for_confirmation"}
TERMINAL_SANDBOX_STATES = {"ERROR", "MISSING"}


def _find_first_string_by_key(value: Any, keys: set[str]) -> str | None:
    """Return the first non-empty string found at any matching key in nested JSON."""
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) in keys and isinstance(item, str) and item.strip():
                return item.strip()
        for item in value.values():
            found = _find_first_string_by_key(item, keys)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_first_string_by_key(item, keys)
            if found:
                return found
    return None


def _response_text_snippet(response: httpx.Response, max_chars: int = 500) -> str:
    try:
        return response.text[:max_chars]
    except Exception:
        return ""


class OpenHandsClient:
    """Small OpenHands V1 REST + native websocket client.

    This client intentionally does not read or write /api/settings.

    The per-conversation model is sent as ``llm_model`` in
    ``POST /api/v1/app-conversations``. The old V0 endpoint
    ``POST /api/conversations`` is not used here because its request model does
    not accept llm_model.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        api_key: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    @property
    def headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            # V1 Cloud API uses Bearer auth. Some self-hosted/local endpoints and
            # agent-server calls use X-Session-API-Key, so send both.
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
        if not path.startswith("/"):
            path = "/" + path
        return self.endpoint + path

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: JsonDict | None = None,
        params: JsonDict | None = None,
        headers: dict[str, str] | None = None,
    ) -> JsonDict | list[Any]:
        request_headers = headers or self.headers
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(
                    method,
                    self._url(path),
                    headers=request_headers,
                    json=json_body,
                    params=params,
                )
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
        except json.JSONDecodeError as exc:
            raise OpenHandsError(
                f"{method} {path} returned non-JSON response: {response.text[:500]}"
            ) from exc
        if isinstance(data, (dict, list)):
            return data
        return {"value": data}

    async def create_app_conversation(self, payload: JsonDict) -> JsonDict:
        """Create a V1 app conversation with the exact user-provided payload."""
        if not payload:
            raise OpenHandsError("Refusing to create app conversation with an empty payload")
        data = await self._request("POST", "/api/v1/app-conversations", json_body=payload)
        if not isinstance(data, dict):
            raise OpenHandsError(f"Unexpected app-conversations response: {data!r}")
        return data

    async def get_start_task(self, task_id: str) -> JsonDict | None:
        data = await self._request(
            "GET",
            "/api/v1/app-conversations/start-tasks",
            params={"ids": task_id},
        )
        if isinstance(data, list):
            if not data:
                return None
            item = data[0]
            if isinstance(item, dict):
                return item
            return {"value": item}
        if isinstance(data, dict):
            if "items" in data and isinstance(data["items"], list):
                return data["items"][0] if data["items"] else None
            return data
        return None

    async def wait_start_task_ready(
        self,
        task: JsonDict,
        *,
        poll_interval: float = 5.0,
        max_attempts: int = 120,
        verbose: bool = False,
    ) -> JsonDict:
        task_id = str(task.get("id") or "")
        if not task_id:
            raise OpenHandsError(f"OpenHands did not return start task id: {task}")

        current = task
        for attempt in range(max_attempts):
            status = str(current.get("status") or "")
            if status == "READY" and current.get("app_conversation_id"):
                return current
            if status == "ERROR":
                detail = current.get("detail") or current.get("error") or current
                raise OpenHandsError(f"OpenHands start task failed: {detail}")

            if verbose and (attempt > 0 or status != "READY"):
                print(f"[setup] start task status={status or 'UNKNOWN'}", file=sys.stderr)
            await asyncio.sleep(poll_interval)
            try:
                next_task = await self.get_start_task(task_id)
            except OpenHandsError as exc:
                # The app server can briefly stop answering while the sandbox is
                # being created or while the agent server is being attached. A
                # transient timeout must not kill the role run with a Python
                # traceback; keep polling until max_attempts is exhausted.
                if verbose:
                    print(f"[warn] start-task poll failed; retrying: {exc}", file=sys.stderr)
                continue
            if next_task:
                current = next_task

        raise OpenHandsError(
            f"Timed out waiting for OpenHands start task {task_id} to become READY. Last task: {current}"
        )

    async def get_app_conversation(self, conversation_id: str) -> JsonDict | None:
        data = await self._request(
            "GET",
            "/api/v1/app-conversations",
            params={"ids": conversation_id},
        )
        if isinstance(data, list):
            return data[0] if data and isinstance(data[0], dict) else None
        if isinstance(data, dict):
            if "items" in data and isinstance(data["items"], list):
                return data["items"][0] if data["items"] else None
            return data
        return None



    async def get_existing_conversation_start(self, conversation_id: str) -> AppConversationStart:
        """Build a best-effort AppConversationStart for an existing conversation id."""
        info = await self.get_app_conversation(conversation_id)
        if not info:
            return AppConversationStart(conversation_id=conversation_id)
        conversation_url = str(info.get("conversation_url") or info.get("url") or "") or None
        agent_server_url = str(
            info.get("agent_server_url")
            or _find_first_string_by_key(info, {"agent_server_url", "runtime_url"})
            or ""
        ) or None
        session_api_key = _find_first_string_by_key(
            info,
            {"session_api_key", "session_key", "runtime_session_api_key"},
        )
        return AppConversationStart(
            conversation_id=conversation_id,
            task_id=str(info.get("task_id") or "") or None,
            status=str(info.get("status") or "") or None,
            sandbox_id=str(info.get("sandbox_id") or "") or None,
            agent_server_url=agent_server_url,
            conversation_url=conversation_url,
            session_api_key=session_api_key,
            raw_conversation=info,
        )

    async def get_conversation_messages(self, conversation_id: str) -> list[JsonDict]:
        """Fetch message history for an existing conversation from the agent-server API."""
        conversation = await self.get_existing_conversation_start(conversation_id)
        headers = self._headers_with_session_key(conversation.session_api_key)
        endpoints: list[str] = []
        seen: set[str] = set()
        for base in self.build_agent_conversation_api_bases(conversation):
            endpoints.append(f"{base.rstrip('/')}/messages")
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as http:
            for url in endpoints:
                if url in seen:
                    continue
                seen.add(url)
                try:
                    response = await http.get(url, headers=headers)
                except httpx.HTTPError:
                    continue
                content_type = response.headers.get("content-type", "")
                if response.status_code >= 400 or "json" not in content_type.lower():
                    continue
                try:
                    data = response.json()
                except ValueError:
                    continue
                if isinstance(data, list):
                    return [item for item in data if isinstance(item, dict)]
                if isinstance(data, dict):
                    messages = data.get("messages")
                    if isinstance(messages, list):
                        return [item for item in messages if isinstance(item, dict)]
        return []
    async def search_sandboxes(
        self,
        limit: int = 100,
        page_id: str | None = None,
    ) -> JsonDict:
        """Search/list sandboxes owned by the current user.

        GET /api/v1/sandboxes/search
        """
        params: dict[str, str | int] = {"limit": limit}
        if page_id:
            params["page_id"] = page_id
        return await self._request("GET", "/api/v1/sandboxes/search", params=params)

    async def search_app_conversations(
        self,
        sandbox_id__eq: str | None = None,
        limit: int = 100,
        page_id: str | None = None,
        include_sub_conversations: bool = False,
    ) -> JsonDict:
        """Search app conversations, optionally filtered by sandbox_id.

        GET /api/v1/app-conversations/search
        """
        params: dict[str, str | int | bool] = {
            "limit": limit,
            "include_sub_conversations": include_sub_conversations,
        }
        if sandbox_id__eq:
            params["sandbox_id__eq"] = sandbox_id__eq
        if page_id:
            params["page_id"] = page_id
        return await self._request("GET", "/api/v1/app-conversations/search", params=params)

    async def update_app_conversation(self, conversation_id: str, patch: JsonDict) -> JsonDict | None:
        """Patch V1 app-conversation metadata when the server supports it.

        Some OpenHands builds ignore the ``title`` field during
        ``POST /api/v1/app-conversations`` and leave API-created conversations
        with the default ``Conversation abc12`` title. The Web UI Swagger for
        recent V1 builds exposes ``PATCH /api/v1/app-conversations/{id}``, so
        callers can update metadata after the conversation id is known.
        """
        if not patch:
            return None
        data = await self._request(
            "PATCH",
            f"/api/v1/app-conversations/{conversation_id}",
            json_body=patch,
        )
        if isinstance(data, dict):
            return data
        return {"items": data}

    async def try_update_app_conversation_title(
        self,
        conversation_id: str,
        title: str | None,
        *,
        verbose: bool = False,
    ) -> JsonDict | None:
        """Best-effort title update for V1 API-created conversations.

        Title patching must not make a role fail: older/local OpenHands builds
        may not expose the endpoint yet. In that case we keep running and emit
        a warning only when setup verbosity is enabled.
        """
        clean_title = " ".join(str(title or "").split()).strip()
        if not clean_title:
            return None
        try:
            return await self.update_app_conversation(conversation_id, {"title": clean_title})
        except OpenHandsError as exc:
            if verbose:
                print(f"[warn] app conversation title update failed; continuing: {exc}", file=sys.stderr)
            return None

    async def start_app_conversation(
        self,
        *,
        payload: JsonDict,
        poll_interval: float = 5.0,
        max_start_attempts: int = 120,
        verbose_start: bool = False,
    ) -> AppConversationStart:
        task = await self.create_app_conversation(payload)
        ready_task = await self.wait_start_task_ready(
            task,
            poll_interval=poll_interval,
            max_attempts=max_start_attempts,
            verbose=verbose_start,
        )
        cid = ready_task.get("app_conversation_id") or ready_task.get("conversation_id")
        if not cid:
            raise OpenHandsError(f"Start task is READY but has no app_conversation_id: {ready_task}")
        cid = str(cid)

        requested_title = payload.get("title") if isinstance(payload, dict) else None
        if requested_title:
            await self.try_update_app_conversation_title(
                cid,
                str(requested_title),
                verbose=verbose_start,
            )

        try:
            conversation = await self.get_app_conversation(cid)
        except OpenHandsError as exc:
            # Metadata lookup is optional; the websocket URL can be derived from
            # the OpenHands endpoint and conversation id. Do not exit before
            # streaming events just because a metadata endpoint differs across
            # OpenHands builds or is temporarily slow.
            if verbose_start:
                print(f"[warn] app conversation metadata lookup failed; continuing: {exc}", file=sys.stderr)
            conversation = None

        # Do not call the legacy /api/conversations/{id} endpoint here.
        # In some local OpenHands builds that path is handled by the React SPA
        # fallback and returns text/html with HTTP 200, which caused the watcher
        # to exit before opening the event websocket.
        # V1 websocket events are available directly under the OpenHands endpoint
        # at /sockets/events/{conversation_id}.
        agent_server_url = str(
            ready_task.get("agent_server_url")
            or _find_first_string_by_key(ready_task, {"agent_server_url", "runtime_url"})
            or ""
        ) or None
        conversation_url = str(
            ready_task.get("conversation_url")
            or ready_task.get("url")
            or _find_first_string_by_key(ready_task, {"conversation_url"})
            or ""
        ) or None
        session_api_key = _find_first_string_by_key(
            ready_task,
            {"session_api_key", "session_key", "runtime_session_api_key"},
        )
        if conversation:
            conversation_url = str(
                conversation.get("conversation_url")
                or conversation.get("url")
                or conversation_url
                or ""
            ) or None
            session_api_key = (
                _find_first_string_by_key(
                    conversation,
                    {"session_api_key", "session_key", "runtime_session_api_key"},
                )
                or session_api_key
            )

        return AppConversationStart(
            conversation_id=cid,
            task_id=str(ready_task.get("id") or task.get("id") or "") or None,
            status=str(ready_task.get("status") or "") or None,
            sandbox_id=str(ready_task.get("sandbox_id") or (conversation or {}).get("sandbox_id") or "") or None,
            agent_server_url=agent_server_url,
            conversation_url=conversation_url,
            session_api_key=session_api_key,
            raw_task=ready_task,
            raw_conversation=conversation,
        )

    async def wait_until_terminal(
        self,
        conversation_id: str,
        *,
        poll_interval: float = 10.0,
    ) -> JsonDict:
        while True:
            try:
                info = await self.get_app_conversation(conversation_id)
            except OpenHandsError as exc:
                # Terminal polling is only a convenience. Do not let a metadata
                # endpoint mismatch kill the websocket event stream.
                print(f"[warn] terminal poll failed; continuing websocket stream: {exc}", file=sys.stderr)
                await asyncio.sleep(poll_interval)
                continue
            if not info:
                await asyncio.sleep(poll_interval)
                continue
            sandbox_status = info.get("sandbox_status")
            execution_status = info.get("execution_status")
            if sandbox_status in TERMINAL_SANDBOX_STATES or execution_status in TERMINAL_EXECUTION_STATES:
                return info
            await asyncio.sleep(poll_interval)

    def _parse_ws_base(self, base: str) -> Any:
        parsed = urlparse(base)
        if not parsed.scheme:
            # Relative URL from a proxied deployment.
            endpoint = urlparse(self.endpoint)
            if base.startswith("/"):
                parsed = urlparse(urlunparse((endpoint.scheme, endpoint.netloc, base, "", "", "")))
            else:
                parsed = urlparse(self.endpoint.rstrip("/") + "/" + base)
        return parsed

    def _normalize_docker_host_for_local_cli(self, base: str) -> str | None:
        """Return a host-reachable variant of Docker-only URLs when possible.

        OpenHands may return agent_server_url=http://host.docker.internal:<port>.
        That value is useful inside Docker, but a CLI running directly on the
        Linux host usually needs localhost:<port> instead. The app conversation
        metadata often already exposes conversation_url=http://localhost:<port>,
        but this fallback keeps the client robust when only agent_server_url is
        present.
        """
        parsed = self._parse_ws_base(base)
        if parsed.hostname not in {"host.docker.internal", "gateway.docker.internal"}:
            return None
        endpoint_host = urlparse(self.endpoint).hostname or "localhost"
        if endpoint_host in {"127.0.0.1", "::1"}:
            endpoint_host = "localhost"
        netloc = endpoint_host
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        return urlunparse((parsed.scheme, netloc, parsed.path, "", parsed.query, ""))

    def build_v1_websocket_url_from_base(self, conversation_id: str, base: str) -> str:
        parsed = self._parse_ws_base(base)

        scheme = "wss" if parsed.scheme == "https" else "ws"
        netloc = parsed.netloc
        path = parsed.path or ""

        # conversation_url can look like:
        #   http://host:port/api/conversations/<id>
        # or, behind a proxy:
        #   https://host/runtime/123/api/conversations/<id>
        # The websocket path must preserve anything before /api/conversations.
        if "/api/conversations" in path:
            prefix = path.split("/api/conversations", 1)[0]
        else:
            prefix = path.rstrip("/")
        ws_path = f"{prefix}/sockets/events/{conversation_id}"
        ws_path = ws_path.replace("//", "/")
        if not ws_path.startswith("/"):
            ws_path = "/" + ws_path

        query = dict(parse_qsl(parsed.query))
        query.setdefault("resend_all", "true")
        return urlunparse((scheme, netloc, ws_path, "", urlencode(query), ""))

    def build_v1_websocket_urls(self, conversation: AppConversationStart) -> list[str]:
        """Return candidate V1 websocket URLs in safest order.

        Prefer conversation_url over agent_server_url. In local Docker setups the
        latter can be host.docker.internal:<dynamic-port>, which is frequently
        not reachable from a CLI process running on the host.
        """
        bases: list[str] = []
        for base in (conversation.conversation_url, conversation.agent_server_url, self.endpoint):
            if base and base not in bases:
                bases.append(base)
            if base:
                normalized = self._normalize_docker_host_for_local_cli(base)
                if normalized and normalized not in bases:
                    bases.append(normalized)

        urls: list[str] = []
        for base in bases:
            url = self.build_v1_websocket_url_from_base(conversation.conversation_id, base)
            if url not in urls:
                urls.append(url)
        return urls

    def build_v1_websocket_url(self, conversation: AppConversationStart) -> str:
        return self.build_v1_websocket_urls(conversation)[0]

    def build_agent_conversation_api_bases(self, conversation: AppConversationStart) -> list[str]:
        """Return candidate agent-server /api/conversations/<id> bases.

        V1 app-conversation creation returns runtime metadata pointing at the
        dynamic agent server. Follow-up messages must be sent there, not to
        POST /api/v1/app-conversations, otherwise OpenHands creates a brand-new
        sandbox. Keep the same local-Docker URL normalization used by websockets.
        """
        bases: list[str] = []

        def add_base(base: str | None) -> None:
            if not base:
                return
            base = base.rstrip("/")
            if base not in bases:
                bases.append(base)
            normalized = self._normalize_docker_host_for_local_cli(base)
            if normalized:
                normalized = normalized.rstrip("/")
                if normalized not in bases:
                    bases.append(normalized)

        if conversation.conversation_url:
            add_base(conversation.conversation_url)
        if conversation.agent_server_url:
            add_base(f"{conversation.agent_server_url.rstrip('/')}/api/conversations/{conversation.conversation_id}")
        # Last-resort local/proxied path. In some builds this may be the SPA
        # fallback, but it is harmless because callers try candidates in order.
        add_base(f"{self.endpoint.rstrip('/')}/api/conversations/{conversation.conversation_id}")
        return bases

    async def send_message_to_existing_conversation(
        self,
        conversation: AppConversationStart,
        text: str,
        *,
        run: bool = True,
    ) -> JsonDict:
        """Send a user message to an already-running agent-server conversation.

        The agent-server endpoint is:
        POST /api/conversations/{conversation_id}/events
        with body {role, content, run}. Passing run=True starts the agent loop
        again when the conversation is idle/finished.
        """
        headers = self._headers_with_session_key(conversation.session_api_key)
        body: JsonDict = {
            "role": "user",
            "content": [
                {
                    "text": text,
                    "cache_prompt": False,
                    "type": "text",
                }
            ],
            "run": run,
        }
        last_error: str | None = None
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as http:
            for base in self.build_agent_conversation_api_bases(conversation):
                url = f"{base.rstrip('/')}/events"
                try:
                    response = await http.post(url, headers=headers, json=body)
                except httpx.HTTPError as exc:
                    last_error = f"{url}: {type(exc).__name__}: {exc}"
                    continue
                if response.status_code >= 400:
                    last_error = f"{url}: HTTP {response.status_code}: {_response_text_snippet(response)}"
                    continue
                if not response.content:
                    return {"success": True}
                try:
                    data = response.json()
                except ValueError:
                    return {"success": True, "raw": response.text}
                if isinstance(data, dict):
                    return data
                return {"value": data}
        raise OpenHandsError(
            "Could not send follow-up message to existing OpenHands conversation; "
            f"last error: {last_error or 'no candidate URL worked'}"
        )

    async def fetch_final_text_fallback(self, conversation: AppConversationStart) -> str:
        """Try REST fallbacks for versions where websocket misses final text."""
        headers = self._headers_with_session_key(conversation.session_api_key)

        bases: list[str] = []

        def add_base(base: str | None) -> None:
            if not base:
                return
            base = base.rstrip("/")
            if base not in bases:
                bases.append(base)
            normalized = self._normalize_docker_host_for_local_cli(base)
            if normalized:
                normalized = normalized.rstrip("/")
                if normalized not in bases:
                    bases.append(normalized)

        add_base(conversation.conversation_url)
        if conversation.agent_server_url:
            add_base(f"{conversation.agent_server_url.rstrip('/')}/api/conversations/{conversation.conversation_id}")

        endpoints: list[str] = []
        for base in bases:
            endpoints.extend([
                base,
                f"{base}/events",
                f"{base}/messages",
                f"{base}/state",
            ])

        seen: set[str] = set()
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as http:
            for url in endpoints:
                if url in seen:
                    continue
                seen.add(url)
                try:
                    response = await http.get(url, headers=headers)
                except httpx.HTTPError:
                    continue
                content_type = response.headers.get("content-type", "")
                if response.status_code >= 400 or "json" not in content_type.lower():
                    continue
                try:
                    data = response.json()
                except ValueError:
                    continue
                texts = _recursive_assistant_texts(data)
                if texts:
                    return texts[-1]
        return ""


    async def _refresh_app_conversation_start_metadata(
        self,
        conversation: AppConversationStart,
        *,
        verbose: bool = False,
    ) -> AppConversationStart:
        """Refresh runtime URL/session fields for a conversation if available."""
        try:
            info = await self.get_app_conversation(conversation.conversation_id)
        except OpenHandsError as exc:
            if verbose:
                print(f"[warn] app conversation metadata refresh failed: {exc}", file=sys.stderr)
            return conversation
        if not info:
            return conversation

        conversation_url = (
            str(info.get("conversation_url") or info.get("url") or conversation.conversation_url or "")
            or None
        )
        agent_server_url = (
            str(
                info.get("agent_server_url")
                or _find_first_string_by_key(info, {"agent_server_url", "runtime_url"})
                or conversation.agent_server_url
                or ""
            )
            or None
        )
        session_api_key = (
            _find_first_string_by_key(
                info,
                {"session_api_key", "session_key", "runtime_session_api_key"},
            )
            or conversation.session_api_key
        )
        return conversation.model_copy(
            update={
                "conversation_url": conversation_url,
                "agent_server_url": agent_server_url,
                "session_api_key": session_api_key,
                "raw_conversation": info,
            }
        )

    async def stream_v1_events(
        self,
        conversation: AppConversationStart,
        *,
        on_event: EventCallback | None = None,
        transport_event_sink: EventSink | None = None,
        raw_websocket: bool = False,
        open_timeout: float = 20.0,
        retry_seconds: float = 240.0,
        retry_interval: float = 2.0,
    ) -> AsyncIterator[JsonDict]:
        """Stream V1 websocket events with startup retries.

        Local OpenHands can return READY/start metadata before the dynamic
        agent-server websocket is actually ready. During that window
        /sockets/events/<conversation_id> may return HTTP 500 or briefly close
        with auth/runtime errors, while the UI later recovers and continues the
        task. Treat failed websocket handshakes as a startup condition and retry
        instead of falling back immediately to an empty REST history.
        """
        # websockets changed the keyword name in newer versions. Support both.
        sig = inspect.signature(websockets.connect)
        header_kw = "additional_headers" if "additional_headers" in sig.parameters else "extra_headers"

        deadline = asyncio.get_running_loop().time() + max(0.0, retry_seconds)
        last_exc: BaseException | None = None
        connected_once = False
        attempt = 0

        while True:
            attempt += 1
            headers = self._headers_with_session_key(conversation.session_api_key)
            connect_kwargs: dict[str, Any] = {"open_timeout": open_timeout, header_kw: headers}

            urls = self.build_v1_websocket_urls(conversation)
            for ws_url in urls:
                _transport_note(transport_event_sink, "websocket_connecting", "Trying WebSocket candidate", {"ws_url": ws_url})
                if raw_websocket:
                    print(f"[socket] connecting {ws_url}", file=sys.stderr)
                try:
                    async with websockets.connect(ws_url, **connect_kwargs) as ws:
                        connected_once = True
                        connect_event = {"_websocket": "connect", "url": ws_url}
                        if on_event:
                            on_event(connect_event)
                        yield connect_event
                        async for message in ws:
                            if isinstance(message, bytes):
                                message = message.decode("utf-8", errors="replace")
                            try:
                                payload = json.loads(message)
                            except json.JSONDecodeError:
                                payload = {"message": message}
                            if not isinstance(payload, dict):
                                payload = {"event": payload}
                            if on_event:
                                on_event(payload)
                            yield payload
                        return
                except (
                    OSError,
                    TimeoutError,
                    asyncio.TimeoutError,
                    websockets.exceptions.WebSocketException,
                ) as exc:
                    last_exc = exc
                    _transport_note(transport_event_sink, "websocket_failed", f"WebSocket candidate failed: {type(exc).__name__}: {exc}", {"ws_url": ws_url})
                    if raw_websocket:
                        print(f"[socket] failed {ws_url}: {type(exc).__name__}: {exc}", file=sys.stderr)
                    continue

            now = asyncio.get_running_loop().time()
            if now >= deadline:
                raise OpenHandsError(
                    "Could not connect to any V1 websocket URL before retry timeout "
                    f"({retry_seconds:.0f}s). Last error: {last_exc!r}"
                )

            # Runtime metadata can become more complete after READY: first only
            # agent_server_url may exist, later conversation_url/session key may
            # appear. Refresh before the next websocket round.
            conversation = await self._refresh_app_conversation_start_metadata(
                conversation,
                verbose=raw_websocket,
            )
            _transport_note(transport_event_sink, "websocket_retry", "Retrying WebSocket after metadata refresh", {"conversation_id": conversation.conversation_id, "websocket_url": conversation.conversation_url or conversation.agent_server_url or self.endpoint})
            if raw_websocket:
                status = "after disconnect" if connected_once else "not ready yet"
                print(
                    f"[socket] websocket {status}; retrying in {retry_interval:.1f}s "
                    f"(attempt {attempt})",
                    file=sys.stderr,
                )
            await asyncio.sleep(max(0.1, retry_interval))


def _content_to_text(value: Any) -> str:
    """Extract text from OpenHands message/observation content shapes."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"]
        if "content" in value:
            return _content_to_text(value["content"])
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            text = _content_to_text(item)
            if text:
                parts.append(text)
        return "\n".join(parts)
    return str(value)


def _clip(text: str, *, max_chars: int = 300, one_line: bool = True) -> str:
    text = text.strip()
    if one_line:
        text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def extract_message_text(event: JsonDict) -> str:
    llm_message = event.get("llm_message")
    if isinstance(llm_message, dict):
        return _content_to_text(llm_message.get("content"))
    for key in ("message", "content", "text"):
        if event.get(key):
            return _content_to_text(event.get(key))
    return ""


def is_agent_message(event: JsonDict) -> bool:
    if event.get("kind") != "MessageEvent":
        return False
    if event.get("source") == "agent":
        return True
    llm_message = event.get("llm_message")
    return isinstance(llm_message, dict) and llm_message.get("role") == "assistant"


def extract_finish_action_text(event: JsonDict) -> str:
    """Extract a final answer from finish-like ActionEvent shapes.

    OpenHands versions do not always emit the final assistant answer as a
    MessageEvent before execution_status=finished. The web UI may still display
    the answer because it also understands finish/action events. Keep this
    intentionally conservative: only finish-like agent actions are considered a
    role answer, never arbitrary tool observations.
    """
    if event.get("kind") != "ActionEvent" or event.get("source") != "agent":
        return ""

    action = event.get("action") if isinstance(event.get("action"), dict) else {}
    action_kind = str(action.get("kind") or "").lower()
    tool_name = str(event.get("tool_name") or "").lower()

    finish_like = (
        "finish" in action_kind
        or action_kind in {"agentfinishaction", "messageaction"}
        or tool_name in {"finish", "final", "message", "done"}
    )
    if not finish_like:
        return ""

    candidates: list[Any] = []
    for key in (
        "final_thought",
        "message",
        "content",
        "text",
        "response",
        "answer",
        "summary",
        "thought",
        "outputs",
        "output",
    ):
        if key in action:
            candidates.append(action.get(key))
    for key in ("message", "content", "text"):
        if key in event:
            candidates.append(event.get(key))

    for value in candidates:
        text = _content_to_text(value).strip()
        if text:
            return text
    return ""


def extract_assistant_result_text(event: JsonDict) -> str:
    """Extract answer text from any known assistant-result event shape."""
    if is_agent_message(event):
        return extract_message_text(event)
    return extract_finish_action_text(event)


def _recursive_assistant_texts(value: Any) -> list[str]:
    """Best-effort fallback extractor for REST-returned conversation records."""
    found: list[str] = []
    if isinstance(value, dict):
        kind = value.get("kind")
        source = value.get("source")
        role = value.get("role")
        llm_message = value.get("llm_message")
        if (kind == "MessageEvent" and source == "agent") or role == "assistant":
            text = extract_message_text(value) or _content_to_text(value.get("content"))
            if text.strip():
                found.append(text.strip())
        if isinstance(llm_message, dict) and llm_message.get("role") == "assistant":
            text = _content_to_text(llm_message.get("content"))
            if text.strip():
                found.append(text.strip())
        # Finish-like action records can appear inside nested history lists.
        text = extract_finish_action_text(value)
        if text.strip():
            found.append(text.strip())
        for child in value.values():
            found.extend(_recursive_assistant_texts(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_recursive_assistant_texts(child))
    return found


def extract_execution_status(event: JsonDict) -> str | None:
    if event.get("kind") == "ConversationStateUpdateEvent" and event.get("key") == "execution_status":
        value = event.get("value")
        return str(value) if value is not None else None
    return None


def _stats_line(value: Any) -> str:
    if not isinstance(value, dict):
        return f"[stats] {_clip(str(value), max_chars=200)}"

    metrics: list[str] = []
    # OpenHands stats schemas vary across versions; keep this tolerant.
    interesting = (
        "model",
        "prompt_tokens",
        "completion_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "total_tokens",
        "response_latency",
        "cost",
    )
    for key in interesting:
        if key in value:
            metrics.append(f"{key}={value[key]}")

    # Some builds nest token usage under accumulated_token_usage or token_usage.
    for parent_key in ("accumulated_token_usage", "token_usage", "usage"):
        nested = value.get(parent_key)
        if isinstance(nested, dict):
            for key in ("prompt_tokens", "completion_tokens", "cache_read_tokens", "cache_write_tokens", "total_tokens"):
                if key in nested:
                    metrics.append(f"{key}={nested[key]}")

    if metrics:
        return "[stats] " + " ".join(metrics)
    return "[stats] " + _clip(json.dumps(value, ensure_ascii=False, sort_keys=True), max_chars=260)


def _action_line(event: JsonDict) -> str:
    tool = str(event.get("tool_name") or "action")
    action = event.get("action") if isinstance(event.get("action"), dict) else {}
    action_kind = str(action.get("kind") or "")

    if tool == "think" or action_kind == "ThinkAction":
        thought = str(action.get("thought") or event.get("summary") or "")
        return f"[think] {_clip(thought, max_chars=260)}"

    command_value = action.get("command")
    if action_kind == "TerminalAction" or tool in {"terminal", "bash", "shell"} or command_value:
        command = str(command_value or "")
        prefix = "terminal" if tool == "terminal" or action_kind == "TerminalAction" else tool
        return f"[{prefix}] $ {_clip(command, max_chars=500, one_line=True)}"

    if action_kind == "FileEditorAction" or tool == "file_editor":
        command = str(action.get("command") or "file")
        path = str(action.get("path") or "")
        view_range = action.get("view_range")
        range_suffix = f" range={view_range}" if view_range else ""
        return f"[file:{command}] {path}{range_suffix}".rstrip()

    if action_kind == "MCPToolAction" or tool.startswith("shttp_") or tool.startswith("mcp_"):
        data = action.get("data")
        if isinstance(data, dict):
            query = data.get("query") or data.get("q")
            if query:
                return f"[mcp:{tool}] query={_clip(str(query), max_chars=260)}"
            return f"[mcp:{tool}] {_clip(json.dumps(data, ensure_ascii=False, sort_keys=True), max_chars=260)}"
        return f"[mcp:{tool}] called"

    summary = str(event.get("summary") or "")
    if summary:
        return f"[action:{tool}] {_clip(summary, max_chars=260)}"
    return f"[action:{tool}] {action_kind or 'called'}"


def _observation_line(event: JsonDict) -> str:
    tool = str(event.get("tool_name") or "observation")
    observation = event.get("observation") if isinstance(event.get("observation"), dict) else {}
    obs_kind = str(observation.get("kind") or "")
    is_error = bool(observation.get("is_error"))
    marker = "error" if is_error else "ok"
    text = _content_to_text(observation.get("content"))

    if obs_kind == "TerminalObservation" or tool == "terminal":
        exit_code = observation.get("exit_code")
        if exit_code is None and isinstance(observation.get("metadata"), dict):
            exit_code = observation["metadata"].get("exit_code")
        timeout = observation.get("timeout")
        suffix = f" exit={exit_code}" if exit_code is not None else ""
        if timeout:
            suffix += " timeout=true"
        if text:
            return f"[terminal:{marker}{suffix}] {_clip(text, max_chars=500)}"
        return f"[terminal:{marker}{suffix}]"

    if obs_kind == "FileEditorObservation" or tool == "file_editor":
        command = str(observation.get("command") or "")
        path = str(observation.get("path") or "")
        line_count = len(text.splitlines()) if text else 0
        char_count = len(text)
        head = _clip(text, max_chars=220) if text else ""
        detail = f" lines={line_count} chars={char_count}" if text else ""
        if head:
            return f"[file:{marker}] {command} {path}{detail} :: {head}".strip()
        return f"[file:{marker}] {command} {path}".strip()

    if obs_kind == "ThinkObservation" or tool == "think":
        return "[think:ok] logged" if not is_error else f"[think:error] {_clip(text, max_chars=220)}"

    if obs_kind == "MCPToolObservation" or tool.startswith("shttp_") or tool.startswith("mcp_"):
        if text:
            return f"[mcp:{tool}:{marker}] {_clip(text, max_chars=500)}"
        return f"[mcp:{tool}:{marker}]"

    if text:
        return f"[observation:{tool}:{marker}] {_clip(text, max_chars=400)}"
    return f"[observation:{tool}:{marker}] {obs_kind or 'done'}"


def format_event(event: JsonDict, *, raw: bool = False, debug: bool = False) -> str | None:
    """Format OpenHands websocket events for humans.

    Default mode is compact and suppresses very noisy state snapshots.
    ``debug=True`` keeps compact formatting but includes state/stat summaries.
    ``raw=True`` prints the original JSON event.
    """
    if raw:
        return json.dumps(event, ensure_ascii=False, indent=2, sort_keys=True)

    if "_websocket" in event:
        if event["_websocket"] == "connect":
            return f"[socket] connected {event.get('url', '')}".rstrip()
        if event["_websocket"] == "disconnect":
            return "[socket] disconnected"
        return f"[socket] {event['_websocket']}: {event.get('data')}"

    kind = str(event.get("kind") or "")

    if kind == "SystemPromptEvent":
        return "[system] prompt/context loaded" if debug else None

    if kind == "MessageEvent":
        source = str(event.get("source") or "message")
        text = extract_message_text(event)
        if source == "user":
            return f"[user] {_clip(text, max_chars=500)}"
        if source == "agent" or is_agent_message(event):
            # Do not dump the final answer inline in compact mode; print it as a
            # clean result block when the conversation reaches a terminal state.
            return f"[assistant] response received ({len(text)} chars)" if debug else "[assistant] response received"
        return f"[message:{source}] {_clip(text, max_chars=500)}"

    if kind == "ActionEvent":
        return _action_line(event)

    if kind == "ObservationEvent":
        return _observation_line(event)

    if kind == "ConversationStateUpdateEvent":
        key = str(event.get("key") or "")
        value = event.get("value")
        if key == "execution_status":
            return f"[status] {value}"
        if key == "last_user_message_id":
            return f"[state] last_user_message_id={value}" if debug else None
        if key == "stats":
            return _stats_line(value) if debug else None
        if key == "full_state":
            if not debug:
                return None
            if isinstance(value, dict):
                skills = value.get("agent", {}).get("agent_context", {}).get("skills", []) if isinstance(value.get("agent"), dict) else []
                workspace = value.get("workspace_base") or value.get("workspace_mount_path") or value.get("workspace")
                return f"[state] full_state workspace={workspace} skills={len(skills) if isinstance(skills, list) else '?'}"
            return "[state] full_state"
        return f"[state] {key}={_clip(json.dumps(value, ensure_ascii=False, sort_keys=True), max_chars=220)}" if debug else None

    if debug:
        return "[event] " + _clip(json.dumps(event, ensure_ascii=False, sort_keys=True), max_chars=500)
    return None


def print_final_result(
    final_text: str | None,
    *,
    status: str | None = None,
    decorated: bool = False,
) -> None:
    text = final_text.strip() if final_text and final_text.strip() else "[no assistant result was received before the conversation finished]"
    if not decorated:
        print(text, flush=True)
        return

    print("", flush=True)
    title = "========== OpenHands result"
    if status:
        title += f" ({status})"
    title += " =========="
    print(title, flush=True)
    print(text, flush=True)
    print("========== end ==========" , flush=True)


async def run_conversation_and_collect(
    *,
    endpoint: str,
    api_key: str | None = None,
    show_events: bool = False,
    raw_events: bool = False,
    debug_events: bool = False,
    raw_websocket: bool = False,
    event_callback: EventCallback | None = None,
    event_sink: EventSink | None = None,
    exit_when_terminal: bool = True,
    start_poll_interval: float = 5.0,
    websocket_open_timeout: float = 20.0,
    websocket_retry_seconds: float = 240.0,
    terminal_grace_seconds: float = 15.0,
    rest_terminal_watch: bool = False,
    print_payload: bool = False,
    payload_file: str | None = None,
    payload_json: str | None = None,
    param_json: list[str] | None = None,
    sandbox_id: str | None = None,
    conversation_id: str | None = None,
    prompt: str | None = None,
    llm_model: str | None = None,
    initial_message_json: str | None = None,
    initial_message_file: str | None = None,
    system_message_suffix: str | None = None,
    processors_json: str | None = None,
    processors_file: str | None = None,
    selected_repository: str | None = None,
    selected_branch: str | None = None,
    git_provider: str | None = None,
    suggested_task_json: str | None = None,
    suggested_task_file: str | None = None,
    title: str | None = None,
    trigger: str | None = None,
    pr_number: list[int] | None = None,
    parent_conversation_id: str | None = None,
    agent_type: str | None = None,
    public: bool | None = None,
    plugins_json: str | None = None,
    plugins_file: str | None = None,
    plugin_json: list[str] | None = None,
    secrets_json: str | None = None,
    secrets_file: str | None = None,
    secret: list[str] | None = None,
) -> OpenHandsRunResult:
    """Create one app conversation, wait for terminal status, and return final text."""
    client = OpenHandsClient(endpoint, api_key=api_key)
    verbose = bool(show_events or raw_events or debug_events or raw_websocket)
    transport_callback = _make_transport_event_callback(event_sink)

    def combined_event_callback(event: JsonDict) -> None:
        if event_callback:
            event_callback(event)
        if transport_callback:
            transport_callback(event)

    payload = build_app_conversation_payload(
        payload_file=payload_file,
        payload_json=payload_json,
        param_json=param_json,
        sandbox_id=sandbox_id,
        conversation_id=conversation_id,
        prompt=prompt,
        llm_model=llm_model,
        initial_message_json=initial_message_json,
        initial_message_file=initial_message_file,
        system_message_suffix=system_message_suffix,
        processors_json=processors_json,
        processors_file=processors_file,
        selected_repository=selected_repository,
        selected_branch=selected_branch,
        git_provider=git_provider,
        suggested_task_json=suggested_task_json,
        suggested_task_file=suggested_task_file,
        title=title,
        trigger=trigger,
        pr_number=pr_number,
        parent_conversation_id=parent_conversation_id,
        agent_type=agent_type,
        public=public,
        plugins_json=plugins_json,
        plugins_file=plugins_file,
        plugin_json=plugin_json,
        secrets_json=secrets_json,
        secrets_file=secrets_file,
        secret=secret,
    )

    if print_payload:
        print(json.dumps(redact_secrets(payload), ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)

    if verbose:
        field_list = ", ".join(sorted(payload.keys()))
        print(f"[setup] creating V1 app conversation with explicit fields only: {field_list}", file=sys.stderr)
    await emit_event(event_sink, "start_payload_ready", "transport", "Prepared OpenHands start payload", {"fields": sorted(payload.keys())})

    started = await client.start_app_conversation(payload=payload, poll_interval=start_poll_interval, verbose_start=verbose)
    if verbose:
        print(f"[setup] conversation_id={started.conversation_id}", file=sys.stderr)
        if started.agent_server_url:
            print(f"[setup] agent_server_url={started.agent_server_url}", file=sys.stderr)
        if started.conversation_url:
            print(f"[setup] conversation_url={started.conversation_url}", file=sys.stderr)
    await emit_event(event_sink, "conversation_ready", "transport", "OpenHands start task became ready", {
        "conversation_id": started.conversation_id,
        "sandbox_id": started.sandbox_id,
        "websocket_url": started.conversation_url or started.agent_server_url or endpoint,
        "session_api_key": bool(started.session_api_key),
        "mode": "new",
    })

    terminal_task: asyncio.Task[JsonDict] | None = None
    if exit_when_terminal and rest_terminal_watch:
        terminal_task = asyncio.create_task(client.wait_until_terminal(started.conversation_id))

    final_text: str | None = None
    final_status: str | None = None
    seen_event_ids: set[str] = set()
    terminal_seen = False
    terminal_deadline: float | None = None
    saw_agent_activity = False
    saw_running_status = False

    event_iter = client.stream_v1_events(
        started,
        on_event=combined_event_callback,
        transport_event_sink=event_sink,
        raw_websocket=raw_websocket,
        open_timeout=websocket_open_timeout,
        retry_seconds=websocket_retry_seconds,
    )

    try:
        while True:
            timeout: float | None = None
            if terminal_seen and not final_text and terminal_deadline is not None:
                timeout = max(0.0, terminal_deadline - asyncio.get_running_loop().time())
            try:
                event = await anext(event_iter) if timeout is None else await asyncio.wait_for(anext(event_iter), timeout=timeout)
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                break
            except OpenHandsError as exc:
                await emit_event(event_sink, "websocket_stream_failed", "transport", f"WebSocket stream failed: {exc}", {"fallback": "rest_messages"})
                if verbose:
                    print(f"[warn] websocket stream failed; trying REST fallback: {exc}", file=sys.stderr)
                break

            event_id = event.get("id")
            if isinstance(event_id, str) and event_id:
                seen_event_ids.add(event_id)

            kind = str(event.get("kind") or "")
            if kind == "ActionEvent" or kind == "ObservationEvent" or is_agent_message(event):
                saw_agent_activity = True

            result_text = extract_assistant_result_text(event)
            if result_text.strip():
                final_text = result_text
                if terminal_seen and exit_when_terminal:
                    break

            if show_events or raw_events or debug_events:
                line = format_event(event, raw=raw_events, debug=debug_events)
                if line:
                    print(line, flush=True)

            status = extract_execution_status(event)
            if status == "running":
                saw_running_status = True
            if exit_when_terminal and status in TERMINAL_EXECUTION_STATES:
                final_status = status
                if final_text:
                    break
                if status == "finished" and not (saw_running_status or saw_agent_activity):
                    if verbose:
                        print("[warn] ignoring early execution_status=finished before agent activity", file=sys.stderr)
                    continue
                terminal_seen = True
                terminal_deadline = asyncio.get_running_loop().time() + max(0.0, terminal_grace_seconds)
                continue

            if exit_when_terminal and terminal_task and terminal_task.done():
                try:
                    info = terminal_task.result()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    print(f"[warn] terminal watcher failed; continuing websocket stream: {exc}", file=sys.stderr)
                    terminal_task = None
                    continue

                execution_status = str(info.get("execution_status") or "")
                sandbox_status = str(info.get("sandbox_status") or "")
                if final_text or execution_status in {"error", "stuck", "waiting_for_confirmation"}:
                    final_status = execution_status or sandbox_status or None
                    if final_text:
                        break
                    terminal_seen = True
                    terminal_deadline = asyncio.get_running_loop().time() + max(0.0, terminal_grace_seconds)
                    terminal_task = None
                    continue
                terminal_task = None
    finally:
        if terminal_task:
            terminal_task.cancel()
        await event_iter.aclose()

    text = final_text.strip() if final_text and final_text.strip() else ""
    if not text:
        await emit_event(event_sink, "rest_fallback_started", "transport", "Trying REST fallback for final text", {"conversation_id": started.conversation_id, "fallback": "rest_messages"})
        fallback_text = await client.fetch_final_text_fallback(started)
        text = fallback_text.strip() if fallback_text and fallback_text.strip() else ""
        await emit_event(event_sink, "rest_fallback_finished", "transport", "REST fallback completed", {"conversation_id": started.conversation_id, "fallback": "rest_messages", "chars": len(text)})
    await emit_event(event_sink, "result_collected", "transport", "Collected OpenHands result text", {"conversation_id": started.conversation_id, "chars": len(text), "last_status": final_status or ""})
    return OpenHandsRunResult(text=text, status=final_status, conversation_id=started.conversation_id, start=started, seen_event_ids=frozenset(seen_event_ids))


async def collect_started_conversation(
    *,
    endpoint: str,
    conversation: AppConversationStart,
    api_key: str | None = None,
    known_event_ids: set[str] | frozenset[str] | None = None,
    show_events: bool = False,
    raw_events: bool = False,
    debug_events: bool = False,
    raw_websocket: bool = False,
    event_callback: EventCallback | None = None,
    event_sink: EventSink | None = None,
    exit_when_terminal: bool = True,
    websocket_open_timeout: float = 20.0,
    websocket_retry_seconds: float = 240.0,
    terminal_grace_seconds: float = 15.0,
) -> OpenHandsRunResult:
    """Collect the result for an already-created OpenHands conversation."""
    client = OpenHandsClient(endpoint, api_key=api_key)
    verbose = bool(show_events or raw_events or debug_events or raw_websocket)
    transport_callback = _make_transport_event_callback(event_sink)

    def combined_event_callback(event: JsonDict) -> None:
        if event_callback:
            event_callback(event)
        if transport_callback:
            transport_callback(event)

    seen_event_ids: set[str] = set(known_event_ids or set())
    final_text: str | None = None
    final_status: str | None = None
    terminal_seen = False
    terminal_deadline: float | None = None
    saw_agent_activity = False
    saw_running_status = False

    event_iter = client.stream_v1_events(
        conversation,
        on_event=combined_event_callback,
        transport_event_sink=event_sink,
        raw_websocket=raw_websocket,
        open_timeout=websocket_open_timeout,
        retry_seconds=websocket_retry_seconds,
    )
    try:
        while True:
            timeout: float | None = None
            if terminal_seen and not final_text and terminal_deadline is not None:
                timeout = max(0.0, terminal_deadline - asyncio.get_running_loop().time())
            try:
                event = await anext(event_iter) if timeout is None else await asyncio.wait_for(anext(event_iter), timeout=timeout)
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                break
            except OpenHandsError as exc:
                await emit_event(event_sink, "websocket_stream_failed", "transport", f"WebSocket stream failed: {exc}", {"conversation_id": conversation.conversation_id, "fallback": "rest_messages"})
                if verbose:
                    print(f"[warn] websocket stream failed; trying REST fallback: {exc}", file=sys.stderr)
                break

            event_id = event.get("id")
            if isinstance(event_id, str) and event_id:
                if event_id in seen_event_ids:
                    continue
                seen_event_ids.add(event_id)

            kind = str(event.get("kind") or "")
            if kind == "ActionEvent" or kind == "ObservationEvent" or is_agent_message(event):
                saw_agent_activity = True

            result_text = extract_assistant_result_text(event)
            if result_text.strip():
                final_text = result_text
                if terminal_seen and exit_when_terminal:
                    break

            if show_events or raw_events or debug_events:
                line = format_event(event, raw=raw_events, debug=debug_events)
                if line:
                    print(line, flush=True)

            status = extract_execution_status(event)
            if status == "running":
                saw_running_status = True
            if exit_when_terminal and status in TERMINAL_EXECUTION_STATES:
                final_status = status
                if final_text:
                    break
                if status == "finished" and not (saw_running_status or saw_agent_activity):
                    if verbose:
                        print("[warn] ignoring early execution_status=finished before agent activity", file=sys.stderr)
                    continue
                terminal_seen = True
                terminal_deadline = asyncio.get_running_loop().time() + max(0.0, terminal_grace_seconds)
                continue
    finally:
        await event_iter.aclose()

    text = final_text.strip() if final_text and final_text.strip() else ""
    if not text:
        await emit_event(event_sink, "rest_fallback_started", "transport", "Trying REST fallback for existing conversation", {"conversation_id": conversation.conversation_id, "fallback": "rest_messages"})
        fallback_text = await client.fetch_final_text_fallback(conversation)
        text = fallback_text.strip() if fallback_text and fallback_text.strip() else ""
        await emit_event(event_sink, "rest_fallback_finished", "transport", "REST fallback completed", {"conversation_id": conversation.conversation_id, "fallback": "rest_messages", "chars": len(text)})
    await emit_event(event_sink, "result_collected", "transport", "Collected OpenHands result text", {"conversation_id": conversation.conversation_id, "chars": len(text), "last_status": final_status or ""})
    return OpenHandsRunResult(text=text, status=final_status, conversation_id=conversation.conversation_id, start=conversation, seen_event_ids=frozenset(seen_event_ids))


async def run_followup_message_and_collect(
    *,
    endpoint: str,
    conversation: AppConversationStart,
    prompt: str,
    api_key: str | None = None,
    known_event_ids: set[str] | frozenset[str] | None = None,
    show_events: bool = False,
    raw_events: bool = False,
    debug_events: bool = False,
    raw_websocket: bool = False,
    event_callback: EventCallback | None = None,
    event_sink: EventSink | None = None,
    exit_when_terminal: bool = True,
    websocket_open_timeout: float = 20.0,
    websocket_retry_seconds: float = 240.0,
    terminal_grace_seconds: float = 15.0,
) -> OpenHandsRunResult:
    """Send a follow-up prompt to an existing conversation and collect its answer."""
    client = OpenHandsClient(endpoint, api_key=api_key)
    verbose = bool(show_events or raw_events or debug_events or raw_websocket)
    transport_callback = _make_transport_event_callback(event_sink)

    def combined_event_callback(event: JsonDict) -> None:
        if event_callback:
            event_callback(event)
        if transport_callback:
            transport_callback(event)

    seen_event_ids: set[str] = set(known_event_ids or set())
    conversation = await client._refresh_app_conversation_start_metadata(conversation, verbose=raw_websocket)
    await emit_event(event_sink, "conversation_metadata_refreshed", "transport", "Refreshed OpenHands conversation metadata", {
        "conversation_id": conversation.conversation_id,
        "sandbox_id": conversation.sandbox_id,
        "websocket_url": conversation.conversation_url or conversation.agent_server_url or endpoint,
        "session_api_key": bool(conversation.session_api_key),
        "mode": "followup",
    })

    if verbose:
        print(f"[followup] sending message to existing conversation {conversation.conversation_id}", file=sys.stderr)
    await emit_event(event_sink, "followup_sent", "transport", "Sent follow-up message to existing conversation", {"conversation_id": conversation.conversation_id, "mode": "followup", "followup": True})
    await client.send_message_to_existing_conversation(conversation, prompt, run=True)

    final_text: str | None = None
    final_status: str | None = None
    terminal_seen = False
    terminal_deadline: float | None = None
    saw_agent_activity = False
    saw_running_status = False

    event_iter = client.stream_v1_events(
        conversation,
        on_event=combined_event_callback,
        transport_event_sink=event_sink,
        raw_websocket=raw_websocket,
        open_timeout=websocket_open_timeout,
        retry_seconds=websocket_retry_seconds,
    )
    try:
        while True:
            timeout: float | None = None
            if terminal_seen and not final_text and terminal_deadline is not None:
                timeout = max(0.0, terminal_deadline - asyncio.get_running_loop().time())
            try:
                event = await anext(event_iter) if timeout is None else await asyncio.wait_for(anext(event_iter), timeout=timeout)
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                break
            except OpenHandsError as exc:
                await emit_event(event_sink, "websocket_stream_failed", "transport", f"Follow-up WebSocket failed: {exc}", {"conversation_id": conversation.conversation_id, "fallback": "rest_messages", "mode": "followup"})
                if verbose:
                    print(f"[warn] follow-up websocket failed; trying REST fallback: {exc}", file=sys.stderr)
                break

            event_id = event.get("id")
            if isinstance(event_id, str) and event_id:
                if event_id in seen_event_ids:
                    continue
                seen_event_ids.add(event_id)

            kind = str(event.get("kind") or "")
            if kind == "ActionEvent" or kind == "ObservationEvent" or is_agent_message(event):
                saw_agent_activity = True

            result_text = extract_assistant_result_text(event)
            if result_text.strip():
                final_text = result_text
                if terminal_seen and exit_when_terminal:
                    break

            if show_events or raw_events or debug_events:
                line = format_event(event, raw=raw_events, debug=debug_events)
                if line:
                    print(line, flush=True)

            status = extract_execution_status(event)
            if status == "running":
                saw_running_status = True
            if exit_when_terminal and status in TERMINAL_EXECUTION_STATES:
                final_status = status
                if final_text:
                    break
                if status == "finished" and not (saw_running_status or saw_agent_activity):
                    if verbose:
                        print("[warn] ignoring replayed/early follow-up execution_status=finished", file=sys.stderr)
                    continue
                terminal_seen = True
                terminal_deadline = asyncio.get_running_loop().time() + max(0.0, terminal_grace_seconds)
                continue
    finally:
        await event_iter.aclose()

    text = final_text.strip() if final_text and final_text.strip() else ""
    if not text:
        await emit_event(event_sink, "rest_fallback_started", "transport", "Trying REST fallback for follow-up", {"conversation_id": conversation.conversation_id, "fallback": "rest_messages", "mode": "followup"})
        fallback_text = await client.fetch_final_text_fallback(conversation)
        text = fallback_text.strip() if fallback_text and fallback_text.strip() else ""
        await emit_event(event_sink, "rest_fallback_finished", "transport", "REST fallback completed", {"conversation_id": conversation.conversation_id, "fallback": "rest_messages", "chars": len(text), "mode": "followup"})
    await emit_event(event_sink, "result_collected", "transport", "Collected OpenHands result text", {"conversation_id": conversation.conversation_id, "chars": len(text), "last_status": final_status or "", "mode": "followup"})
    return OpenHandsRunResult(text=text, status=final_status, conversation_id=conversation.conversation_id, start=conversation, seen_event_ids=frozenset(seen_event_ids))


async def run_prompt_and_watch(
    *,
    endpoint: str,
    api_key: str | None = None,
    show_events: bool = False,
    raw_events: bool = False,
    debug_events: bool = False,
    raw_websocket: bool = False,
    exit_when_terminal: bool = True,
    start_poll_interval: float = 5.0,
    websocket_open_timeout: float = 20.0,
    websocket_retry_seconds: float = 240.0,
    terminal_grace_seconds: float = 15.0,
    rest_terminal_watch: bool = False,
    print_payload: bool = False,
    payload_file: str | None = None,
    payload_json: str | None = None,
    param_json: list[str] | None = None,
    sandbox_id: str | None = None,
    conversation_id: str | None = None,
    prompt: str | None = None,
    llm_model: str | None = None,
    initial_message_json: str | None = None,
    initial_message_file: str | None = None,
    system_message_suffix: str | None = None,
    processors_json: str | None = None,
    processors_file: str | None = None,
    selected_repository: str | None = None,
    selected_branch: str | None = None,
    git_provider: str | None = None,
    suggested_task_json: str | None = None,
    suggested_task_file: str | None = None,
    title: str | None = None,
    trigger: str | None = None,
    pr_number: list[int] | None = None,
    parent_conversation_id: str | None = None,
    agent_type: str | None = None,
    public: bool | None = None,
    plugins_json: str | None = None,
    plugins_file: str | None = None,
    plugin_json: list[str] | None = None,
    secrets_json: str | None = None,
    secrets_file: str | None = None,
    secret: list[str] | None = None,
) -> int:
    try:
        result = await run_conversation_and_collect(
            endpoint=endpoint,
            api_key=api_key,
            show_events=show_events,
            raw_events=raw_events,
            debug_events=debug_events,
            raw_websocket=raw_websocket,
            exit_when_terminal=exit_when_terminal,
            start_poll_interval=start_poll_interval,
            websocket_open_timeout=websocket_open_timeout,
            websocket_retry_seconds=websocket_retry_seconds,
            terminal_grace_seconds=terminal_grace_seconds,
            rest_terminal_watch=rest_terminal_watch,
            print_payload=print_payload,
            payload_file=payload_file,
            payload_json=payload_json,
            param_json=param_json,
            sandbox_id=sandbox_id,
            conversation_id=conversation_id,
            prompt=prompt,
            llm_model=llm_model,
            initial_message_json=initial_message_json,
            initial_message_file=initial_message_file,
            system_message_suffix=system_message_suffix,
            processors_json=processors_json,
            processors_file=processors_file,
            selected_repository=selected_repository,
            selected_branch=selected_branch,
            git_provider=git_provider,
            suggested_task_json=suggested_task_json,
            suggested_task_file=suggested_task_file,
            title=title,
            trigger=trigger,
            pr_number=pr_number,
            parent_conversation_id=parent_conversation_id,
            agent_type=agent_type,
            public=public,
            plugins_json=plugins_json,
            plugins_file=plugins_file,
            plugin_json=plugin_json,
            secrets_json=secrets_json,
            secrets_file=secrets_file,
            secret=secret,
        )
    except KeyboardInterrupt:
        print("\n[interrupt] stopped by user", file=sys.stderr)
        return 130

    print_final_result(
        result.text,
        status=result.status,
        decorated=bool(show_events or raw_events or debug_events),
    )
    return 0


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

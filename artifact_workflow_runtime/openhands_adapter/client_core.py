from __future__ import annotations

import asyncio
from dataclasses import replace
import inspect
import json
import sys
from typing import Any, AsyncIterator
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
import websockets

from artifact_workflow_runtime.runtime_events import EventSink

from .errors import OpenHandsError, OpenHandsHTTPError
from .events import (
    EventCallback,
    TERMINAL_EXECUTION_STATES,
    TERMINAL_SANDBOX_STATES,
    _find_first_string_by_key,
    _recursive_assistant_texts,
    _response_text_snippet,
    _transport_note,
    looks_like_openhands_html,
)
from .models import AppConversationStart, JsonDict

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
                texts = [text for text in _recursive_assistant_texts(data) if not looks_like_openhands_html(text)]
                if texts:
                    return texts[-1]
        return ""


    async def _refresh_app_conversation_start_metadata(
        self,
        conversation: AppConversationStart,
        *,
        verbose: bool = False,
    ) -> AppConversationStart:
        """Refresh runtime URL/session fields for a conversation if available.

        Follow-up calls should avoid extra metadata round-trips when the
        conversation already contains enough websocket/auth information. This
        keeps the existing-conversation path simple and avoids turning a working
        chat thread into a follow-up failure because of an auxiliary refresh
        request.
        """
        if (conversation.session_api_key and conversation.conversation_url) or (conversation.session_api_key and conversation.agent_server_url):
            return conversation
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
        return replace(
            conversation,
            conversation_url=conversation_url,
            agent_server_url=agent_server_url,
            session_api_key=session_api_key,
            raw_conversation=info,
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

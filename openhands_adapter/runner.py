from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from artifact_workflow_runtime.runtime_events import EventSink, emit_event

from .client_core import OpenHandsClient
from .errors import OpenHandsError
from .events import (
    EventCallback,
    TERMINAL_EXECUTION_STATES,
    _make_transport_event_callback,
    _stats_line,
    extract_assistant_result_text,
    extract_execution_status,
    format_event,
    is_agent_message,
    looks_like_openhands_html,
    print_final_result,
)
from .models import AppConversationStart, JsonDict, OpenHandsRunResult
from .payload import build_app_conversation_payload, redact_secrets

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
                if looks_like_openhands_html(result_text):
                    await emit_event(
                        event_sink,
                        "agent_result_html_suppressed",
                        "transport",
                        "Suppressed OpenHands web UI HTML returned where agent result text was expected",
                        {"conversation_id": started.conversation_id},
                    )
                else:
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
                if looks_like_openhands_html(result_text):
                    await emit_event(
                        event_sink,
                        "agent_result_html_suppressed",
                        "transport",
                        "Suppressed OpenHands web UI HTML returned where agent result text was expected",
                        {"conversation_id": conversation.conversation_id},
                    )
                else:
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
    await client.send_message_to_existing_conversation(conversation, prompt, run=True)
    await emit_event(event_sink, "followup_sent", "transport", "Sent follow-up message to existing conversation", {"conversation_id": conversation.conversation_id, "mode": "followup", "followup": True})

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
                if looks_like_openhands_html(result_text):
                    await emit_event(
                        event_sink,
                        "agent_result_html_suppressed",
                        "transport",
                        "Suppressed OpenHands web UI HTML returned where agent result text was expected",
                        {"conversation_id": conversation.conversation_id},
                    )
                else:
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

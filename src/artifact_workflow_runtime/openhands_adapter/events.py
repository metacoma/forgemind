from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, Callable

from artifact_workflow_runtime.runtime_events import EventSink, emit_event

from .models import JsonDict

EventCallback = Callable[[JsonDict], None]
OPENHANDS_HTML_MARKERS = ("<!doctype html", "<html", "reactrouter", "window.__reactroutercontext", "let&#x27;s start building", "<title>openhands</title>")

def looks_like_openhands_html(text: str) -> bool:
    lowered = text.strip().lower()
    return bool(lowered) and any(marker in lowered for marker in OPENHANDS_HTML_MARKERS)


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

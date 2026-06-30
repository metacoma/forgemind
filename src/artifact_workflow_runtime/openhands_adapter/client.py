from __future__ import annotations

# Compatibility facade. The OpenHands integration is intentionally split into
# focused modules now: payload construction, event normalization, low-level
# client operations, and run orchestration. Keep public imports stable for CLI,
# TUI, tests, and external users that import from openhands_adapter.client.

from .client_core import OpenHandsClient
from .errors import OpenHandsError, OpenHandsHTTPError
from .events import (
    EventCallback,
    OPENHANDS_HTML_MARKERS,
    TERMINAL_EXECUTION_STATES,
    TERMINAL_SANDBOX_STATES,
    extract_assistant_result_text,
    extract_execution_status,
    extract_finish_action_text,
    extract_message_text,
    format_event,
    is_agent_message,
    looks_like_openhands_html,
    print_final_result,
)
from .payload import (
    build_app_conversation_payload,
    build_initial_message_from_prompt,
    load_json_array,
    load_json_array_file,
    load_json_file,
    load_json_object,
    load_json_object_file,
    load_json_value,
    parse_key_value,
    redact_secrets,
    set_if_not_none,
)
from .runner import (
    collect_started_conversation,
    find_reusable_sandbox_for_model,
    run_conversation_and_collect,
    run_followup_message_and_collect,
    run_prompt_and_watch,
)

__all__ = [
    "EventCallback",
    "OPENHANDS_HTML_MARKERS",
    "TERMINAL_EXECUTION_STATES",
    "TERMINAL_SANDBOX_STATES",
    "OpenHandsClient",
    "OpenHandsError",
    "OpenHandsHTTPError",
    "build_app_conversation_payload",
    "build_initial_message_from_prompt",
    "collect_started_conversation",
    "extract_assistant_result_text",
    "extract_execution_status",
    "extract_finish_action_text",
    "extract_message_text",
    "find_reusable_sandbox_for_model",
    "format_event",
    "is_agent_message",
    "load_json_array",
    "load_json_array_file",
    "load_json_file",
    "load_json_object",
    "load_json_object_file",
    "load_json_value",
    "looks_like_openhands_html",
    "parse_key_value",
    "print_final_result",
    "redact_secrets",
    "run_conversation_and_collect",
    "run_followup_message_and_collect",
    "run_prompt_and_watch",
    "set_if_not_none",
]

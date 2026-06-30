from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import JsonDict
from .errors import OpenHandsError

SECRET_FIELD_NAMES = {"secrets", "secret", "api_key", "token", "password", "key"}

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

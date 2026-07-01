from __future__ import annotations

import json
from typing import Any

from artifact_workflow_runtime.openhands_adapter.client import extract_message_text


def format_conversation_messages(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in messages:
        role = str(item.get("role") or item.get("source") or item.get("type") or "message")
        text = extract_message_text(item).strip()
        if not text:
            llm_message = item.get("llm_message")
            if isinstance(llm_message, dict):
                role = str(llm_message.get("role") or role)
            text = json.dumps(item, ensure_ascii=False, indent=2)
        lines.append(f"[{role}]\n{text}")
    return "\n\n".join(lines) if lines else "No messages available for this conversation."


def build_conversation_details(row: dict[str, Any]) -> str:
    details = {
        "conversation_id": row.get("conversation_id", ""),
        "sandbox_id": row.get("sandbox_id", ""),
        "status": row.get("status", ""),
        "title": row.get("title", ""),
        "updated_at": row.get("updated_at", ""),
        "mode": row.get("mode", ""),
        "websocket_url": row.get("websocket_url", ""),
    }
    return json.dumps(details, ensure_ascii=False, indent=2)

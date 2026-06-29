from __future__ import annotations

import json

from artifact_workflow_runtime.models import ContextPacket, Task


CLASSIFICATION_SCHEMA_HINT = {
    "normalized_task": "string",
    "needs_world_facts": "boolean",
    "execution_family": "documentation_only|repository_change|host_operation|cluster_operation|network_investigation",
    "capabilities": ["repo_read"],
    "observation_focus": ["string"],
    "reasoning": "string",
    "risk_level": "low|medium|high",
}

PLAN_SCHEMA_HINT = {
    "summary": "string",
    "execution_family": "documentation_only|repository_change|host_operation|cluster_operation|network_investigation",
    "capabilities": ["repo_read"],
    "steps": ["string"],
    "success_criteria": ["string"],
    "verification_checks": ["string"],
    "requires_mutation": "boolean",
    "reasoning": "string",
}


def build_classification_prompt(task: Task) -> str:
    return (
        "Classify the task for a controller-driven workflow.\n"
        "You only see text. Do not assume filesystem or runtime access.\n"
        "Return strict JSON matching this shape:\n"
        f"{json.dumps(CLASSIFICATION_SCHEMA_HINT, ensure_ascii=False, indent=2)}\n\n"
        f"Task title: {task.title or ''}\n"
        f"Task description:\n{task.description}\n"
        f"Repository: {task.repository or 'n/a'}\n"
        f"Branch: {task.branch or 'n/a'}\n"
    )


def build_plan_prompt(task: Task, context_packet: ContextPacket) -> str:
    return (
        "Produce an execution plan for a controller-driven workflow.\n"
        "You only see text from a ContextPacket.\n"
        "Return strict JSON matching this shape:\n"
        f"{json.dumps(PLAN_SCHEMA_HINT, ensure_ascii=False, indent=2)}\n\n"
        f"Task:\n{task.description}\n\n"
        f"ContextPacket:\n{context_packet.text}\n"
    )

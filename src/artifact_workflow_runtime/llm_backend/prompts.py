from __future__ import annotations

import json

from artifact_workflow_runtime.models import ContextPacket, ExecutionPlan, ExecutionResult, Task


CLASSIFICATION_SCHEMA_HINT = {
    "normalized_task": "string",
    "needs_world_facts": "boolean",
    "execution_family": "documentation_only|repository_change|host_operation|cluster_operation|network_investigation",
    "task_intent": "implement|modify|investigate|document|verify",
    "capabilities": ["repo_read"],
    "observation_focus": ["string"],
    "reasoning": "string",
    "risk_level": "low|medium|high",
}

PLAN_SCHEMA_HINT = {
    "summary": "string",
    "execution_family": "documentation_only|repository_change|host_operation|cluster_operation|network_investigation",
    "task_intent": "implement|modify|investigate|document|verify",
    "deliverable_kind": "repository_changes|host_changes|cluster_changes|network_findings|documentation|analysis",
    "capabilities": ["repo_read"],
    "steps": ["string"],
    "success_criteria": ["string"],
    "verification_checks": ["string"],
    "requires_mutation": "boolean",
    "must_change_world": "boolean",
    "expected_repo_changes": ["string"],
    "reasoning": "string",
}

VERIFICATION_SCHEMA_HINT = {
    "passed": "boolean",
    "summary": "string",
    "checks_passed": ["string"],
    "checks_failed": ["string"],
    "missing_evidence": ["string"],
    "confidence": "low|medium|high",
    "reasoning": "string",
}


def build_classification_prompt(task: Task) -> str:
    return (
        "Classify the task for a controller-driven workflow.\n"
        "You only see text. Do not assume filesystem or runtime access.\n"
        "All target repositories, hosts, clusters, and systems are described only in the task text.\n"
        "Infer the user's real task intent. If the user asks to add, implement, fix, modify, update, extend, remove, or create something, task_intent must be implement or modify, not document or investigate.\n"
        "Return strict JSON matching this shape:\n"
        f"{json.dumps(CLASSIFICATION_SCHEMA_HINT, ensure_ascii=False, indent=2)}\n\n"
        f"Task title: {task.title or ''}\n"
        f"Task description:\n{task.description}\n"
    )


def build_plan_prompt(task: Task, context_packet: ContextPacket, task_intent: str) -> str:
    return (
        "Produce an execution plan for a controller-driven workflow.\n"
        "You only see text from a ContextPacket.\n"
        "All target repositories, hosts, clusters, and systems are described only in the task text or observed evidence.\n"
        "Preserve the user's actual intent.\n"
        "If task_intent is implement or modify, the plan must end in real world changes and must not degrade into design-only, outline-only, instructions-only, or documentation-only work.\n"
        "For repository_change implementation tasks, include concrete file changes, build/test actions, and expected repository outputs.\n"
        "Return strict JSON matching this shape:\n"
        f"{json.dumps(PLAN_SCHEMA_HINT, ensure_ascii=False, indent=2)}\n\n"
        f"Task intent: {task_intent}\n\n"
        f"Task:\n{task.description}\n\n"
        f"ContextPacket:\n{context_packet.text}\n"
    )


def build_verification_prompt(task: Task, context_packet: ContextPacket, plan: ExecutionPlan, execution: ExecutionResult) -> str:
    return (
        "Verify the result using evidence only.\n"
        "You do not have live access to the world.\n"
        "Judge pass/fail strictly from the task, observation evidence, plan, success criteria, verification checks, and execution evidence.\n"
        "Do not invent missing facts. If evidence is missing, say so explicitly.\n"
        "Return strict JSON matching this shape:\n"
        f"{json.dumps(VERIFICATION_SCHEMA_HINT, ensure_ascii=False, indent=2)}\n\n"
        f"Task:\n{task.description}\n\n"
        f"ContextPacket:\n{context_packet.text}\n\n"
        f"Plan summary: {plan.summary}\n"
        f"Success criteria:\n" + "\n".join(f"- {item}" for item in plan.success_criteria) + "\n\n"
        f"Verification checks:\n" + "\n".join(f"- {item}" for item in plan.verification_checks) + "\n\n"
        f"Execution evidence:\n{execution.evidence_text}\n"
    )

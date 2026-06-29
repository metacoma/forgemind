from __future__ import annotations

import json

from artifact_workflow_runtime.models import ContextPacket, ExecutionPlan, ExecutionResult, PublishResult, Task, TaskClassification


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

ROUTE_SCHEMA_HINT = {
    "needs_repository_observation": "boolean",
    "needs_world_observation": "boolean",
    "needs_fresh_external_research": "boolean",
    "can_plan_immediately": "boolean",
    "required_evidence_types": ["repo_structure|repo_patterns|host_state|cluster_state|network_state|official_docs|package_versions|release_notes|api_examples|build_instructions|unknown"],
    "research_targets": ["string"],
    "observation_focus": ["string"],
    "reasoning": "string",
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
    "required_test_levels": ["build|unit|integration|smoke|lint"],
    "required_setup_steps": ["string"],
    "require_commit": "boolean",
    "require_push": "boolean",
    "execution_environment": "docker_container|host|cluster",
    "environment_notes": ["string"],
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
    "performed_test_levels": ["string"],
    "missing_test_levels": ["string"],
    "setup_steps_performed": ["string"],
    "missing_setup_steps": ["string"],
    "commit_required": "boolean",
    "push_required": "boolean",
    "commit_done": "boolean",
    "push_done": "boolean",
    "missing_obligations": ["string"],
    "completion_status": "completed|implemented_not_verified|verified_not_published|partially_completed|blocked",
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


def build_route_prompt(task: Task, classification: TaskClassification) -> str:
    return (
        "Decide what evidence must be collected before planning.\n"
        "You are a narrow route-analysis step in a controller-driven workflow.\n"
        "You do not produce a plan. You only decide whether planning requires additional evidence first.\n"
        "You only see text. Do not assume filesystem or runtime access.\n"
        "Fresh external research means current official docs, package versions, release notes, migration guides, or current API syntax that may have changed since model training.\n"
        "Repository observation means inspecting the actual repository, files, build configuration, existing implementations, test topology, and dependency setup in the workspace.\n"
        "World observation means inspecting actual hosts, clusters, services, logs, commands, or network state in the environment.\n"
        "If fresh external knowledge is needed, set needs_fresh_external_research=true.\n"
        "If repository or world facts are needed before planning, set the corresponding observation flags and can_plan_immediately=false.\n"
        "Return strict JSON matching this shape:\n"
        f"{json.dumps(ROUTE_SCHEMA_HINT, ensure_ascii=False, indent=2)}\n\n"
        f"Task:\n{task.description}\n\n"
        "Classification:\n"
        f"execution_family={classification.execution_family.value}\n"
        f"task_intent={classification.task_intent}\n"
        f"observation_focus={classification.observation_focus}\n"
        f"capabilities={[cap.value for cap in classification.capabilities]}\n"
    )


def build_plan_prompt(task: Task, context_packet: ContextPacket, task_intent: str) -> str:
    return (
        "Produce an execution plan for a controller-driven workflow.\n"
        "You only see text from a ContextPacket.\n"
        "All target repositories, hosts, clusters, and systems are described only in the task text or observed evidence.\n"
        "The execution environment is a Docker container unless evidence says otherwise.\n"
        "Preserve the user's actual intent.\n"
        "If task_intent is implement or modify, the plan must end in real world changes and must not degrade into design-only, outline-only, instructions-only, or documentation-only work.\n"
        "For repository_change implementation tasks, include concrete file changes, required dependency installation/setup steps, build/test actions, and expected repository outputs.\n"
        "If the change adds a new client, runtime, integration surface, or compatibility layer, require the necessary integration tests unless evidence proves they do not exist or cannot be run.\n"
        "If tests need extra dependencies inside Docker, include the setup steps explicitly.\n"
        "If the task should leave the repository deliverable-ready, set require_commit and require_push accordingly.\n"
        "Return strict JSON matching this shape:\n"
        f"{json.dumps(PLAN_SCHEMA_HINT, ensure_ascii=False, indent=2)}\n\n"
        f"Task intent: {task_intent}\n\n"
        f"Task:\n{task.description}\n\n"
        f"ContextPacket:\n{context_packet.text}\n"
    )


def build_verification_prompt(task: Task, context_packet: ContextPacket, plan: ExecutionPlan, execution: ExecutionResult, publish: PublishResult | None = None) -> str:
    publish_text = publish.evidence_text if publish else "No separate publish step evidence was captured."
    return (
        "Verify the result using evidence only.\n"
        "You do not have live access to the world.\n"
        "Judge pass/fail strictly from the task, observation evidence, plan, success criteria, verification checks, execution evidence, and publish evidence if present.\n"
        "Do not invent missing facts. If evidence is missing, say so explicitly.\n"
        "The execution environment is a Docker container unless evidence says otherwise.\n"
        "Check whether required setup/dependency installation happened before running build, unit, and integration tests.\n"
        "Check whether the performed test levels are sufficient for the change. Unit-only evidence is insufficient when the plan required integration tests.\n"
        "Check whether commit/push obligations were fulfilled if they were required by the plan.\n"
        "Return strict JSON matching this shape:\n"
        f"{json.dumps(VERIFICATION_SCHEMA_HINT, ensure_ascii=False, indent=2)}\n\n"
        f"Task:\n{task.description}\n\n"
        f"ContextPacket:\n{context_packet.text}\n\n"
        f"Plan summary: {plan.summary}\n"
        f"Required test levels: {plan.required_test_levels}\n"
        f"Required setup steps: {plan.required_setup_steps}\n"
        f"Require commit: {plan.require_commit}\n"
        f"Require push: {plan.require_push}\n"
        f"Execution environment: {plan.execution_environment}\n"
        + "Success criteria:\n"
        + "\n".join(f"- {item}" for item in plan.success_criteria)
        + "\n\nVerification checks:\n"
        + "\n".join(f"- {item}" for item in plan.verification_checks)
        + f"\n\nExecution evidence:\n{execution.evidence_text}\n\nPublish evidence:\n{publish_text}\n"
    )

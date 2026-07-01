from __future__ import annotations

import json

from artifact_workflow_runtime.evidence import render_structured_evidence_summary
from artifact_workflow_runtime.models import Capability, ContextPacket, ExecutionPlan, ExecutionResult, ObligationAnalysis, PublishResult, RoutingDecision, Task, TaskClassification


ALLOWED_CAPABILITY_VALUES = [cap.value for cap in Capability]


CLASSIFICATION_SCHEMA_HINT = {
    "normalized_task": "string",
    "needs_world_facts": "boolean",
    "execution_family": "documentation_only|repository_change|host_operation|cluster_operation|network_investigation",
    "task_intent": "implement|modify|investigate|document|verify",
    "capabilities": ALLOWED_CAPABILITY_VALUES[:1],
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
    "capabilities": ALLOWED_CAPABILITY_VALUES[:1],
    "steps": ["string"],
    "publication_steps": ["string"],
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



OBLIGATION_SCHEMA_HINT = {
    "required_test_levels": ["build|unit|component|integration|e2e|smoke|lint"],
    "required_setup_steps": ["string"],
    "required_environment_conditions": ["string"],
    "required_documentation_updates": ["README|user docs|developer docs|API docs|migration notes"],
    "required_examples_updates": ["examples|snippets|samples|usage demos"],
    "required_ci_updates": ["workflow changes|CI jobs|build scripts|packaging checks"],
    "required_codegen_or_build_updates": ["proto/codegen/tooling/build config updates"],
    "affected_surfaces": ["public API|client binding|integration path|build surface|docs surface"],
    "adjacent_components": ["string"],
    "discovered_impacts": [
        {
            "kind": "code|test|integration|setup|documentation|examples|ci_build|codegen_tooling|publish|research|observation",
            "summary": "string",
            "required": True,
            "blocking": True,
            "affected_paths": ["string"],
            "evidence_artifact_ids": ["string"],
        }
    ],
    "work_surface": {
        "affected_surfaces": ["string"],
        "impacts": [],
        "adjacent_components": ["string"],
        "reasoning": "string",
    },
    "required_publish_actions": ["commit|push|create_pr|wait_pr_checks"],
    "completion_requirements": ["string"],
    "blocker_conditions": ["string"],
    "reasoning_summary": "string",
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
    "pr_detected": "boolean",
    "pr_checks_waited": "boolean",
    "pr_checks_passed": ["string"],
    "pr_checks_failed": ["string"],
    "pr_checks_pending": ["string"],
    "missing_obligations": ["string"],
    "completion_status": "completed|implemented_not_verified|verified_not_published|partially_completed|blocked",
}



def _operational_evidence_text(result: ExecutionResult | PublishResult | None, *, fallback: str) -> str:
    if result is None:
        return fallback
    parts: list[str] = []
    if result.evidence_bundle is not None:
        parts.append("Structured EvidenceBundle operational summary:")
        parts.append(result.evidence_bundle.operational_summary())
    parts.append("StructuredEvidence:")
    parts.append(render_structured_evidence_summary(result.structured_evidence))
    if result.raw_evidence_artifact_id:
        parts.append(f"Raw evidence artifact: {result.raw_evidence_artifact_id} (raw text is supplement, not source of truth)")
    if not parts:
        parts.append(result.evidence_text)
    return "\n".join(part for part in parts if part).strip()

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


def build_plan_prompt(task: Task, context_packet: ContextPacket, task_intent: str, obligations: ObligationAnalysis, reconciliation: object | None = None) -> str:
    reconciliation_block = ""
    if reconciliation is not None:
        payload = reconciliation.model_dump(mode="json") if hasattr(reconciliation, "model_dump") else reconciliation
        reconciliation_block = "Workspace reconciliation:\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n\n"
    return (
        "Produce an execution plan for a controller-driven workflow.\n"
        "You only see text from a ContextPacket.\n"
        "All target repositories, hosts, clusters, and systems are described only in the task text or observed evidence.\n"
        "The execution environment is a Docker container unless evidence says otherwise.\n"
        "Freshness/retrieval artifacts in the ContextPacket are the truth layer for current docs, versions, changelog, CLI flags, compatibility, and migration facts; do not override them with stale model memory.\n"
        "Preserve the user's actual intent.\n"
        "If task_intent is implement or modify, the plan must end in real world changes and must not degrade into design-only, outline-only, instructions-only, or documentation-only work.\n"
        "If Workspace reconciliation says delivery_mode is continue_existing_candidate or complete_existing_candidate, treat the existing surface as adopted candidate work and continue with modify/repair/complete semantics; verify-only plans are forbidden.\n"
        "When existing code is already present, do not reinterpret that as task completion. Use passed_obligations and unresolved_obligations to decide what still must change or be repaired.\n"
        "For repository_change implementation tasks, include concrete file changes, required dependency installation/setup steps, build/test actions, and expected repository outputs.\n"
        "If the change adds a new client, runtime, integration surface, or compatibility layer, require the necessary integration tests unless evidence proves they do not exist or cannot be run.\n"
        "If tests need extra dependencies inside Docker, include the setup steps explicitly.\n"
        "If the task should leave the repository deliverable-ready, set require_commit and require_push accordingly.\n"
        "If the task involves opening or updating a pull request, repository publication is not complete until PR checks are awaited and assessed.\n"
        "Return strict JSON matching this shape:\n"
        f"{json.dumps(PLAN_SCHEMA_HINT, ensure_ascii=False, indent=2)}\n\n"
        f"Task intent: {task_intent}\n\n"
        + reconciliation_block
        + f"Task:\n{task.description}\n\n"
        + f"ContextPacket:\n{context_packet.text}\n"
    )



def build_obligation_analysis_prompt(task: Task, classification: TaskClassification, route: RoutingDecision, context_packet: ContextPacket) -> str:
    return (
        "Synthesize execution obligations from observed evidence before planning.\n"
        "You are not producing an execution plan. You are extracting mandatory obligations from the task plus evidence.\n"
        "Do not weaken or skip evidence-backed requirements.\n"
        "Discovery is broad: for a feature/API/client/binding/integration path, identify the whole required work surface, not only the main code change.\n"
        "Explicitly discover obligations for code, build/config, tests, integration/e2e/smoke, environment setup, documentation, examples/snippets, CI/pipeline, codegen/tooling, packaging, and adjacent components.\n"
        "If a public API/client/binding or user-facing behavior changes, require user/developer/API docs and examples unless evidence proves none exist.\n"
        "If proto/generated code/build tooling/CI workflows are affected, require codegen/build/CI obligations.\n"
        "If the repository evidence shows an integration harness, integration scripts, or setup scripts for runtime dependencies such as Freeplane inside Docker, require them when the change touches the same functional surface.\n"
        "Prefer semantic reasoning over surface wording.\n"
        "Return strict JSON matching this shape:\n"
        f"{json.dumps(OBLIGATION_SCHEMA_HINT, ensure_ascii=False, indent=2)}\n\n"
        f"Task:\n{task.description}\n\n"
        "Classification:\n"
        f"execution_family={classification.execution_family.value}\n"
        f"task_intent={classification.task_intent}\n"
        f"capabilities={[cap.value for cap in classification.capabilities]}\n\n"
        "Route decision:\n"
        f"needs_repository_observation={route.needs_repository_observation}\n"
        f"needs_world_observation={route.needs_world_observation}\n"
        f"needs_fresh_external_research={route.needs_fresh_external_research}\n"
        f"required_evidence_types={route.required_evidence_types}\n\n"
        f"ContextPacket:\n{context_packet.text}\n"
    )

def build_verification_prompt(task: Task, context_packet: ContextPacket, plan: ExecutionPlan, execution: ExecutionResult, publish: PublishResult | None = None) -> str:
    publish_text = _operational_evidence_text(publish, fallback="No separate publish step evidence was captured.")
    return (
        "Verify the result using evidence only.\n"
        "You do not have live access to the world.\n"
        "Judge pass/fail strictly from the task, observation evidence, retrieval artifacts, plan, success criteria, verification checks, execution evidence, and publish evidence if present.\n"
        "Freshness/retrieval artifacts in the ContextPacket are the truth layer for current docs, versions, changelog, CLI flags, compatibility, and migration facts; do not verify against stale model memory.\n"
        "Do not invent missing facts. If evidence is missing, say so explicitly.\n"
        "The execution environment is a Docker container unless evidence says otherwise.\n"
        "Check whether required setup/dependency installation happened before running build, unit, and integration tests.\n"
        "Check whether the performed test levels are sufficient for the change. Unit-only evidence is insufficient when the plan required integration tests.\n"
        "Check publish obligations only when publish evidence is present; before publish, missing commit/push/PR is deferred, not an execution failure.\n"
        "If publish evidence indicates that a PR exists, check whether the workflow waited for all PR checks to complete and whether all of them passed.\n"
        "If any PR checks failed, classify publish verification as failed/blocked and report the failing jobs as controller repair input; publisher must not fix CI inside publish.\n"
        "Return strict JSON matching this shape:\n"
        f"{json.dumps(VERIFICATION_SCHEMA_HINT, ensure_ascii=False, indent=2)}\n\n"
        f"Task:\n{task.description}\n\n"
        f"ContextPacket:\n{context_packet.text}\n\n"
        f"Plan summary: {plan.summary}\n"
        f"Required test levels: {plan.required_test_levels}\n"
        f"Required setup steps: {plan.required_setup_steps}\n"
        f"Publish required later: {plan.require_commit or plan.require_push or bool(plan.publication_steps)}\n"
        f"Execution environment: {plan.execution_environment}\n"
        + "Success criteria:\n"
        + "\n".join(f"- {item}" for item in plan.success_criteria)
        + "\n\nVerification checks:\n"
        + "\n".join(f"- {item}" for item in plan.verification_checks)
        + f"\n\nExecution structured evidence:\n{_operational_evidence_text(execution, fallback='No execution evidence.')}\n\nPublish structured evidence:\n{publish_text}\n"
    )


def build_verification_check_prompt(
    task: Task,
    context_packet: ContextPacket,
    plan: ExecutionPlan,
    execution: ExecutionResult,
    check_name: str,
    publish: PublishResult | None = None,
) -> str:
    publish_text = _operational_evidence_text(publish, fallback="No separate publish step evidence was captured.")
    return (
        "Verify exactly one verification check using evidence only.\n"
        "You do not have live access to the world.\n"
        "Judge only the named check below, preserving required setup and test levels. Treat commit/push/PR checks as publish-stage obligations unless publish evidence is present.\n"
        "Freshness/retrieval artifacts in the ContextPacket are the truth layer for current docs, versions, changelog, CLI flags, compatibility, and migration facts; do not verify against stale model memory.\n"
        "Do not invent missing facts. If evidence is missing, say so explicitly.\n"
        "Return strict JSON matching this shape:\n"
        f"{json.dumps(VERIFICATION_SCHEMA_HINT, ensure_ascii=False, indent=2)}\n\n"
        f"Check under review:\n{check_name}\n\n"
        f"Task:\n{task.description}\n\n"
        f"ContextPacket:\n{context_packet.text}\n\n"
        f"Plan summary: {plan.summary}\n"
        f"Required test levels: {plan.required_test_levels}\n"
        f"Required setup steps: {plan.required_setup_steps}\n"
        f"Publish required later: {plan.require_commit or plan.require_push or bool(plan.publication_steps)}\n"
        f"Execution environment: {plan.execution_environment}\n"
        + "Success criteria:\n"
        + "\n".join(f"- {item}" for item in plan.success_criteria)
        + "\n\nAll verification checks from plan:\n"
        + "\n".join(f"- {item}" for item in plan.verification_checks)
        + f"\n\nExecution structured evidence:\n{_operational_evidence_text(execution, fallback='No execution evidence.')}\n\nPublish structured evidence:\n{publish_text}\n"
    )

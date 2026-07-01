from __future__ import annotations

import json

import pytest

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.controller import WorkflowController
from artifact_workflow_runtime.control_plane.reconcile import WorkspaceReconciler
from artifact_workflow_runtime.freshness import FreshnessGate, RetrievalMode, RetrievalService, RetrievalSourceKind
from artifact_workflow_runtime.llm_backend import ScriptedLLMBackend
from artifact_workflow_runtime.models import (
    Capability,
    ExecutionFamily,
    ExtractedFact,
    ObservationResult,
    RoutingDecision,
    StructuredEvidence,
    Task,
    TaskClassification,
    WorkPacketKind,
)
from artifact_workflow_runtime.openhands_adapter import FakeOpenHandsAdapter
from artifact_workflow_runtime.policy import StaticApprovalProvider



def _classification(task_text: str = "x") -> TaskClassification:
    return TaskClassification(
        normalized_task=task_text,
        needs_world_facts=False,
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        task_intent="implement",
        capabilities=[Capability.REPO_READ, Capability.REPO_WRITE],
        observation_focus=[],
        reasoning="test",
        risk_level="low",
    )


def _route(**kwargs) -> RoutingDecision:
    data = {
        "needs_repository_observation": False,
        "needs_world_observation": False,
        "needs_fresh_external_research": False,
        "can_plan_immediately": True,
        "required_evidence_types": [],
        "research_targets": [],
        "observation_focus": [],
        "reasoning": "route says local facts are enough",
    }
    data.update(kwargs)
    return RoutingDecision(**data)


def test_freshness_gate_triggers_versions_for_current_ci_action_versions() -> None:
    task = Task(description="Update GitHub Actions workflow to use latest stable actions/checkout and current recommended syntax.")
    decision = FreshnessGate().decide(task, _classification(task.description), _route())

    assert decision.freshness_required is True
    assert decision.retrieval_mode == RetrievalMode.DOCS_PLUS_VERSIONS
    assert decision.version_resolution_required is True
    assert decision.docs_resolution_required is True
    assert "version_resolution" in decision.triggered_by
    assert any(pref.source_kind == RetrievalSourceKind.OFFICIAL_DOCUMENTATION for pref in decision.preferred_sources)
    assert any(pref.source_kind == RetrievalSourceKind.OFFICIAL_GITHUB_RELEASES for pref in decision.preferred_sources)


def test_freshness_gate_triggers_docs_mode_for_cli_flags() -> None:
    task = Task(description="Generate the correct kubectl command using current docs for this CLI flag behavior.")
    decision = FreshnessGate().decide(task, _classification(task.description), _route())

    assert decision.freshness_required is True
    assert decision.retrieval_mode == RetrievalMode.DOCS_PLUS_VERSIONS
    assert decision.docs_resolution_required is True


def test_freshness_gate_does_not_trigger_for_stable_local_task() -> None:
    task = Task(description="Rename a local helper function and update its unit test in the repository.")
    decision = FreshnessGate().decide(task, _classification(task.description), _route())

    assert decision.freshness_required is False
    assert decision.retrieval_mode == RetrievalMode.NONE
    assert decision.version_resolution_required is False
    assert decision.docs_resolution_required is False


def test_freshness_gate_prefers_after_observe_when_repository_observation_is_also_required() -> None:
    task = Task(description="Inspect grpc/csharp in the repository and align Grpc.Net.Client plus GitHub Actions to current versions.")
    route = _route(needs_repository_observation=True, can_plan_immediately=False)
    decision = FreshnessGate().decide(task, _classification(task.description), route)

    assert decision.freshness_required is True
    assert decision.stage_preference.value == "after_observe"


def test_source_preference_policy_ranks_official_sources_before_third_party(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    task = Task(description="Resolve current Helm docs and version.")
    decision = FreshnessGate().decide(task, _classification(task.description), _route())
    raw = store.add_text("raw_retrieval", "retrieval")
    result = ObservationResult(
        request_id="observe_1",
        ok=True,
        summary="retrieval ok",
        evidence_text="See https://helm.sh/docs/ and https://random-blog.example/helm-version",
        artifacts=[raw],
        structured_evidence=StructuredEvidence(
            extracted_facts=[
                ExtractedFact(subject="helm", fact="current docs describe the command syntax", source="https://helm.sh/docs/"),
                ExtractedFact(subject="helm", fact="blog mentions a version", source="https://random-blog.example/helm-version"),
            ]
        ),
    )

    snapshot, artifacts = RetrievalService().normalize_result(artifact_store=store, task=task, decision=decision, result=result)

    assert artifacts
    assert snapshot.sources[0].source_kind == RetrievalSourceKind.OFFICIAL_DOCUMENTATION
    assert snapshot.sources[0].official is True
    assert any(source.source_kind in {RetrievalSourceKind.GENERAL_WEB, RetrievalSourceKind.THIRD_PARTY_ARTICLE} for source in snapshot.sources)
    assert any(store.get(artifact.id).kind == "retrieval_summary" for artifact in artifacts)
    assert any(store.get(artifact.id).kind == "retrieval_sources" for artifact in artifacts)


@pytest.mark.asyncio
async def test_runtime_route_forces_retrieval_and_plan_execute_receive_grounding(tmp_path) -> None:
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    llm = ScriptedLLMBackend(
        {
            "classification": [
                {
                    "normalized_task": "Add GitHub Actions workflow using current actions versions",
                    "needs_world_facts": False,
                    "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
                    "task_intent": "implement",
                    "capabilities": [Capability.REPO_READ.value, Capability.REPO_WRITE.value],
                    "observation_focus": [],
                    "reasoning": "Repository mutation task.",
                    "risk_level": "low",
                }
            ],
            "route_analysis": [
                {
                    "needs_repository_observation": True,
                    "needs_world_observation": False,
                    "needs_fresh_external_research": False,
                    "can_plan_immediately": False,
                    "required_evidence_types": ["repo_structure"],
                    "research_targets": [],
                    "observation_focus": ["workflow files"],
                    "reasoning": "The route model did not request research; the control-plane gate must override this.",
                }
            ],
            "obligation_analysis": [
                {
                    "required_test_levels": ["lint"],
                    "required_setup_steps": [],
                    "required_environment_conditions": ["docker_container"],
                    "required_documentation_updates": [],
                    "required_examples_updates": [],
                    "required_ci_updates": ["GitHub Actions workflow"],
                    "required_codegen_or_build_updates": [],
                    "affected_surfaces": ["ci_build"],
                    "adjacent_components": [],
                    "discovered_impacts": [],
                    "required_publish_actions": [],
                    "completion_requirements": ["workflow uses retrieved current action versions"],
                    "blocker_conditions": [],
                    "reasoning_summary": "CI workflow must use retrieved current docs/versions.",
                }
            ],
            "planning": [
                {
                    "summary": "Add a workflow pinned to the retrieved actions/checkout version",
                    "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
                    "task_intent": "implement",
                    "deliverable_kind": "repository_changes",
                    "capabilities": [Capability.REPO_WRITE.value],
                    "steps": ["create workflow", "pin actions/checkout to retrieved version", "run syntax check"],
                    "publication_steps": [],
                    "success_criteria": ["workflow uses actions/checkout@v5"],
                    "verification_checks": ["workflow syntax check passed"],
                    "requires_mutation": True,
                    "must_change_world": True,
                    "expected_repo_changes": [".github/workflows/ci.yml"],
                    "required_test_levels": ["lint"],
                    "required_setup_steps": [],
                    "require_commit": False,
                    "require_push": False,
                    "execution_environment": "docker_container",
                    "environment_notes": [],
                    "reasoning": "Use retrieval grounding rather than memory.",
                }
            ],
            "verification": [
                {
                    "passed": True,
                    "summary": "QA evidence uses retrieved current action version.",
                    "checks_passed": ["workflow syntax check passed"],
                    "checks_failed": [],
                    "missing_evidence": [],
                    "confidence": "high",
                    "reasoning": "Retrieval summary and execution evidence are present.",
                    "performed_test_levels": ["lint"],
                    "missing_test_levels": [],
                    "setup_steps_performed": [],
                    "missing_setup_steps": [],
                    "commit_required": False,
                    "push_required": False,
                    "commit_done": False,
                    "push_done": False,
                    "pr_detected": False,
                    "pr_checks_waited": False,
                    "pr_checks_passed": [],
                    "pr_checks_failed": [],
                    "pr_checks_pending": [],
                    "missing_obligations": [],
                    "completion_status": "completed",
                }
            ],
        }
    )
    retrieval_output = json.dumps(
        {
            "summary": "Resolved current GitHub Actions docs and versions.",
            "structured_evidence": {
                "extracted_facts": [
                    {
                        "subject": "actions/checkout",
                        "fact": "current stable release is v5 and should be pinned as actions/checkout@v5",
                        "source": "https://github.com/actions/checkout/releases",
                        "confidence": "high",
                    },
                    {
                        "subject": "GitHub Actions workflow syntax",
                        "fact": "official docs describe uses: owner/action@version syntax",
                        "source": "https://docs.github.com/actions",
                        "confidence": "high",
                    },
                ],
                "commands_run": [],
                "blockers": [],
            },
        }
    )
    openhands = FakeOpenHandsAdapter(
        artifact_store,
        scripts={
            "observe": ["Observed repository root. found .github/workflows and README.md. command: git status --short", retrieval_output],
            "execute": [
                "changed .github/workflows/ci.yml to use actions/checkout@v5\ncommand: bash -n .github/workflows/ci.yml passed",
                "confirmed .github/workflows/ci.yml still uses actions/checkout@v5\ncommand: bash -n .github/workflows/ci.yml passed",
                "confirmed .github/workflows/ci.yml still uses actions/checkout@v5\ncommand: bash -n .github/workflows/ci.yml passed",
                "confirmed .github/workflows/ci.yml still uses actions/checkout@v5\ncommand: bash -n .github/workflows/ci.yml passed",
                "confirmed .github/workflows/ci.yml still uses actions/checkout@v5\ncommand: bash -n .github/workflows/ci.yml passed",
                "confirmed .github/workflows/ci.yml still uses actions/checkout@v5\ncommand: bash -n .github/workflows/ci.yml passed",
            ],
            "verify": ["unused"],
        },
    )
    controller = WorkflowController(
        llm_backend=llm,
        openhands_adapter=openhands,
        artifact_root=tmp_path / "artifacts",
        approval_provider=StaticApprovalProvider(approve=True, reviewer="test"),
    )

    report = await controller.run(Task(description="Add GitHub Actions CI using latest stable actions/checkout and current official docs."))

    assert report.status in {"completed", "blocked", "needs_environment", "partially_completed", "failed"}
    assert len(openhands.calls["observe"]) == 2
    observation_request = openhands.calls["observe"][0]
    research_request = openhands.calls["observe"][1]
    assert observation_request.work_packet_kind == WorkPacketKind.OBSERVE
    assert observation_request.metadata["mode"] == "observe_only"
    assert research_request.work_packet_kind == WorkPacketKind.RESEARCH
    assert research_request.metadata["mode"] == "freshness_retrieval"
    assert research_request.metadata["retrieval_mode"] == RetrievalMode.DOCS_PLUS_VERSIONS.value
    route = report.route
    assert route is not None and route.needs_fresh_external_research is True
    planning_prompt = llm.calls["planning"][0].prompt
    assert "Freshness retrieval summary" in planning_prompt
    assert "actions/checkout@v5" in planning_prompt
    assert "truth layer" in planning_prompt
    execute_prompt = openhands.calls["execute"][0].prompt
    assert "Freshness/retrieval artifacts in the ContextPacket are the truth layer" in execute_prompt
    assert "actions/checkout@v5" in execute_prompt
    artifact_kinds = {artifact.kind for artifact in artifact_store.list()}
    assert "freshness_decision" in artifact_kinds
    assert "retrieval_summary" in artifact_kinds
    assert "retrieval_sources" in artifact_kinds
    assert "version_resolution" in artifact_kinds
    assert "retrieval_snapshot" in artifact_kinds


def test_workspace_reconciler_adopts_existing_candidate_and_marks_continuation() -> None:
    task = Task(description="Work in /workspace/freeplane_plugin_grpc, improve grpc/csharp until smoke tests pass, and align Grpc.Net.Client with current official docs.")
    classification = _classification(task.description)
    route = _route(needs_repository_observation=True, can_plan_immediately=False, observation_focus=["grpc/csharp", "integration", "smoke"], required_evidence_types=["repo_patterns", "build_instructions"])
    observation = ObservationResult(
        request_id="observe_1",
        ok=True,
        summary="grpc/csharp already exists and dotnet test passed",
        evidence_text="grpc/csharp already exists. dotnet test grpc/csharp/tests/FreeplaneGrpcClient.Tests.csproj passed with 39 passed.",
        structured_evidence=StructuredEvidence(
            files_observed=[],
            commands_run=[],
            extracted_facts=[],
        ),
    )
    decision = FreshnessGate().decide(task, classification, route)

    reconciliation = WorkspaceReconciler().reconcile(
        task=task,
        classification=classification,
        route=route,
        observation=observation,
        freshness_decision=decision,
    )

    assert reconciliation.adopt_existing_work is True
    assert reconciliation.delivery_mode == "continue_existing_candidate"
    assert "grpc/csharp" in reconciliation.existing_target_surfaces
    assert reconciliation.freshness_scope in {"targeted_post_observe", "post_observe"}


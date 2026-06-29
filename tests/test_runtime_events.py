from __future__ import annotations

import pytest

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.controller import WorkflowController
from artifact_workflow_runtime.llm_backend.fake import ScriptedLLMBackend
from artifact_workflow_runtime.models import Task
from artifact_workflow_runtime.openhands_adapter.fake import FakeOpenHandsAdapter
from artifact_workflow_runtime.policy import StaticApprovalProvider


@pytest.mark.asyncio
async def test_controller_emits_stage_events(tmp_path):
    events = []
    llm = ScriptedLLMBackend(
        {
            "classification": [
                {
                    "normalized_task": "fix repo task",
                    "needs_world_facts": True,
                    "execution_family": "repository_change",
                    "task_intent": "implement",
                    "capabilities": ["repo_read", "repo_write"],
                    "observation_focus": ["repo layout"],
                    "reasoning": "Repository changes need facts from the repo.",
                    "risk_level": "medium",
                }
            ],
            "route_analysis": [
                {
                    "needs_repository_observation": True,
                    "needs_world_observation": False,
                    "needs_fresh_external_research": True,
                    "can_plan_immediately": False,
                    "required_evidence_types": ["official_docs", "repo_patterns"],
                    "research_targets": ["official grpc docs"],
                    "observation_focus": ["repo layout"],
                    "reasoning": "Need fresh docs and repo facts before planning.",
                }
            ],
            "planning": [
                {
                    "summary": "Implement the requested repository change.",
                    "execution_family": "repository_change",
                    "task_intent": "implement",
                    "deliverable_kind": "repository_changes",
                    "capabilities": ["repo_read", "repo_write"],
                    "steps": ["Inspect repo", "Modify files"],
                    "success_criteria": ["Files changed"],
                    "verification_checks": ["changed files listed"],
                    "requires_mutation": True,
                    "must_change_world": True,
                    "expected_repo_changes": ["source changes"],
                    "reasoning": "Need to change files in repo.",
                }
            ],
            "verification": [
                {
                    "passed": True,
                    "summary": "Verification passed.",
                    "checks_passed": ["changed files listed"],
                    "checks_failed": [],
                    "missing_evidence": [],
                    "confidence": "high",
                    "reasoning": "Execution evidence contains concrete repo changes.",
                }
            ],
        }
    )
    store = ArtifactStore(tmp_path / "artifacts")
    openhands = FakeOpenHandsAdapter(
        store,
        scripts={
            "observe": [
                "Official grpc docs captured.",
                "Repository tree and existing clients inspected.",
            ],
            "execute": ["Changed files: cpp/client.cpp\nCommands run: cmake --build ."],
        },
    )

    controller = WorkflowController(
        llm_backend=llm,
        openhands_adapter=openhands,
        artifact_root=tmp_path / "artifacts",
        approval_provider=StaticApprovalProvider(approve=True, reviewer="test"),
        event_sink=events.append,
    )

    report = await controller.run(Task(description="Add C++ gRPC client to the repository using current official docs"))

    assert report.status
    stage_kinds = {(event.stage, event.kind) for event in events}
    assert ("classify", "stage_started") in stage_kinds
    assert ("classify", "stage_completed") in stage_kinds
    assert ("route", "stage_completed") in stage_kinds
    assert ("research", "stage_completed") in stage_kinds
    assert ("observe", "stage_completed") in stage_kinds
    assert ("execute", "stage_completed") in stage_kinds
    assert ("finalize", "stage_completed") in stage_kinds

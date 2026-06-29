from __future__ import annotations

import pytest

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.controller import WorkflowController
from artifact_workflow_runtime.llm_backend import ScriptedLLMBackend
from artifact_workflow_runtime.models import Capability, ExecutionFamily, Task
from artifact_workflow_runtime.openhands_adapter import FakeOpenHandsAdapter
from artifact_workflow_runtime.policy import StaticApprovalProvider

pytestmark = pytest.mark.asyncio


async def test_workflow_mvp_runs_end_to_end(tmp_path) -> None:
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    llm = ScriptedLLMBackend(
        {
            "classification": [
                {
                    "normalized_task": "Inspect repo and fix failing tests",
                    "needs_world_facts": True,
                    "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
                    "task_intent": "modify",
                    "capabilities": [Capability.REPO_READ.value, Capability.REPO_WRITE.value, Capability.GIT_WRITE.value],
                    "observation_focus": ["find failing test commands", "identify changed files"],
                    "reasoning": "Need repository facts before planning.",
                    "risk_level": "medium",
                }
            ],
            "route_analysis": [
                {
                    "needs_repository_observation": True,
                    "needs_world_observation": False,
                    "needs_fresh_external_research": False,
                    "can_plan_immediately": False,
                    "required_evidence_types": ["repo_structure", "repo_patterns"],
                    "research_targets": [],
                    "observation_focus": ["find failing test commands", "identify changed files"],
                    "reasoning": "Need repository evidence before planning.",
                }
            ],
            "planning": [
                {
                    "summary": "Edit failing code path and validate",
                    "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
                    "task_intent": "modify",
                    "deliverable_kind": "repository_changes",
                    "capabilities": [Capability.REPO_WRITE.value, Capability.GIT_WRITE.value],
                    "steps": ["inspect failing path", "edit code", "run tests"],
                    "success_criteria": ["target tests pass"],
                    "verification_checks": ["run pytest target"],
                    "requires_mutation": True,
                    "must_change_world": True,
                    "expected_repo_changes": ["src/app.py updated"],
                    "reasoning": "Observation evidence indicates a concrete code fix is needed.",
                }
            ],
            "verification": [
                {
                    "passed": True,
                    "summary": "Evidence shows the target pytest command passed after the code change.",
                    "checks_passed": ["run pytest target"],
                    "checks_failed": [],
                    "missing_evidence": [],
                    "confidence": "high",
                    "reasoning": "Execution evidence includes a concrete file change and successful pytest output.",
                }
            ],
        }
    )
    openhands = FakeOpenHandsAdapter(
        artifact_store,
        scripts={
            "observe": ["Observed failing test: pytest tests/test_feature.py -k scenario. Relevant file: src/app.py"],
            "execute": ["Applied fix in src/app.py and ran pytest tests/test_feature.py -k scenario successfully."],
            "verify": ["unused in evidence-backed verification"],
        },
    )
    controller = WorkflowController(
        llm_backend=llm,
        openhands_adapter=openhands,
        artifact_root=tmp_path / "artifacts",
        approval_provider=StaticApprovalProvider(approve=True, reviewer="test"),
    )
    report = await controller.run(Task(description="Inspect repo metacoma/freeplane_plugin_grpc and fix failing tests"))
    assert report.status == "completed"
    assert report.classification is not None
    assert report.route is not None and report.route.needs_repository_observation is True
    assert report.plan is not None
    assert report.policy is not None and report.policy.requires_approval is True
    assert report.approval is not None and report.approval.approved is True
    assert report.execution is not None
    assert report.verification is not None and report.verification.passed is True
    assert report.verification.checks_passed == ["run pytest target"]
    assert len(report.artifact_ids) >= 9
    assert len(openhands.calls["observe"]) == 1
    assert len(openhands.calls["execute"]) == 1
    assert len(openhands.calls["verify"]) == 0


async def test_route_analysis_can_require_repo_observation_even_if_classifier_says_no_world_facts(tmp_path) -> None:
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    llm = ScriptedLLMBackend(
        {
            "classification": [
                {
                    "normalized_task": "Add C++ gRPC client",
                    "needs_world_facts": False,
                    "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
                    "task_intent": "implement",
                    "capabilities": [Capability.REPO_READ.value, Capability.REPO_WRITE.value],
                    "observation_focus": [],
                    "reasoning": "Classification alone is not enough.",
                    "risk_level": "medium",
                }
            ],
            "route_analysis": [
                {
                    "needs_repository_observation": True,
                    "needs_world_observation": False,
                    "needs_fresh_external_research": False,
                    "can_plan_immediately": False,
                    "required_evidence_types": ["repo_patterns", "build_instructions"],
                    "research_targets": [],
                    "observation_focus": ["inspect existing clients", "inspect build files"],
                    "reasoning": "Need repository observation before planning.",
                }
            ],
            "planning": [
                {
                    "summary": "Implement the new client after inspecting existing clients",
                    "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
                    "task_intent": "implement",
                    "deliverable_kind": "repository_changes",
                    "capabilities": [Capability.REPO_WRITE.value],
                    "steps": ["inspect repo", "implement client", "build"],
                    "success_criteria": ["new client exists", "build works"],
                    "verification_checks": ["confirm files exist", "confirm build output"],
                    "requires_mutation": True,
                    "must_change_world": True,
                    "expected_repo_changes": ["cpp client files", "build config updates"],
                    "reasoning": "Repo evidence is required and was collected before planning.",
                }
            ],
            "verification": [
                {
                    "passed": True,
                    "summary": "Evidence is sufficient.",
                    "checks_passed": ["confirm files exist", "confirm build output"],
                    "checks_failed": [],
                    "missing_evidence": [],
                    "confidence": "medium",
                    "reasoning": "Observation and execution evidence are both present.",
                }
            ],
        }
    )
    openhands = FakeOpenHandsAdapter(
        artifact_store,
        scripts={
            "observe": ["Found existing ruby/rust/python/nodejs clients and proto definitions in repo."],
            "execute": ["Added cpp client and build changes; cmake build succeeded."],
        },
    )
    controller = WorkflowController(
        llm_backend=llm,
        openhands_adapter=openhands,
        artifact_root=tmp_path / "artifacts",
        approval_provider=StaticApprovalProvider(approve=True, reviewer="test"),
    )
    report = await controller.run(Task(description="Work with repository metacoma/freeplane_plugin_grpc and add a C++ gRPC client"))
    assert report.status == "completed"
    assert report.route is not None and report.route.needs_repository_observation is True
    assert len(openhands.calls["observe"]) == 1


async def test_route_analysis_can_require_fresh_external_research_before_planning(tmp_path) -> None:
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    llm = ScriptedLLMBackend(
        {
            "classification": [
                {
                    "normalized_task": "Add C++ gRPC client using current docs",
                    "needs_world_facts": False,
                    "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
                    "task_intent": "implement",
                    "capabilities": [Capability.REPO_READ.value, Capability.REPO_WRITE.value],
                    "observation_focus": ["inspect existing clients"],
                    "reasoning": "Need repo changes.",
                    "risk_level": "medium",
                }
            ],
            "route_analysis": [
                {
                    "needs_repository_observation": True,
                    "needs_world_observation": False,
                    "needs_fresh_external_research": True,
                    "can_plan_immediately": False,
                    "required_evidence_types": ["official_docs", "package_versions", "repo_patterns"],
                    "research_targets": ["gRPC C++ official docs", "protobuf C++ generation docs"],
                    "observation_focus": ["inspect existing clients", "inspect build files"],
                    "reasoning": "Need both fresh docs and repo evidence before planning.",
                }
            ],
            "planning": [
                {
                    "summary": "Implement C++ client using current gRPC and protobuf guidance",
                    "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
                    "task_intent": "implement",
                    "deliverable_kind": "repository_changes",
                    "capabilities": [Capability.REPO_WRITE.value],
                    "steps": ["inspect repo", "use current docs", "implement client", "build"],
                    "success_criteria": ["cpp client exists", "build works"],
                    "verification_checks": ["confirm files exist", "confirm build output"],
                    "requires_mutation": True,
                    "must_change_world": True,
                    "expected_repo_changes": ["cpp client files", "build config updates"],
                    "reasoning": "Research and repo evidence were both collected.",
                }
            ],
            "verification": [
                {
                    "passed": True,
                    "summary": "Execution evidence matches the researched plan.",
                    "checks_passed": ["confirm files exist", "confirm build output"],
                    "checks_failed": [],
                    "missing_evidence": [],
                    "confidence": "medium",
                    "reasoning": "Research and repo evidence were available before execution.",
                }
            ],
        }
    )
    openhands = FakeOpenHandsAdapter(
        artifact_store,
        scripts={
            "observe": [
                "Research evidence: official gRPC C++ docs and protobuf generation docs located with current version references.",
                "Repository evidence: found existing ruby/rust/python/nodejs clients and build files.",
            ],
            "execute": ["Implemented cpp client and build integration; build passed."],
        },
    )
    controller = WorkflowController(
        llm_backend=llm,
        openhands_adapter=openhands,
        artifact_root=tmp_path / "artifacts",
        approval_provider=StaticApprovalProvider(approve=True, reviewer="test"),
    )
    report = await controller.run(Task(description="Work with repository metacoma/freeplane_plugin_grpc and add a C++ gRPC client using the current official gRPC/protobuf docs"))
    assert report.status == "completed"
    assert report.route is not None and report.route.needs_fresh_external_research is True
    assert report.research is not None and report.research.ok is True
    assert len(openhands.calls["observe"]) == 2


async def test_html_execution_evidence_fails_verification_without_second_openhands_run(tmp_path) -> None:
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    llm = ScriptedLLMBackend(
        {
            "classification": [
                {
                    "normalized_task": "Modify repo",
                    "needs_world_facts": True,
                    "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
                    "task_intent": "modify",
                    "capabilities": [Capability.REPO_READ.value, Capability.REPO_WRITE.value],
                    "observation_focus": ["inspect repo"],
                    "reasoning": "Need repo facts.",
                    "risk_level": "medium",
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
                    "observation_focus": ["inspect repo"],
                    "reasoning": "Need repository evidence before mutation.",
                }
            ],
            "planning": [
                {
                    "summary": "Make repo change",
                    "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
                    "task_intent": "modify",
                    "deliverable_kind": "repository_changes",
                    "capabilities": [Capability.REPO_WRITE.value],
                    "steps": ["edit file"],
                    "success_criteria": ["expected file changed"],
                    "verification_checks": ["confirm changed file", "confirm test output"],
                    "requires_mutation": True,
                    "must_change_world": True,
                    "expected_repo_changes": ["a file should change"],
                    "reasoning": "Need mutation.",
                }
            ],
        }
    )
    openhands = FakeOpenHandsAdapter(
        artifact_store,
        scripts={
            "observe": ["Repo observed normally."],
            "execute": ["<!DOCTYPE html><html><body>OpenHands SPA</body></html>"],
        },
    )
    controller = WorkflowController(
        llm_backend=llm,
        openhands_adapter=openhands,
        artifact_root=tmp_path / "artifacts",
        approval_provider=StaticApprovalProvider(approve=True, reviewer="test"),
    )
    report = await controller.run(Task(description="Work with repository metacoma/freeplane_plugin_grpc and modify a file"))
    assert report.status == "execution_failed"
    assert report.execution is not None and report.execution.transport_error is True
    assert report.verification is not None and report.verification.passed is False
    assert "usable execution evidence" in report.verification.missing_evidence
    assert len(openhands.calls["verify"]) == 0


async def test_planner_cannot_degrade_implementation_task_into_documentation_only(tmp_path) -> None:
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    llm = ScriptedLLMBackend(
        {
            "classification": [
                {
                    "normalized_task": "Add C++ gRPC client",
                    "needs_world_facts": True,
                    "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
                    "task_intent": "implement",
                    "capabilities": [Capability.REPO_READ.value, Capability.REPO_WRITE.value],
                    "observation_focus": ["inspect existing clients", "inspect build files"],
                    "reasoning": "User asked for a real implementation.",
                    "risk_level": "medium",
                }
            ],
            "route_analysis": [
                {
                    "needs_repository_observation": True,
                    "needs_world_observation": False,
                    "needs_fresh_external_research": True,
                    "can_plan_immediately": False,
                    "required_evidence_types": ["repo_patterns", "official_docs", "api_examples"],
                    "research_targets": ["gRPC C++ official docs", "protobuf C++ code generation docs"],
                    "observation_focus": ["inspect existing clients", "inspect build files"],
                    "reasoning": "Need both fresh external docs and repository evidence before planning implementation.",
                }
            ],
            "planning": [
                {
                    "summary": "Analyze existing clients and document the steps to add C++ support",
                    "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
                    "task_intent": "document",
                    "deliverable_kind": "documentation",
                    "capabilities": [Capability.REPO_WRITE.value],
                    "steps": ["analyze repo", "outline design", "provide instructions"],
                    "success_criteria": ["design is documented"],
                    "verification_checks": ["confirm instructions exist"],
                    "requires_mutation": False,
                    "must_change_world": False,
                    "expected_repo_changes": [],
                    "reasoning": "Planner degraded the task into documentation.",
                }
            ],
        }
    )
    openhands = FakeOpenHandsAdapter(
        artifact_store,
        scripts={
            "observe": [
                "Research evidence: official gRPC and protobuf docs gathered.",
                "Repository evidence: Found ruby/rust/python/nodejs clients and proto/build files.",
            ],
            "execute": ["should not run"],
        },
    )
    controller = WorkflowController(
        llm_backend=llm,
        openhands_adapter=openhands,
        artifact_root=tmp_path / "artifacts",
        approval_provider=StaticApprovalProvider(approve=True, reviewer="test"),
    )
    report = await controller.run(Task(description="Work with repository metacoma/freeplane_plugin_grpc and add a C++ gRPC client using current docs"))
    assert report.status == "blocked"
    assert report.policy is not None and report.policy.blocked is True
    assert any("degraded" in reason.lower() or "documentation" in reason.lower() for reason in report.policy.reasons)
    assert len(openhands.calls["execute"]) == 0


async def test_repository_change_marks_incomplete_when_integration_and_push_obligations_missing(tmp_path) -> None:
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    llm = ScriptedLLMBackend(
        {
            "classification": [
                {
                    "normalized_task": "Add C++ gRPC client",
                    "needs_world_facts": True,
                    "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
                    "task_intent": "implement",
                    "capabilities": [Capability.REPO_READ.value, Capability.REPO_WRITE.value, Capability.GIT_WRITE.value],
                    "observation_focus": ["inspect existing clients", "inspect test topology"],
                    "reasoning": "Need repo evidence before planning.",
                    "risk_level": "medium",
                }
            ],
            "route_analysis": [
                {
                    "needs_repository_observation": True,
                    "needs_world_observation": False,
                    "needs_fresh_external_research": False,
                    "can_plan_immediately": False,
                    "required_evidence_types": ["repo_patterns", "build_instructions"],
                    "research_targets": [],
                    "observation_focus": ["inspect existing clients", "inspect integration tests"],
                    "reasoning": "Need repository observation before planning.",
                }
            ],
            "planning": [
                {
                    "summary": "Implement the C++ client and make the repo deliverable-ready",
                    "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
                    "task_intent": "implement",
                    "deliverable_kind": "repository_changes",
                    "capabilities": [Capability.REPO_WRITE.value, Capability.GIT_WRITE.value],
                    "steps": ["inspect repo", "implement client", "install deps", "run build", "run integration tests"],
                    "success_criteria": ["cpp client exists", "build works", "integration tests cover the new client", "changes are pushed"],
                    "verification_checks": ["confirm files exist", "confirm build output", "confirm integration test evidence", "confirm push evidence"],
                    "requires_mutation": True,
                    "must_change_world": True,
                    "expected_repo_changes": ["cpp client files", "build config updates", "integration tests"],
                    "required_test_levels": ["build", "unit", "integration"],
                    "required_setup_steps": ["install grpc/protobuf/cmake test dependencies in docker container"],
                    "require_commit": True,
                    "require_push": True,
                    "execution_environment": "docker_container",
                    "environment_notes": ["execution runs inside Docker"],
                    "reasoning": "New client surface requires integration coverage and publish completion.",
                }
            ],
            "verification": [
                {
                    "passed": False,
                    "summary": "The client was implemented, but integration tests were not shown and no push evidence was captured.",
                    "checks_passed": ["confirm files exist", "confirm build output"],
                    "checks_failed": ["confirm integration test evidence", "confirm push evidence"],
                    "missing_evidence": ["integration test run", "git push result"],
                    "confidence": "high",
                    "reasoning": "Unit/build evidence exists, but required integration and publish obligations are missing.",
                    "performed_test_levels": ["build", "unit"],
                    "missing_test_levels": ["integration"],
                    "setup_steps_performed": [],
                    "missing_setup_steps": ["install grpc/protobuf/cmake test dependencies in docker container"],
                    "commit_required": True,
                    "push_required": True,
                    "commit_done": False,
                    "push_done": False,
                    "missing_obligations": ["install docker test dependencies", "run integration tests", "commit changes", "push changes"],
                    "completion_status": "partially_completed",
                }
            ],
        }
    )
    openhands = FakeOpenHandsAdapter(
        artifact_store,
        scripts={
            "observe": ["Observed existing clients and integration harness files in the repository."],
            "execute": [
                "Implemented the cpp client, updated build files, and ran unit tests only.",
                "Checked git status in Docker container; changes remain uncommitted and nothing was pushed.",
            ],
            "verify": ["unused in evidence-backed verification"],
        },
    )
    controller = WorkflowController(
        llm_backend=llm,
        openhands_adapter=openhands,
        artifact_root=tmp_path / "artifacts",
        approval_provider=StaticApprovalProvider(approve=True, reviewer="test"),
    )
    report = await controller.run(Task(description="Add a C++ gRPC client in the repository and leave the change pushed and fully tested"))
    assert report.status == "partially_completed"
    assert report.publish is not None
    assert report.verification is not None
    assert report.verification.missing_test_levels == ["integration"]
    assert report.verification.push_required is True
    assert report.verification.push_done is False
    assert len(openhands.calls["execute"]) == 2


async def test_publish_step_runs_for_pr_capability_and_prompt_requires_waiting_for_pr_checks(tmp_path) -> None:
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    llm = ScriptedLLMBackend(
        {
            "classification": [
                {
                    "normalized_task": "Implement change and open a PR",
                    "needs_world_facts": True,
                    "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
                    "task_intent": "implement",
                    "capabilities": [Capability.REPO_READ.value, Capability.REPO_WRITE.value, Capability.REPO_CREATE_PR.value],
                    "observation_focus": ["inspect repo"],
                    "reasoning": "Need repo changes and PR creation.",
                    "risk_level": "medium",
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
                    "observation_focus": ["inspect repo"],
                    "reasoning": "Need repository evidence before planning.",
                }
            ],
            "planning": [
                {
                    "summary": "Implement the change and open/update a PR",
                    "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
                    "task_intent": "implement",
                    "deliverable_kind": "repository_changes",
                    "capabilities": [Capability.REPO_WRITE.value, Capability.REPO_CREATE_PR.value],
                    "steps": ["inspect repo", "implement", "open pr"],
                    "success_criteria": ["feature implemented", "PR checks green"],
                    "verification_checks": ["feature implemented", "PR checks green"],
                    "requires_mutation": True,
                    "must_change_world": True,
                    "expected_repo_changes": ["code changes"],
                    "require_commit": False,
                    "require_push": False,
                    "reasoning": "A PR should still trigger publish completion handling.",
                }
            ],
            "verification": [
                {
                    "passed": True,
                    "summary": "PR checks were awaited and passed.",
                    "checks_passed": ["feature implemented", "PR checks green"],
                    "checks_failed": [],
                    "missing_evidence": [],
                    "confidence": "high",
                    "reasoning": "Publish evidence showed PR checks completed successfully.",
                    "pr_detected": True,
                    "pr_checks_waited": True,
                    "pr_checks_passed": ["build", "integration"],
                    "pr_checks_failed": [],
                    "pr_checks_pending": [],
                    "completion_status": "completed",
                }
            ],
        }
    )
    openhands = FakeOpenHandsAdapter(
        artifact_store,
        scripts={
            "observe": ["Repository observed."],
            "execute": [
                "Implemented the change.",
                "Created PR #7, waited for checks, fixed a failure if needed, and all PR checks passed.",
            ],
        },
    )
    controller = WorkflowController(
        llm_backend=llm,
        openhands_adapter=openhands,
        artifact_root=tmp_path / "artifacts",
        approval_provider=StaticApprovalProvider(approve=True, reviewer="test"),
    )
    report = await controller.run(Task(description="Implement the change and open a PR"))
    assert report.status == "completed"
    assert report.publish is not None
    assert len(openhands.calls["execute"]) == 2
    publish_request = openhands.calls["execute"][1]
    assert "wait for all PR checks" in publish_request.prompt
    assert "if checks fail" in publish_request.prompt

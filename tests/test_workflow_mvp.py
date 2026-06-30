from __future__ import annotations

import pytest

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.controller import WorkflowController
from artifact_workflow_runtime.llm_backend import ScriptedLLMBackend
from artifact_workflow_runtime.model_routing import ModelRoutingConfig
from artifact_workflow_runtime.models import AcceptanceStatus, BlockerKind, Capability, ExecutionFamily, ExecutionStatus, Task
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
            "obligation_analysis": [
                {
                    "required_test_levels": ["unit"],
                    "required_setup_steps": [],
                    "required_environment_conditions": ["docker_container"],
                    "required_publish_actions": [],
                    "completion_requirements": ["run pytest target"],
                    "blocker_conditions": [],
                    "reasoning_summary": "Observation evidence implies unit validation only for this narrow fix."
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


async def test_missing_environment_dependency_blocks_acceptance_for_required_integration_verification(tmp_path) -> None:
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
                    "observation_focus": ["inspect existing clients", "inspect integration harness"],
                    "reasoning": "Repository facts are needed before implementation.",
                    "risk_level": "medium",
                }
            ],
            "route_analysis": [
                {
                    "needs_repository_observation": True,
                    "needs_world_observation": False,
                    "needs_fresh_external_research": False,
                    "can_plan_immediately": False,
                    "required_evidence_types": ["repo_patterns", "integration_harness"],
                    "research_targets": [],
                    "observation_focus": ["inspect existing clients", "inspect Freeplane integration test path"],
                    "reasoning": "Need repository and integration evidence before planning.",
                }
            ],
            "obligation_analysis": [
                {
                    "required_test_levels": ["build", "integration"],
                    "required_setup_steps": ["Freeplane must be available for integration tests"],
                    "required_environment_conditions": ["docker_container", "Freeplane runtime available"],
                    "required_publish_actions": [],
                    "completion_requirements": ["integration tests with Freeplane must run and pass"],
                    "blocker_conditions": ["missing Freeplane runtime blocks acceptance"],
                    "reasoning_summary": "A new C++ gRPC client touches the integration path; Freeplane-backed integration verification is mandatory.",
                }
            ],
            "planning": [
                {
                    "summary": "Implement C++ gRPC client and validate through Freeplane integration tests",
                    "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
                    "task_intent": "implement",
                    "deliverable_kind": "repository_changes",
                    "capabilities": [Capability.REPO_WRITE.value],
                    "steps": ["implement client", "run build", "run Freeplane integration tests"],
                    "success_criteria": ["client code exists", "build passes", "Freeplane integration tests pass"],
                    "verification_checks": ["build succeeds", "Freeplane integration tests pass"],
                    "requires_mutation": True,
                    "must_change_world": True,
                    "expected_repo_changes": ["src/cpp/client.cc", "CMakeLists.txt"],
                    "required_test_levels": ["build", "integration"],
                    "required_setup_steps": ["Freeplane must be available for integration tests"],
                    "execution_environment": "docker_container",
                    "environment_notes": ["Freeplane runtime is required for integration acceptance"],
                    "reasoning": "Integration verification is mandatory for this repo path.",
                }
            ],
            "verification": [
                {
                    "passed": True,
                    "summary": "The implementation and build evidence look useful, but integration could not actually run.",
                    "checks_passed": ["build succeeds"],
                    "checks_failed": [],
                    "missing_evidence": [],
                    "confidence": "medium",
                    "reasoning": "This intentionally simulates a too-soft evidence review; acceptance must still be hard-blocked by structured environment evidence.",
                    "performed_test_levels": ["build"],
                    "missing_test_levels": [],
                    "completion_status": "completed",
                }
            ],
        }
    )
    openhands = FakeOpenHandsAdapter(
        artifact_store,
        scripts={
            "observe": ["Observed existing clients and a Freeplane-backed integration harness."],
            "execute": [
                "Added src/cpp/client.cc and updated CMakeLists.txt. cmake build succeeded. "
                "Blocker: integration tests not run because Freeplane runtime is not installed / not found in the Docker environment."
            ],
        },
    )
    controller = WorkflowController(
        llm_backend=llm,
        openhands_adapter=openhands,
        artifact_root=tmp_path / "artifacts",
        approval_provider=StaticApprovalProvider(approve=True, reviewer="test"),
    )
    report = await controller.run(Task(description="Add a C++ gRPC client and validate it with Freeplane integration tests"))

    assert report.status == "needs_environment"
    assert report.execution is not None
    assert report.execution.execution_status == ExecutionStatus.PARTIAL
    assert report.verification is not None
    assert report.verification.acceptance_status == AcceptanceStatus.NEEDS_ENVIRONMENT
    assert report.acceptance_decision is not None
    assert report.acceptance_decision.accepted is False
    assert report.acceptance_decision.status == AcceptanceStatus.NEEDS_ENVIRONMENT
    assert any(blocker.kind == BlockerKind.INTEGRATION_ENVIRONMENT_UNAVAILABLE for blocker in report.acceptance_decision.blockers)
    assert any(result.status.value == "blocked" for result in report.acceptance_decision.obligation_results)


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
            "obligation_analysis": [
                {
                    "required_test_levels": ["build", "unit", "integration"],
                    "required_setup_steps": ["install dependencies inside docker"],
                    "required_environment_conditions": ["docker_container"],
                    "required_publish_actions": [],
                    "completion_requirements": ["integration validation for new client"],
                    "blocker_conditions": [],
                    "reasoning_summary": "Evidence shows a new client should respect existing validation topology."
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
            "execute": ["Added cpp client files src/cpp/client.cc and build changes CMakeLists.txt; cmake build succeeded; pytest unit tests passed; integration tests passed."],
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
            "obligation_analysis": [
                {
                    "required_test_levels": ["build", "unit", "integration"],
                    "required_setup_steps": ["install Freeplane inside docker if integration harness requires it"],
                    "required_environment_conditions": ["docker_container"],
                    "required_publish_actions": [],
                    "completion_requirements": ["integration validation using current repo harness"],
                    "blocker_conditions": [],
                    "reasoning_summary": "Research plus repo evidence imply integration validation before completion."
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
            "execute": ["Implemented cpp client files src/cpp/client.cc and build integration CMakeLists.txt; build passed; pytest unit tests passed; integration tests passed."],
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


async def test_verification_checks_use_per_check_model_routing(tmp_path) -> None:
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    llm = ScriptedLLMBackend(
        {
            "classification": [
                {
                    "normalized_task": "Fix repo and verify with multiple checks",
                    "needs_world_facts": True,
                    "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
                    "task_intent": "modify",
                    "capabilities": [Capability.REPO_READ.value, Capability.REPO_WRITE.value, Capability.GIT_WRITE.value],
                    "observation_focus": ["inspect repo"],
                    "reasoning": "Repository facts are needed before planning.",
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
                    "reasoning": "Need repository evidence.",
                }
            ],
            "obligation_analysis": [
                {
                    "required_test_levels": ["unit"],
                    "required_setup_steps": [],
                    "required_environment_conditions": ["docker_container"],
                    "required_publish_actions": [],
                    "completion_requirements": ["unit tests and PR checks are accounted for"],
                    "blocker_conditions": [],
                    "reasoning_summary": "The plan must verify both local tests and PR checks.",
                }
            ],
            "planning": [
                {
                    "summary": "Apply repo fix and verify with local and PR evidence",
                    "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
                    "task_intent": "modify",
                    "deliverable_kind": "repository_changes",
                    "capabilities": [Capability.REPO_WRITE.value, Capability.GIT_WRITE.value],
                    "steps": ["edit code", "run unit tests", "wait PR checks"],
                    "success_criteria": ["unit tests pass", "PR checks are green"],
                    "verification_checks": ["run unit tests", "wait for GitHub Actions PR checks"],
                    "requires_mutation": True,
                    "must_change_world": True,
                    "expected_repo_changes": ["src/app.py updated"],
                    "reasoning": "Two different verification concerns should be assessed separately.",
                }
            ],
            "verification_check": [
                {
                    "passed": True,
                    "summary": "Unit test evidence is present.",
                    "checks_passed": ["run unit tests"],
                    "checks_failed": [],
                    "missing_evidence": [],
                    "confidence": "high",
                    "reasoning": "Execution evidence says pytest passed.",
                    "performed_test_levels": ["unit"],
                    "commit_required": False,
                    "push_required": False,
                    "completion_status": "completed",
                },
                {
                    "passed": True,
                    "summary": "PR check evidence is present.",
                    "checks_passed": ["wait for GitHub Actions PR checks"],
                    "checks_failed": [],
                    "missing_evidence": [],
                    "confidence": "medium",
                    "reasoning": "Execution evidence says PR checks passed.",
                    "pr_detected": True,
                    "pr_checks_waited": True,
                    "pr_checks_passed": ["ci/test"],
                    "commit_required": False,
                    "push_required": False,
                    "completion_status": "completed",
                },
                {
                    "passed": True,
                    "summary": "Completion obligation evidence is present.",
                    "checks_passed": ["unit tests and PR checks are accounted for"],
                    "checks_failed": [],
                    "missing_evidence": [],
                    "confidence": "medium",
                    "reasoning": "Execution evidence includes both local unit and PR check status.",
                    "performed_test_levels": ["unit"],
                    "pr_detected": True,
                    "pr_checks_waited": True,
                    "pr_checks_passed": ["ci/test"],
                    "commit_required": False,
                    "push_required": False,
                    "completion_status": "completed",
                },
            ],
        }
    )
    openhands = FakeOpenHandsAdapter(
        artifact_store,
        scripts={
            "observe": ["Repo observed."],
            "execute": ["Changed src/app.py. Ran pytest tests/test_app.py: passed. PR #1 checks ci/test passed."],
        },
    )
    routing = ModelRoutingConfig(
        direct_llm={"verify": "openai/default-verifier"},
        verification_checks={
            "unit_tests": "openai/qwen36-27b",
            "pr_checks": "openai/qwen36-35b",
        },
    )
    controller = WorkflowController(
        llm_backend=llm,
        openhands_adapter=openhands,
        artifact_root=tmp_path / "artifacts",
        approval_provider=StaticApprovalProvider(approve=True, reviewer="test"),
        model_routing=routing,
    )
    report = await controller.run(Task(description="Work with repository and fix code, then verify tests and PR checks"))

    assert report.status == "completed"
    assert report.verification is not None and report.verification.verifier_backend == "direct_llm_per_check"
    assert report.verification.checks_passed[:2] == ["run unit tests", "wait for GitHub Actions PR checks"]
    assert len(llm.calls["verification_check"]) == 3
    assert len(llm.calls["verification"]) == 0
    assert llm.calls["verification_check"][0].metadata["model_override"] == "openai/qwen36-27b"
    assert llm.calls["verification_check"][1].metadata["model_override"] == "openai/qwen36-35b"
    assert llm.calls["verification_check"][0].metadata["verification_check"] == "unit_tests"
    assert llm.calls["verification_check"][1].metadata["verification_check"] == "pr_checks"

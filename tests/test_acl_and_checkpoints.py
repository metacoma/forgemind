from __future__ import annotations

import pytest

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.graph import WorkflowServices, build_workflow_graph
from artifact_workflow_runtime.policy import (
    PolicyEnforcementError,
    PolicyEnforcementPoint,
    RuntimeAction,
    RuntimeResource,
    RuntimeSubject,
)
from artifact_workflow_runtime.state import WorkflowCheckpointRecorder, wrap_stage_node_with_checkpoint
from artifact_workflow_runtime.models import Artifact, ExecutionFamily, Task


@pytest.mark.asyncio
async def test_checkpoint_wrapper_persists_stage_state(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    recorder = WorkflowCheckpointRecorder(store)

    async def node(state):
        return {
            "status": "classified",
            "classification": {
                "normalized_task": "x",
                "needs_world_facts": False,
                "execution_family": ExecutionFamily.DOCUMENTATION_ONLY.value,
                "task_intent": "document",
                "capabilities": [],
                "observation_focus": [],
                "reasoning": "test",
                "risk_level": "low",
            },
            "artifact_ids": ["artifact_classify"],
            "transitions": [
                {
                    "from_status": "created",
                    "to_status": "classified",
                    "stage": "classify",
                    "reason": "classification completed",
                    "artifact_ids_added": ["artifact_classify"],
                }
            ],
        }

    wrapped = wrap_stage_node_with_checkpoint("classify", node, recorder)
    update = await wrapped({"task": {"id": "task_1", "description": "x"}, "status": "created", "artifact_ids": []})

    assert update["status"] == "classified"
    checkpoints = [artifact for artifact in store.list() if artifact.kind == "workflow_checkpoint"]
    assert len(checkpoints) == 1
    payload = store.read_json(checkpoints[0].id)
    assert payload["stage"] == "classify"
    assert payload["before_status"] == "created"
    assert payload["status"] == "classified"
    assert "classification" in payload["update_keys"]


def test_action_acl_denies_publish_side_effects_in_execute_stage() -> None:
    pep = PolicyEnforcementPoint()
    subject = RuntimeSubject(kind="agent", name="openhands", stage="execute")
    resource = RuntimeResource(kind="repo", name="example/repo")

    with pytest.raises(PolicyEnforcementError):
        pep.require(subject=subject, action=RuntimeAction.GIT_PUSH, resource=resource, context={"stage": "execute"})

    decision = pep.require(subject=subject, action="edit_files", resource=resource, context={"stage": "execute"})
    assert decision.allowed is True
    assert decision.action == RuntimeAction.FILE_WRITE


def test_action_acl_allows_publish_actions_only_in_publish_stage() -> None:
    pep = PolicyEnforcementPoint()
    publisher = RuntimeSubject(kind="agent", name="openhands", stage="publish")
    resource = RuntimeResource(kind="repo", name="example/repo")

    assert pep.require(subject=publisher, action="git push", resource=resource, context={"stage": "publish"}).allowed
    assert pep.require(subject=publisher, action="create_pr", resource=resource, context={"stage": "publish"}).allowed


def test_action_acl_coerces_freshness_observation_aliases() -> None:
    assert RuntimeAction.coerce("inspect_package_registry") == RuntimeAction.INTERNET_SEARCH
    assert RuntimeAction.coerce("inspect_release_notes") == RuntimeAction.INTERNET_SEARCH
    assert RuntimeAction.coerce("resolve_package_versions") == RuntimeAction.INTERNET_SEARCH

from __future__ import annotations

from artifact_workflow_runtime.models import Capability, ExecutionFamily, ExecutionPlan, TaskClassification
from artifact_workflow_runtime.policy import PolicyEngine


def test_policy_requires_approval_for_mutation() -> None:
    engine = PolicyEngine()
    classification = TaskClassification(
        normalized_task="change repository",
        needs_world_facts=True,
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        capabilities=[Capability.REPO_READ, Capability.REPO_WRITE],
        observation_focus=["inspect failing tests"],
        reasoning="repo change",
    )
    plan = ExecutionPlan(
        summary="edit code",
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        capabilities=[Capability.REPO_WRITE, Capability.GIT_WRITE],
        steps=["edit file"],
        success_criteria=["tests pass"],
        verification_checks=["pytest"],
        requires_mutation=True,
        reasoning="need code change",
    )
    decision = engine.decide(classification, plan)
    assert decision.allowed is True
    assert decision.requires_approval is True

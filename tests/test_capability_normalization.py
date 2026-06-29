from artifact_workflow_runtime.models import Capability, ExecutionFamily, TaskClassification


def test_task_classification_normalizes_capabilities_and_ignores_unknown_values() -> None:
    model = TaskClassification.model_validate({
        "normalized_task": "create pr for repo change",
        "needs_world_facts": False,
        "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
        "task_intent": "modify",
        "capabilities": ["repo_read", "repo_create_pr", "git_push", "totally_unknown_capability"],
        "observation_focus": [],
        "reasoning": "Need repository access and publish-related capability.",
        "risk_level": "medium",
    })

    assert model.capabilities == [Capability.REPO_READ, Capability.REPO_CREATE_PR, Capability.GIT_WRITE]

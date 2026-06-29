from __future__ import annotations

from artifact_workflow_runtime.models import ObservationRequest, Task, TaskClassification


class ObservationService:
    def build_request(self, task: Task, classification: TaskClassification) -> ObservationRequest:
        focus = "\n".join(f"- {item}" for item in classification.observation_focus) or "- collect the minimum world facts needed"
        prompt = (
            "You are gathering world facts for a controller-driven workflow.\n"
            "Do not decide the plan. Do not mutate the world. Observe only.\n\n"
            f"Task: {classification.normalized_task}\n"
            f"Execution family: {classification.execution_family.value}\n"
            f"Focus:\n{focus}\n\n"
            "Return concise factual evidence, relevant files/paths/commands/state, and unknowns."
        )
        return ObservationRequest(
            task_id=task.id,
            execution_family=classification.execution_family,
            capabilities=classification.capabilities,
            prompt=prompt,
            repository=task.repository,
            branch=task.branch,
            git_provider=task.git_provider,
            metadata={"mode": "observe_only"},
        )

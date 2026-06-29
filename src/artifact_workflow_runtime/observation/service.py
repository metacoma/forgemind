from __future__ import annotations

from artifact_workflow_runtime.models import ExecutionFamily, ObservationRequest, Task, TaskClassification


class ObservationService:
    def build_request(self, task: Task, classification: TaskClassification) -> ObservationRequest:
        focus = "\n".join(f"- {item}" for item in classification.observation_focus) or "- collect the minimum world facts needed"
        prompt = self._build_prompt(task, classification, focus)
        return ObservationRequest(
            task_id=task.id,
            execution_family=classification.execution_family,
            capabilities=classification.capabilities,
            prompt=prompt,
            metadata={"mode": "observe_only", "evidence_required": True},
        )

    def _build_prompt(self, task: Task, classification: TaskClassification, focus: str) -> str:
        family = classification.execution_family
        if family == ExecutionFamily.REPOSITORY_CHANGE:
            return (
                "You are gathering repository facts for a controller-driven workflow.\n"
                "Observe only. Do not edit files, do not commit, do not push, do not mutate the repository.\n"
                "Use only the task text, the existing environment, and already available credentials or checked-out workspaces.\n\n"
                f"Task: {classification.normalized_task}\n"
                f"Focus:\n{focus}\n\n"
                "Return factual evidence only. Include:\n"
                "- repository root or clone location if found\n"
                "- current branch and HEAD commit if available\n"
                "- relevant files, directories, proto definitions, client implementations, build files\n"
                "- concrete commands you ran and their outputs\n"
                "- candidate build/test commands\n"
                "- blockers and unknowns\n"
                "Do not produce a plan yet."
            )
        if family == ExecutionFamily.HOST_OPERATION:
            return (
                "You are gathering host facts for a controller-driven workflow.\n"
                "Observe only. Do not change services, configs, or packages.\n\n"
                f"Task: {classification.normalized_task}\n"
                f"Focus:\n{focus}\n\n"
                "Return factual evidence only: relevant hosts, services, logs, config paths, commands, outputs, blockers, unknowns."
            )
        if family == ExecutionFamily.CLUSTER_OPERATION:
            return (
                "You are gathering cluster facts for a controller-driven workflow.\n"
                "Observe only. Do not apply manifests or change cluster state.\n\n"
                f"Task: {classification.normalized_task}\n"
                f"Focus:\n{focus}\n\n"
                "Return factual evidence only: namespaces, workloads, status, events, commands, outputs, blockers, unknowns."
            )
        if family == ExecutionFamily.NETWORK_INVESTIGATION:
            return (
                "You are gathering network facts for a controller-driven workflow.\n"
                "Observe only. Do not change network configuration.\n\n"
                f"Task: {classification.normalized_task}\n"
                f"Focus:\n{focus}\n\n"
                "Return factual evidence only: endpoints, routes, diagnostics commands, outputs, blockers, unknowns."
            )
        return (
            "You are gathering world facts for a controller-driven workflow.\n"
            "Do not decide the plan. Do not mutate the world. Observe only.\n"
            "Use only the task text, the available environment, and already available credentials or checked-out workspaces.\n\n"
            f"Task: {classification.normalized_task}\n"
            f"Execution family: {classification.execution_family.value}\n"
            f"Focus:\n{focus}\n\n"
            "Return concise factual evidence, relevant files/paths/commands/state, and unknowns."
        )

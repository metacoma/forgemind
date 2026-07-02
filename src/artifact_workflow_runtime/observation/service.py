from __future__ import annotations

from artifact_workflow_runtime.models import Capability, ExecutionFamily, ObservationRequest, RoutingDecision, Task, TaskClassification, WorkPacketKind

_READ_ONLY_CAPABILITIES = {
    Capability.DOCUMENT_READ,
    Capability.REPO_READ,
    Capability.SHELL_READ,
    Capability.GIT_READ,
    Capability.K8S_READ,
    Capability.NETWORK_DIAGNOSTICS,
}


def _read_only_capabilities(capabilities: list[Capability]) -> list[Capability]:
    return [cap for cap in capabilities if cap in _READ_ONLY_CAPABILITIES]


class ObservationService:
    def build_request(self, task: Task, classification: TaskClassification, route: RoutingDecision | None = None) -> ObservationRequest:
        focus_items = list(classification.observation_focus)
        if route is not None:
            focus_items.extend(item for item in route.observation_focus if item not in focus_items)
        focus = "\n".join(f"- {item}" for item in focus_items) or "- collect the minimum world facts needed"
        prompt = self._build_prompt(task, classification, focus)
        return ObservationRequest(
            task_id=task.id,
            execution_family=classification.execution_family,
            capabilities=_read_only_capabilities(classification.capabilities),
            prompt=prompt,
            objective="collect controller-requested world facts without mutation",
            focus=focus_items or ["collect the minimum world facts needed"],
            required_facts=list(route.required_evidence_types) if route is not None else [],
            scope_constraints=["observe only", "do not plan", "do not mutate repository/hosts/cluster"],
            metadata={"mode": "observe_only", "evidence_required": True},
        )

    def build_research_request(self, task: Task, classification: TaskClassification, route: RoutingDecision) -> ObservationRequest:
        targets = "\n".join(f"- {item}" for item in route.research_targets) or "- identify the official sources needed for this task"
        evidence_types = "\n".join(f"- {item}" for item in route.required_evidence_types) or "- official_docs"
        prompt = (
            "You are gathering fresh external research evidence for a controller-driven workflow.\n"
            "Observe only. Do not modify the local repository, hosts, or cluster.\n"
            "Use internet access, package registries, official documentation sites, release pages, and repository metadata if available in the environment.\n"
            "Prefer official sources over blogs or forum posts.\n"
            "Return factual evidence only, with source attribution and freshness hints.\n\n"
            f"Task: {task.description}\n"
            f"Execution family: {classification.execution_family.value}\n"
            f"Task intent: {classification.task_intent}\n\n"
            "Research targets:\n"
            f"{targets}\n\n"
            "Required evidence types:\n"
            f"{evidence_types}\n\n"
            "Include in the result:\n"
            "- official docs URLs and page titles if found\n"
            "- package names and current versions if relevant\n"
            "- release notes or breaking-change references if relevant\n"
            "- short source-backed facts that will help planning\n"
            "- unknowns and blockers\n"
            "Do not produce a plan yet."
        )
        return ObservationRequest(
            task_id=task.id,
            execution_family=classification.execution_family,
            capabilities=[],
            prompt=prompt,
            objective="collect fresh external source-backed facts without local mutation",
            focus=list(route.research_targets),
            required_facts=list(route.required_evidence_types),
            scope_constraints=["official sources preferred", "observe only", "do not produce a plan"],
            work_packet_kind=WorkPacketKind.RESEARCH,
            allowed_actions=["internet_research", "read_official_docs", "inspect_public_metadata", "collect_source_attribution"],
            forbidden_actions=["edit_files", "write_files", "run_mutating_commands", "commit", "push", "git push", "git push --force", "git tag", "git merge", "git rebase", "create_pr", "open_pull_request", "publish", "release", "change_hosts", "change_cluster_state", "change_workflow_decision", "declare_task_completed_or_accepted"],
            expected_outputs=["source_urls", "version_facts", "release_notes", "blockers", "unknowns"],
            metadata={
                "mode": "observe_only",
                "evidence_required": True,
                "source": "fresh_external_research",
                "research_targets": list(route.research_targets),
            },
        )

    def _build_prompt(self, task: Task, classification: TaskClassification, focus: str) -> str:
        family = classification.execution_family
        if family == ExecutionFamily.REPOSITORY_CHANGE:
            return (
                "You are gathering repository facts for a controller-driven workflow.\n"
                "Observe only. Do not edit files, do not commit, do not push, do not mutate the repository.\n"
                "The execution environment is a Docker container unless evidence shows otherwise.\n"
                "Use only the task text, the existing environment, and already available credentials or checked-out workspaces.\n\n"
                f"Task: {classification.normalized_task}\n"
                f"Focus:\n{focus}\n\n"
                "Return factual evidence only. Include:\n"
                "- repository root or clone location if found\n"
                "- current branch and HEAD commit if available\n"
                "- relevant files, directories, proto definitions, client implementations, build files\n"
                "- current test topology, including integration harnesses and how other clients are validated\n"
                "- dependency/setup commands needed to run build/unit/integration tests inside Docker\n"
                "- for repo-supported setup/runtime paths, populate structured_evidence.files_observed.role using setup_script, runtime_probe_script, integration_harness, smoke_harness, or ci_workflow\n"
                "- when you actually run a command during observation, populate structured_evidence.commands_run.role using observed_setup_path, observed_runtime_probe, build, unit_test, integration_test, or smoke_test\n"
                "- when you record an executed check, populate structured_evidence.tests.level using build, unit, integration, smoke, or runtime_probe\n"
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

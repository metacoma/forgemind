from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


def _normalize(value: object) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


@dataclass(frozen=True)
class RuntimeActionDefinition:
    canonical: str
    aliases: tuple[str, ...] = ()


class RuntimeActionRegistry:
    """Canonical registry for runtime action aliases and stage permissions.

    This keeps action normalization and per-stage default ACLs in one place so
    freshness/research capability additions cannot drift away from stage
    contract validation.
    """

    DEFINITIONS: tuple[RuntimeActionDefinition, ...] = (
        RuntimeActionDefinition("repo.read", ("read_repo", "repo_read", "inspect_repository", "read_repository", "inspect_repo", "collect_evidence")),
        RuntimeActionDefinition("file.read", ("read_files", "filesystem", "context_packet_text", "file_read")),
        RuntimeActionDefinition("file.write", ("write_files", "edit_files", "file_write")),
        RuntimeActionDefinition("shell.run", ("inspect_runtime_state", "run_read_only_commands", "run_commands", "run_read_commands", "run_write_commands", "shell", "shell_run")),
        RuntimeActionDefinition("test.run", ("run_tests", "test_runtime", "test_run")),
        RuntimeActionDefinition("git.read", ("inspect_git", "inspect_git_read_only", "inspect_pr_checks", "git_status", "git", "git_diff", "git_read")),
        RuntimeActionDefinition("git.commit", ("commit_when_required", "commit", "git_commit")),
        RuntimeActionDefinition("git.push", ("push_when_required", "push", "git_push", "git_push_--force")),
        RuntimeActionDefinition("pr.create", ("create_pr_when_required", "create_pr", "open_pull_request", "pull_request")),
        RuntimeActionDefinition(
            "internet.search",
            (
                "internet_research",
                "read_official_docs",
                "inspect_public_metadata",
                "collect_source_attribution",
                "search_web",
                "internet_search",
                "web_search",
                "inspect_package_registry",
                "read_package_registry",
                "inspect_release_notes",
                "read_release_notes",
                "inspect_changelog",
                "resolve_package_versions",
            ),
        ),
        RuntimeActionDefinition("k8s.read", ("k8s_read", "kubectl_get")),
        RuntimeActionDefinition("k8s.apply", ("k8s_write", "kubectl_apply", "apply_cluster_changes")),
        RuntimeActionDefinition("argocd.sync", ("argocd_sync",)),
        RuntimeActionDefinition("ssh.run", ("ssh", "ssh_run", "change_host_config")),
    )

    DEFAULT_STAGE_ALLOW: Mapping[str, tuple[str, ...]] = {
        "research": ("internet.search", "repo.read", "file.read"),
        "observe": ("repo.read", "file.read", "shell.run", "git.read", "k8s.read", "internet.search"),
        "execute": ("repo.read", "file.read", "file.write", "shell.run", "test.run", "git.read"),
        "repair": ("repo.read", "file.read", "file.write", "shell.run", "test.run", "git.read"),
        "verify": ("repo.read", "file.read", "shell.run", "test.run", "git.read", "k8s.read"),
        "publish": ("repo.read", "file.read", "shell.run", "test.run", "git.read", "git.commit", "git.push", "pr.create"),
    }

    def __init__(self) -> None:
        aliases: dict[str, str] = {}
        for definition in self.DEFINITIONS:
            aliases[_normalize(definition.canonical)] = definition.canonical
            for alias in definition.aliases:
                aliases[_normalize(alias)] = definition.canonical
        self._aliases = aliases

    def canonicalize(self, value: object) -> str | None:
        text = _normalize(value)
        if not text:
            return None
        return self._aliases.get(text)

    def stage_allowed(self, stage: str) -> tuple[str, ...]:
        return self.DEFAULT_STAGE_ALLOW.get(str(stage or "").strip().lower(), ())


RUNTIME_ACTION_REGISTRY = RuntimeActionRegistry()

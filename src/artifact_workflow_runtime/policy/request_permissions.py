from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .acl import RuntimeAction, StaticStagePolicyDecisionPoint


class RequestPermission(str, Enum):
    READ_FILES = "read_files"
    RUN_READ_ONLY_COMMANDS = "run_read_only_commands"
    INSPECT_REPO = "inspect_repo"
    INSPECT_RUNTIME_STATE = "inspect_runtime_state"

    INTERNET_RESEARCH = "internet_research"
    READ_OFFICIAL_DOCS = "read_official_docs"
    INSPECT_RELEASE_NOTES = "inspect_release_notes"
    INSPECT_PACKAGE_REGISTRY = "inspect_package_registry"
    INSPECT_PUBLIC_METADATA = "inspect_public_metadata"
    COLLECT_SOURCE_ATTRIBUTION = "collect_source_attribution"

    EDIT_FILES = "edit_files"
    WRITE_FILES = "write_files"
    RUN_COMMANDS = "run_commands"
    RUN_TESTS = "run_tests"
    INSPECT_GIT_READ_ONLY = "inspect_git_read_only"
    COLLECT_EVIDENCE = "collect_evidence"

    INSPECT_GIT = "inspect_git"
    COMMIT_WHEN_REQUIRED = "commit_when_required"
    PUSH_WHEN_REQUIRED = "push_when_required"
    CREATE_PR_WHEN_REQUIRED = "create_pr_when_required"
    INSPECT_PR_CHECKS = "inspect_pr_checks"

    FILESYSTEM = "filesystem"
    SHELL = "shell"
    GIT = "git"
    TEST_RUNTIME = "test_runtime"
    CONTEXT_PACKET_TEXT = "context_packet_text"
    ARTIFACT_TEXT = "artifact_text"
    TASK_TEXT = "task_text"
    STRUCTURED_EVIDENCE = "structured_evidence"
    SCHEMA_TEXT = "schema_text"


@dataclass(frozen=True)
class RequestPermissionSpec:
    permission: RequestPermission
    runtime_action: RuntimeAction
    allowed_stages: frozenset[str]
    aliases: frozenset[str]


class RequestPermissionCatalog:
    _SPECS: tuple[RequestPermissionSpec, ...] = (
        RequestPermissionSpec(RequestPermission.READ_FILES, RuntimeAction.FILE_READ, frozenset({"observe"}), frozenset({"read_files"})),
        RequestPermissionSpec(RequestPermission.RUN_READ_ONLY_COMMANDS, RuntimeAction.SHELL_RUN, frozenset({"observe"}), frozenset({"run_read_only_commands"})),
        RequestPermissionSpec(RequestPermission.INSPECT_REPO, RuntimeAction.REPO_READ, frozenset({"observe"}), frozenset({"inspect_repo"})),
        RequestPermissionSpec(RequestPermission.INSPECT_RUNTIME_STATE, RuntimeAction.SHELL_RUN, frozenset({"observe"}), frozenset({"inspect_runtime_state"})),

        RequestPermissionSpec(RequestPermission.INTERNET_RESEARCH, RuntimeAction.INTERNET_SEARCH, frozenset({"research"}), frozenset({"internet_research", "search_web", "internet_search", "web_search"})),
        RequestPermissionSpec(RequestPermission.READ_OFFICIAL_DOCS, RuntimeAction.INTERNET_SEARCH, frozenset({"research"}), frozenset({"read_official_docs"})),
        RequestPermissionSpec(RequestPermission.INSPECT_RELEASE_NOTES, RuntimeAction.INTERNET_SEARCH, frozenset({"research"}), frozenset({"inspect_release_notes"})),
        RequestPermissionSpec(RequestPermission.INSPECT_PACKAGE_REGISTRY, RuntimeAction.INTERNET_SEARCH, frozenset({"research"}), frozenset({"inspect_package_registry"})),
        RequestPermissionSpec(RequestPermission.INSPECT_PUBLIC_METADATA, RuntimeAction.INTERNET_SEARCH, frozenset({"research"}), frozenset({"inspect_public_metadata"})),
        RequestPermissionSpec(RequestPermission.COLLECT_SOURCE_ATTRIBUTION, RuntimeAction.INTERNET_SEARCH, frozenset({"research"}), frozenset({"collect_source_attribution"})),

        RequestPermissionSpec(RequestPermission.EDIT_FILES, RuntimeAction.FILE_WRITE, frozenset({"execute", "repair"}), frozenset({"edit_files"})),
        RequestPermissionSpec(RequestPermission.WRITE_FILES, RuntimeAction.FILE_WRITE, frozenset({"execute", "repair"}), frozenset({"write_files", "file_write"})),
        RequestPermissionSpec(RequestPermission.RUN_COMMANDS, RuntimeAction.SHELL_RUN, frozenset({"execute", "repair"}), frozenset({"run_commands", "shell_run", "run_read_commands", "run_write_commands"})),
        RequestPermissionSpec(RequestPermission.RUN_TESTS, RuntimeAction.TEST_RUN, frozenset({"execute", "repair"}), frozenset({"run_tests", "test_run"})),
        RequestPermissionSpec(RequestPermission.INSPECT_GIT_READ_ONLY, RuntimeAction.GIT_READ, frozenset({"execute", "repair"}), frozenset({"inspect_git_read_only"})),
        RequestPermissionSpec(RequestPermission.COLLECT_EVIDENCE, RuntimeAction.REPO_READ, frozenset({"execute", "repair", "publish"}), frozenset({"collect_evidence"})),

        RequestPermissionSpec(RequestPermission.INSPECT_GIT, RuntimeAction.GIT_READ, frozenset({"publish"}), frozenset({"inspect_git"})),
        RequestPermissionSpec(RequestPermission.COMMIT_WHEN_REQUIRED, RuntimeAction.GIT_COMMIT, frozenset({"publish"}), frozenset({"commit_when_required"})),
        RequestPermissionSpec(RequestPermission.PUSH_WHEN_REQUIRED, RuntimeAction.GIT_PUSH, frozenset({"publish"}), frozenset({"push_when_required"})),
        RequestPermissionSpec(RequestPermission.CREATE_PR_WHEN_REQUIRED, RuntimeAction.PR_CREATE, frozenset({"publish"}), frozenset({"create_pr_when_required"})),
        RequestPermissionSpec(RequestPermission.INSPECT_PR_CHECKS, RuntimeAction.GIT_READ, frozenset({"publish"}), frozenset({"inspect_pr_checks"})),

        RequestPermissionSpec(RequestPermission.FILESYSTEM, RuntimeAction.FILE_READ, frozenset({"verify"}), frozenset({"filesystem"})),
        RequestPermissionSpec(RequestPermission.SHELL, RuntimeAction.SHELL_RUN, frozenset({"verify"}), frozenset({"shell"})),
        RequestPermissionSpec(RequestPermission.GIT, RuntimeAction.GIT_READ, frozenset({"verify"}), frozenset({"git"})),
        RequestPermissionSpec(RequestPermission.TEST_RUNTIME, RuntimeAction.TEST_RUN, frozenset({"verify"}), frozenset({"test_runtime"})),
        RequestPermissionSpec(RequestPermission.CONTEXT_PACKET_TEXT, RuntimeAction.FILE_READ, frozenset({"verify"}), frozenset({"context_packet_text"})),
        RequestPermissionSpec(RequestPermission.ARTIFACT_TEXT, RuntimeAction.FILE_READ, frozenset({"verify"}), frozenset({"artifact_text"})),
        RequestPermissionSpec(RequestPermission.TASK_TEXT, RuntimeAction.FILE_READ, frozenset({"verify"}), frozenset({"task_text"})),
        RequestPermissionSpec(RequestPermission.STRUCTURED_EVIDENCE, RuntimeAction.FILE_READ, frozenset({"verify"}), frozenset({"structured_evidence"})),
        RequestPermissionSpec(RequestPermission.SCHEMA_TEXT, RuntimeAction.FILE_READ, frozenset({"verify"}), frozenset({"schema_text"})),
    )
    _BY_ALIAS = {alias: spec for spec in _SPECS for alias in spec.aliases}
    _BY_PERMISSION = {spec.permission: spec for spec in _SPECS}

    @classmethod
    def normalize(cls, value: object) -> str:
        return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")

    @classmethod
    def resolve(cls, value: object) -> RequestPermissionSpec:
        normalized = cls.normalize(value)
        spec = cls._BY_ALIAS.get(normalized)
        if spec is None:
            raise ValueError(f"unknown request permission '{value}'")
        return spec

    @classmethod
    def _resolve_runtime_compat(cls, value: object, *, stage: str) -> RequestPermissionSpec:
        runtime_action = RuntimeAction.coerce(value)
        normalized_stage = str(stage or "").strip().lower()
        stage_allow = StaticStagePolicyDecisionPoint.DEFAULT_STAGE_ALLOW.get(normalized_stage, frozenset())
        if runtime_action == RuntimeAction.UNKNOWN or runtime_action not in stage_allow:
            raise ValueError(f"unknown request permission '{value}'")
        return RequestPermissionSpec(
            permission=RequestPermissionCatalog._compat_permission_for_runtime(runtime_action),
            runtime_action=runtime_action,
            allowed_stages=frozenset({normalized_stage}),
            aliases=frozenset({cls.normalize(value)}),
        )

    @staticmethod
    def _compat_permission_for_runtime(runtime_action: RuntimeAction) -> RequestPermission:
        mapping = {
            RuntimeAction.REPO_READ: RequestPermission.INSPECT_REPO,
            RuntimeAction.FILE_READ: RequestPermission.READ_FILES,
            RuntimeAction.FILE_WRITE: RequestPermission.EDIT_FILES,
            RuntimeAction.SHELL_RUN: RequestPermission.RUN_COMMANDS,
            RuntimeAction.TEST_RUN: RequestPermission.RUN_TESTS,
            RuntimeAction.GIT_READ: RequestPermission.INSPECT_GIT,
            RuntimeAction.GIT_COMMIT: RequestPermission.COMMIT_WHEN_REQUIRED,
            RuntimeAction.GIT_PUSH: RequestPermission.PUSH_WHEN_REQUIRED,
            RuntimeAction.PR_CREATE: RequestPermission.CREATE_PR_WHEN_REQUIRED,
            RuntimeAction.INTERNET_SEARCH: RequestPermission.INTERNET_RESEARCH,
            RuntimeAction.K8S_READ: RequestPermission.INSPECT_RUNTIME_STATE,
            RuntimeAction.K8S_APPLY: RequestPermission.EDIT_FILES,
            RuntimeAction.ARGOCD_SYNC: RequestPermission.EDIT_FILES,
            RuntimeAction.SSH_RUN: RequestPermission.RUN_COMMANDS,
        }
        return mapping[runtime_action]

    @classmethod
    def require_stage_permission(cls, value: object, *, stage: str) -> RequestPermissionSpec:
        normalized_stage = str(stage or "").strip().lower()
        try:
            spec = cls.resolve(value)
        except ValueError:
            return cls._resolve_runtime_compat(value, stage=normalized_stage)
        if normalized_stage not in spec.allowed_stages:
            allowed = ", ".join(sorted(spec.allowed_stages))
            raise ValueError(f"permission '{spec.permission.value}' is not allowed for stage {normalized_stage}; allowed stages: {allowed}")
        return spec

    @classmethod
    def runtime_actions_for(cls, values: Iterable[object], *, stage: str) -> list[RuntimeAction]:
        actions: list[RuntimeAction] = []
        for value in values:
            spec = cls.require_stage_permission(value, stage=stage)
            if spec.runtime_action not in actions:
                actions.append(spec.runtime_action)
        return actions


OBSERVE_PACKET_PERMISSIONS: tuple[str, ...] = (
    RequestPermission.READ_FILES.value,
    RequestPermission.RUN_READ_ONLY_COMMANDS.value,
    RequestPermission.INSPECT_REPO.value,
    RequestPermission.INSPECT_RUNTIME_STATE.value,
)

RESEARCH_PACKET_PERMISSIONS: tuple[str, ...] = (
    RequestPermission.INTERNET_RESEARCH.value,
    RequestPermission.READ_OFFICIAL_DOCS.value,
    RequestPermission.INSPECT_RELEASE_NOTES.value,
    RequestPermission.INSPECT_PACKAGE_REGISTRY.value,
    RequestPermission.INSPECT_PUBLIC_METADATA.value,
    RequestPermission.COLLECT_SOURCE_ATTRIBUTION.value,
)

EXECUTE_PACKET_PERMISSIONS: tuple[str, ...] = (
    RequestPermission.EDIT_FILES.value,
    RequestPermission.RUN_COMMANDS.value,
    RequestPermission.RUN_TESTS.value,
    RequestPermission.INSPECT_GIT_READ_ONLY.value,
    RequestPermission.COLLECT_EVIDENCE.value,
)

PUBLISH_PACKET_PERMISSIONS: tuple[str, ...] = (
    RequestPermission.INSPECT_GIT.value,
    RequestPermission.COMMIT_WHEN_REQUIRED.value,
    RequestPermission.PUSH_WHEN_REQUIRED.value,
    RequestPermission.CREATE_PR_WHEN_REQUIRED.value,
    RequestPermission.INSPECT_PR_CHECKS.value,
)

VERIFY_PACKET_PERMISSIONS: tuple[str, ...] = (
    RequestPermission.FILESYSTEM.value,
    RequestPermission.SHELL.value,
    RequestPermission.GIT.value,
    RequestPermission.TEST_RUNTIME.value,
    RequestPermission.CONTEXT_PACKET_TEXT.value,
    RequestPermission.ARTIFACT_TEXT.value,
    RequestPermission.TASK_TEXT.value,
    RequestPermission.STRUCTURED_EVIDENCE.value,
    RequestPermission.SCHEMA_TEXT.value,
)

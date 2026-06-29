from __future__ import annotations

from artifact_workflow_runtime.models import Capability, ExecutionFamily


WORLD_BACKED_FAMILIES = {
    ExecutionFamily.REPOSITORY_CHANGE,
    ExecutionFamily.HOST_OPERATION,
    ExecutionFamily.CLUSTER_OPERATION,
    ExecutionFamily.NETWORK_INVESTIGATION,
}


REPOSITORY_KEYWORDS = (
    "repo",
    "repository",
    "clone",
    "git",
    "branch",
    "commit",
    "tag",
    "pull request",
    "grpc",
    "readme",
    "build",
    "test",
    "compile",
    "freeplane_plugin_grpc",
)

HOST_KEYWORDS = ("ssh", "systemd", "service", "journalctl", "host", "server", "linux", "docker")
K8S_KEYWORDS = ("kubectl", "helm", "argocd", "k8s", "kubernetes", "pod", "deployment", "namespace")
NETWORK_KEYWORDS = ("dns", "route", "routing", "latency", "connectivity", "ping", "tcp", "http")


def family_default_capabilities(family: ExecutionFamily) -> list[Capability]:
    mapping = {
        ExecutionFamily.DOCUMENTATION_ONLY: [Capability.DOCUMENT_READ],
        ExecutionFamily.REPOSITORY_CHANGE: [Capability.REPO_READ, Capability.REPO_WRITE, Capability.GIT_WRITE],
        ExecutionFamily.HOST_OPERATION: [Capability.HOST_ACCESS, Capability.SHELL_READ, Capability.SHELL_WRITE],
        ExecutionFamily.CLUSTER_OPERATION: [Capability.K8S_READ, Capability.K8S_WRITE],
        ExecutionFamily.NETWORK_INVESTIGATION: [Capability.NETWORK_DIAGNOSTICS, Capability.SHELL_READ],
    }
    return mapping[family]


def family_requires_observation(family: ExecutionFamily) -> bool:
    return family in WORLD_BACKED_FAMILIES


def family_requires_evidence_gate(family: ExecutionFamily) -> bool:
    return family in WORLD_BACKED_FAMILIES


def task_text_suggests_world_facts(task_text: str) -> bool:
    lowered = task_text.lower()
    keyword_groups = (REPOSITORY_KEYWORDS, HOST_KEYWORDS, K8S_KEYWORDS, NETWORK_KEYWORDS)
    return any(keyword in lowered for group in keyword_groups for keyword in group)


IMPLEMENTATION_KEYWORDS = ("add", "implement", "create", "fix", "write", "update", "modify", "extend", "remove")
INVESTIGATION_KEYWORDS = ("investigate", "analyze", "inspect", "check", "find", "diagnose", "understand")
DOCUMENTATION_KEYWORDS = ("document", "documentation", "readme", "describe", "outline", "instructions")


def infer_task_intent(task_text: str) -> str:
    lowered = task_text.lower()
    if any(keyword in lowered for keyword in IMPLEMENTATION_KEYWORDS):
        return "implement"
    if any(keyword in lowered for keyword in INVESTIGATION_KEYWORDS):
        return "investigate"
    if any(keyword in lowered for keyword in DOCUMENTATION_KEYWORDS):
        return "document"
    return "investigate"

from __future__ import annotations

from artifact_workflow_runtime.models import Capability, ExecutionFamily


def family_default_capabilities(family: ExecutionFamily) -> list[Capability]:
    mapping = {
        ExecutionFamily.DOCUMENTATION_ONLY: [Capability.DOCUMENT_READ],
        ExecutionFamily.REPOSITORY_CHANGE: [Capability.REPO_READ, Capability.REPO_WRITE, Capability.GIT_WRITE],
        ExecutionFamily.HOST_OPERATION: [Capability.HOST_ACCESS, Capability.SHELL_READ, Capability.SHELL_WRITE],
        ExecutionFamily.CLUSTER_OPERATION: [Capability.K8S_READ, Capability.K8S_WRITE],
        ExecutionFamily.NETWORK_INVESTIGATION: [Capability.NETWORK_DIAGNOSTICS, Capability.SHELL_READ],
    }
    return mapping[family]

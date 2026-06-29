from __future__ import annotations

from artifact_workflow_runtime.models import Capability

MUTATING_CAPABILITIES = {
    Capability.REPO_WRITE,
    Capability.SHELL_WRITE,
    Capability.GIT_WRITE,
    Capability.K8S_WRITE,
}

READ_ONLY_CAPABILITIES = {
    Capability.DOCUMENT_READ,
    Capability.REPO_READ,
    Capability.SHELL_READ,
    Capability.GIT_READ,
    Capability.K8S_READ,
    Capability.NETWORK_DIAGNOSTICS,
}

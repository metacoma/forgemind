from __future__ import annotations

from artifact_workflow_runtime.models import ExecutionPlan


_PUBLISH_PHASE_TERMS = (
    "commit",
    "push",
    "pull request",
    " pr",
    "create_pr",
    "open_pull_request",
    "wait_pr_checks",
    "pr checks",
    "publish branch",
    "publish obligations",
)
_SETUP_TERMS = ("install", "setup", "bootstrap", "prepare", "runtime", "environment", "dependency", "sdk", "toolchain", "probe", "version")
_DOC_TERMS = ("readme", "documentation", "docs", "example", "examples", "snippet")
_CI_TERMS = ("workflow", "github actions", "ci", "pipeline", "build script")
_TEST_TERMS = ("test", "unit", "integration", "smoke", "build", "compile", "verify")


def execute_phase_items(items: list[str]) -> list[str]:
    """Keep only obligations that belong to execute.

    The publish stage owns commit/push/PR/check-wait side effects. Execute may
    edit files and run build/unit/integration checks, but it must not receive
    publish obligations as success criteria or verification commands.
    """

    kept: list[str] = []
    for item in items:
        text = str(item).strip()
        lowered = text.lower()
        if not text:
            continue
        if any(term in lowered for term in _PUBLISH_PHASE_TERMS):
            continue
        kept.append(text)
    return kept


def packet_scoped_execute_items(items: list[str], packet) -> list[str]:
    scoped = execute_phase_items(items)
    if packet is None:
        return scoped
    packet_type = getattr(getattr(packet, "packet_type", None), "value", getattr(packet, "packet_type", None)) or ""
    local_contract = getattr(packet, "local_contract", None)
    env_terms = [str(item).lower() for item in getattr(local_contract, "environment_nodes", [])]
    work_terms = [str(item).lower() for item in getattr(local_contract, "work_surfaces", [])]
    verification_terms = [str(item).lower() for item in getattr(local_contract, "verification_levels", [])]
    publish_terms = [str(item).lower() for item in getattr(local_contract, "publish_requirements", [])]

    def keep(text: str) -> bool:
        lowered = text.lower()
        if packet_type == "setup":
            if any(term and term in lowered for term in env_terms):
                return True
            return any(term in lowered for term in _SETUP_TERMS)
        if packet_type == "implementation":
            if any(term and term in lowered for term in work_terms):
                return True
            return not any(term in lowered for term in (*_DOC_TERMS, *_CI_TERMS))
        if packet_type in {"test", "integration", "verification"}:
            if any(term and term in lowered for term in verification_terms):
                return True
            return any(term in lowered for term in _TEST_TERMS)
        if packet_type == "docs":
            if any(term and term in lowered for term in work_terms):
                return True
            return any(term in lowered for term in _DOC_TERMS)
        if packet_type == "publish_preparation":
            if any(term and term in lowered for term in publish_terms):
                return True
            return any(term in lowered for term in _CI_TERMS)
        return True

    kept = [item for item in scoped if keep(item)]
    return kept or scoped


def execute_prompt_steps(plan: ExecutionPlan) -> list[str]:
    return execute_phase_items(list(plan.steps))


def execute_success_criteria(plan: ExecutionPlan) -> list[str]:
    return execute_phase_items(list(plan.success_criteria))


def execute_verification_commands(plan: ExecutionPlan) -> list[str]:
    return execute_phase_items(list(plan.verification_checks))

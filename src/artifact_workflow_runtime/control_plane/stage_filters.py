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


def execute_prompt_steps(plan: ExecutionPlan) -> list[str]:
    return execute_phase_items(list(plan.steps))


def execute_success_criteria(plan: ExecutionPlan) -> list[str]:
    return execute_phase_items(list(plan.success_criteria))


def execute_verification_commands(plan: ExecutionPlan) -> list[str]:
    return execute_phase_items(list(plan.verification_checks))

_SETUP_SCOPE_TERMS = (
    "setup",
    "bootstrap",
    "install",
    "dependency",
    "sdk",
    "tool",
    "probe",
    "runtime",
    "restore",
    "dotnet --version",
    "protoc --version",
    "freeplane",
    "xvfb",
    "environment",
)
_IMPLEMENTATION_SCOPE_TERMS = ("implement", "code", "client", "library", "source", "feature", "fix", "proto", "grpc", "api", "class", "method")
_TEST_SCOPE_TERMS = ("test", "unit", "integration", "smoke", "verify", "verification", "xunit", "pytest", "dotnet test")
_DOCS_SCOPE_TERMS = ("doc", "readme", "example", "sample", "usage", "tutorial")
_CI_SCOPE_TERMS = ("ci", "workflow", "github actions", "actions/setup", "pipeline", "build script")


def packet_scoped_execute_items(items: list[str], packet: object | None, *, default_to_packet: bool = True) -> list[str]:
    """Filter execute-stage instructions to the active packet scope.

    The decomposition packet is the hard boundary. A setup packet must not
    receive the full implementation/docs/CI checklist, while implementation and
    test packets must not receive publish obligations.
    """

    execute_items = execute_phase_items(items)
    if packet is None:
        return execute_items

    packet_type = str(getattr(getattr(packet, "packet_type", None), "value", getattr(packet, "packet_type", ""))).lower()
    packet_text = " ".join(
        str(value or "")
        for value in (
            getattr(packet, "title", ""),
            getattr(packet, "goal", ""),
            getattr(packet, "scope", ""),
            *getattr(packet, "success_criteria", []),
            *getattr(packet, "required_evidence", []),
        )
    ).lower()

    if "setup" in packet_type or "setup" in packet_text or "bootstrap" in packet_text:
        allowed = _SETUP_SCOPE_TERMS
        denied = (*_IMPLEMENTATION_SCOPE_TERMS, *_DOCS_SCOPE_TERMS, *_CI_SCOPE_TERMS)
        return _scope_filter(execute_items, allowed=allowed, denied=denied, fallback=[getattr(packet, "goal", "")])
    if "doc" in packet_type or "documentation" in packet_text:
        return _scope_filter(execute_items, allowed=_DOCS_SCOPE_TERMS, denied=(*_SETUP_SCOPE_TERMS, *_CI_SCOPE_TERMS), fallback=[getattr(packet, "goal", "")])
    if "test" in packet_type or "integration" in packet_type or "verification" in packet_type:
        return _scope_filter(execute_items, allowed=_TEST_SCOPE_TERMS, denied=_PUBLISH_PHASE_TERMS, fallback=[getattr(packet, "goal", "")])
    if "ci" in packet_text or "workflow" in packet_text:
        return _scope_filter(execute_items, allowed=_CI_SCOPE_TERMS, denied=_PUBLISH_PHASE_TERMS, fallback=[getattr(packet, "goal", "")])
    if "implementation" in packet_type or "implement" in packet_text:
        return _scope_filter(execute_items, allowed=(*_IMPLEMENTATION_SCOPE_TERMS, *_TEST_SCOPE_TERMS), denied=_PUBLISH_PHASE_TERMS, fallback=[getattr(packet, "goal", "")])
    return execute_items if default_to_packet else execute_items


def packet_scoped_expected_changes(items: list[str], packet: object | None) -> list[str]:
    execute_items = execute_phase_items(items)
    if packet is None:
        return execute_items
    packet_type = str(getattr(getattr(packet, "packet_type", None), "value", getattr(packet, "packet_type", ""))).lower()
    packet_text = " ".join([str(getattr(packet, "title", "")), str(getattr(packet, "goal", "")), str(getattr(packet, "scope", ""))]).lower()
    if "setup" in packet_type or "setup" in packet_text or "bootstrap" in packet_text:
        return _scope_filter(execute_items, allowed=_SETUP_SCOPE_TERMS, denied=(*_IMPLEMENTATION_SCOPE_TERMS, *_DOCS_SCOPE_TERMS, *_CI_SCOPE_TERMS), fallback=[])
    return execute_items


def _scope_filter(items: list[str], *, allowed: tuple[str, ...], denied: tuple[str, ...], fallback: list[str]) -> list[str]:
    scoped: list[str] = []
    for item in items:
        text = str(item).strip()
        lowered = text.lower()
        if not text:
            continue
        if any(term in lowered for term in denied) and not any(term in lowered for term in allowed):
            continue
        if any(term in lowered for term in allowed):
            scoped.append(text)
    if scoped:
        return scoped
    return [str(item).strip() for item in fallback if str(item).strip()]


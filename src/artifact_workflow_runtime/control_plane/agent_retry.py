from __future__ import annotations

from pydantic import Field

from artifact_workflow_runtime.models import OpenHandsRunFailure, RuntimeModel, StageFailureKind, utc_now


AGENT_RETRYABLE_FAILURE_KINDS: frozenset[StageFailureKind] = frozenset(
    {
        StageFailureKind.AGENT_NO_RESULT,
        StageFailureKind.TERMINAL_WITHOUT_ANSWER,
        StageFailureKind.EMPTY_ASSISTANT_ANSWER,
        StageFailureKind.TRANSPORT_ERROR,
        StageFailureKind.API_ERROR,
        StageFailureKind.HTML_TRANSPORT_ERROR,
        StageFailureKind.EVIDENCE_CONTRACT_MISSING,
    }
)

AGENT_RETRYABLE_EVIDENCE_KINDS: frozenset[str] = frozenset(
    {
        "empty_response",
        "agent_no_result",
        "terminal_without_answer",
        "empty_assistant_answer",
        "transport_error",
        "api_error",
        "html_transport_error",
        "evidence_contract_missing",
    }
)


class AgentRetryDecision(RuntimeModel):
    """Control-plane decision for bounded retry of transient agent failures."""

    origin_stage: str
    failure_id: str | None = None
    failure_kind: str | None = None
    evidence_kind: str | None = None
    terminal_state: str | None = None
    retryable_failure: bool = False
    retry_allowed: bool = False
    retry_count: int = 0
    retry_budget: int = 3
    next_retry_count: int = 0
    reason: str
    created_at: str = Field(default_factory=utc_now)


def is_agent_retryable_failure(failure: OpenHandsRunFailure | None) -> bool:
    """True only for agent/transport/no-result failures explicitly marked retryable."""

    if failure is None or not failure.retryable:
        return False
    if failure.failure_kind in AGENT_RETRYABLE_FAILURE_KINDS:
        return True
    return str(failure.evidence_kind or "").strip().lower() in AGENT_RETRYABLE_EVIDENCE_KINDS


class AgentRetryPolicy:
    def __init__(self, *, default_budget: int = 3) -> None:
        self.default_budget = default_budget

    def decide(
        self,
        *,
        origin_stage: str,
        failure: OpenHandsRunFailure | None,
        current_retry_count: int = 0,
        retry_budget: int | None = None,
    ) -> AgentRetryDecision:
        budget = self.default_budget if retry_budget is None else int(retry_budget)
        count = max(0, int(current_retry_count or 0))
        retryable = is_agent_retryable_failure(failure)
        allowed = bool(retryable and count < budget)
        next_count = count + 1 if allowed else count
        if failure is None:
            reason = f"No OpenHands stage failure was present for {origin_stage}; agent retry is not applicable."
        elif not retryable:
            reason = (
                f"OpenHands {origin_stage} failure is not an agent/transport/no-result retryable failure "
                f"(failure_kind={failure.failure_kind.value}, retryable={failure.retryable})."
            )
        elif allowed:
            reason = (
                f"Retryable OpenHands {origin_stage} agent failure detected "
                f"({failure.failure_kind.value}/{failure.evidence_kind}); scheduling control-plane retry "
                f"{next_count}/{budget}."
            )
        else:
            reason = (
                f"Retryable OpenHands {origin_stage} agent failure detected, but retry budget is exhausted "
                f"({count}/{budget}); terminalization is now allowed."
            )
        return AgentRetryDecision(
            origin_stage=origin_stage,
            failure_id=failure.id if failure else None,
            failure_kind=failure.failure_kind.value if failure else None,
            evidence_kind=failure.evidence_kind if failure else None,
            terminal_state=failure.terminal_state if failure else None,
            retryable_failure=retryable,
            retry_allowed=allowed,
            retry_count=count,
            retry_budget=budget,
            next_retry_count=next_count,
            reason=reason,
        )

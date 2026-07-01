from __future__ import annotations

from artifact_workflow_runtime.control_plane.agent_retry import AgentRetryPolicy, is_agent_retryable_failure
from artifact_workflow_runtime.models import OpenHandsRunFailure, StageFailureKind, WorkPacketKind


def _failure(kind: StageFailureKind, *, retryable: bool = True, evidence_kind: str = "empty_response") -> OpenHandsRunFailure:
    return OpenHandsRunFailure(
        stage="execute",
        request_id="exec_req_1",
        work_packet_kind=WorkPacketKind.EXECUTE,
        failure_kind=kind,
        summary="OpenHands did not return usable output.",
        retryable=retryable,
        terminal_state="error",
        evidence_kind=evidence_kind,
    )


def test_retryable_agent_no_result_triggers_bounded_retry() -> None:
    policy = AgentRetryPolicy(default_budget=3)
    decision = policy.decide(origin_stage="execute", failure=_failure(StageFailureKind.AGENT_NO_RESULT), current_retry_count=0)

    assert decision.retryable_failure is True
    assert decision.retry_allowed is True
    assert decision.next_retry_count == 1
    assert decision.retry_budget == 3


def test_retryable_evidence_contract_missing_from_unusable_agent_output_retries() -> None:
    failure = _failure(StageFailureKind.EVIDENCE_CONTRACT_MISSING, evidence_kind="evidence_contract_missing")

    assert is_agent_retryable_failure(failure) is True
    decision = AgentRetryPolicy(default_budget=3).decide(origin_stage="execute", failure=failure, current_retry_count=2)
    assert decision.retry_allowed is True
    assert decision.next_retry_count == 3


def test_retry_budget_exhaustion_terminalizes_after_three_retries() -> None:
    decision = AgentRetryPolicy(default_budget=3).decide(
        origin_stage="execute",
        failure=_failure(StageFailureKind.AGENT_NO_RESULT),
        current_retry_count=3,
    )

    assert decision.retryable_failure is True
    assert decision.retry_allowed is False
    assert decision.next_retry_count == 3
    assert "budget is exhausted" in decision.reason


def test_non_retryable_agent_failure_does_not_enter_retry_loop() -> None:
    failure = _failure(StageFailureKind.AGENT_NO_RESULT, retryable=False)
    decision = AgentRetryPolicy(default_budget=3).decide(origin_stage="execute", failure=failure, current_retry_count=0)

    assert decision.retryable_failure is False
    assert decision.retry_allowed is False
    assert decision.next_retry_count == 0


def test_environment_or_policy_blocker_without_stage_failure_is_not_agent_retry() -> None:
    decision = AgentRetryPolicy(default_budget=3).decide(origin_stage="execute", failure=None, current_retry_count=0)

    assert decision.retryable_failure is False
    assert decision.retry_allowed is False
    assert "not applicable" in decision.reason

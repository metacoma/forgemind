from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from artifact_workflow_runtime.models import BlockerKind

from .models import LifecycleFacts, LifecyclePolicyDecision, PolicyViolation


POLICY_PATH = Path(__file__).with_name("policies") / "runtime.rego"


class OpaPolicyEvaluator:
    """OPA/Rego-backed policy evaluator with explicit fallback mode.

    ``mode="opa_required"`` is the production-safe mode: policy evaluation must
    come from OPA/Rego and the evaluator fails closed if the OPA binary, policy
    bundle, or query result is unavailable.

    ``mode="dev_fallback"`` keeps local tests and offline tarballs runnable by
    using the in-process evaluator when OPA cannot answer. The fallback is
    intentionally explicit so it cannot be mistaken for the canonical policy
    engine.
    """

    OPA_REQUIRED = "opa_required"
    DEV_FALLBACK = "dev_fallback"

    def __init__(self, *, opa_binary: str | None = None, policy_path: Path | None = None, mode: str | None = None) -> None:
        self.mode = mode or os.getenv("AWRT_POLICY_MODE") or self.DEV_FALLBACK
        if self.mode not in {self.OPA_REQUIRED, self.DEV_FALLBACK}:
            raise ValueError(f"Unsupported policy mode: {self.mode!r}")
        self.opa_binary = opa_binary or shutil.which("opa")
        self.policy_path = policy_path or POLICY_PATH

    def evaluate(self, query: str, facts: LifecycleFacts) -> LifecyclePolicyDecision:
        if self.opa_binary and self.policy_path.exists():
            decision = self._evaluate_with_opa(query, facts)
            if decision is not None:
                return decision
        if self.mode == self.DEV_FALLBACK:
            return self._evaluate_fallback(query, facts)
        return self._fail_closed(query, facts)

    def _fail_closed(self, query: str, facts: LifecycleFacts) -> LifecyclePolicyDecision:
        violation = PolicyViolation(
            code="policy_evaluation_unavailable",
            message="OPA policy evaluation is required but unavailable or returned no usable decision.",
            blocker_kind=BlockerKind.POLICY_BLOCKED,
        )
        return LifecyclePolicyDecision(
            allowed=False,
            query=query,
            engine="opa_required",
            reasons=[violation.message],
            violations=[*facts.control_plane_violations, violation],
        )

    def _evaluate_with_opa(self, query: str, facts: LifecycleFacts) -> LifecyclePolicyDecision | None:
        input_data = facts.model_dump(mode="json")
        rego_query = f"data.artifact_workflow_runtime.lifecycle.{query}"
        try:
            proc = subprocess.run(
                [self.opa_binary or "opa", "eval", "--format", "json", "--data", str(self.policy_path), "--input", "-", rego_query],
                input=json.dumps(input_data),
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
        except Exception:
            return None
        if proc.returncode != 0:
            return None
        try:
            payload = json.loads(proc.stdout)
            expressions = payload.get("result", [{}])[0].get("expressions", [])
            value = expressions[0].get("value") if expressions else None
        except Exception:
            return None
        if isinstance(value, bool):
            return LifecyclePolicyDecision(allowed=value, query=query, engine="opa", reasons=[] if value else [f"OPA policy {query} denied transition."])
        if isinstance(value, dict):
            violations = [PolicyViolation.model_validate(item) for item in value.get("violations", [])]
            return LifecyclePolicyDecision(
                allowed=bool(value.get("allowed", False)),
                query=query,
                engine="opa",
                reasons=[str(item) for item in value.get("reasons", [])],
                violations=violations,
            )
        return None

    def _evaluate_fallback(self, query: str, facts: LifecycleFacts) -> LifecyclePolicyDecision:
        violations: list[PolicyViolation] = [*facts.control_plane_violations]

        if query == "can_leave_execute":
            if facts.execution_stage_failed:
                violations.append(PolicyViolation(code="openhands_execute_no_usable_result", message=f"OpenHands execute stage did not return usable operational evidence ({facts.stage_failure_kind or 'unknown'}). Verification must not start from missing producer evidence.", blocker_kind=BlockerKind.EXECUTION_FAILURE))
            if facts.execute_pr_created:
                violations.append(PolicyViolation(code="execute_created_pr", message="OpenHands execute packet created or updated a pull request; publish is only allowed in publish stage.", blocker_kind=BlockerKind.POLICY_BLOCKED))
            if facts.execute_git_push:
                violations.append(PolicyViolation(code="execute_pushed_git", message="OpenHands execute packet pushed git changes; push is only allowed in publish stage.", blocker_kind=BlockerKind.POLICY_BLOCKED))
            if facts.execute_git_commit:
                violations.append(PolicyViolation(code="execute_committed_git", message="OpenHands execute packet committed git changes; commit is only allowed in publish stage.", blocker_kind=BlockerKind.POLICY_BLOCKED))
            if facts.execute_forbidden_action_detected:
                violations.append(PolicyViolation(code="execute_forbidden_action", message="Execution evidence indicates a forbidden action for the execute packet.", blocker_kind=BlockerKind.POLICY_BLOCKED))
            allowed = not violations
            return LifecyclePolicyDecision(allowed=allowed, query=query, reasons=[] if allowed else [item.message for item in violations], violations=violations, engine="dev_fallback")

        if query == "can_publish":
            if facts.environment_blocked:
                violations.append(PolicyViolation(code="publish_blocked_by_environment", message="Publish is forbidden while mandatory verification is blocked by missing environment/runtime prerequisites.", blocker_kind=BlockerKind.MISSING_ENVIRONMENT_DEPENDENCY))
            if facts.mandatory_verification_required and not facts.mandatory_verification_satisfied:
                violations.append(PolicyViolation(code="publish_requires_mandatory_verification", message="Publish is forbidden until mandatory verification obligations are satisfied.", blocker_kind=BlockerKind.MISSING_EVIDENCE))
            if not facts.execution_succeeded:
                violations.append(PolicyViolation(code="publish_requires_successful_execution", message="Publish is forbidden unless execution status is succeeded and blocker-free.", blocker_kind=BlockerKind.EXECUTION_FAILURE))
            allowed = not violations
            return LifecyclePolicyDecision(allowed=allowed, query=query, reasons=[] if allowed else [item.message for item in violations], violations=violations, engine="dev_fallback")

        if query == "can_leave_publish":
            if facts.publish_stage_failed:
                violations.append(PolicyViolation(code="openhands_publish_no_usable_result", message=f"OpenHands publish stage did not return usable operational evidence ({facts.stage_failure_kind or 'unknown'}).", blocker_kind=BlockerKind.EXECUTION_FAILURE))
            if facts.publish_forbidden_action_detected:
                violations.append(PolicyViolation(code="publisher_repaired_or_reimplemented", message="Publish packet appears to have modified source or repaired CI; publish must only commit/push/open PR/collect check evidence.", blocker_kind=BlockerKind.POLICY_BLOCKED))
            allowed = not violations
            return LifecyclePolicyDecision(allowed=allowed, query=query, reasons=[] if allowed else [item.message for item in violations], violations=violations, engine="dev_fallback")

        if query == "can_repair":
            if facts.environment_blocked:
                violations.append(PolicyViolation(code="repair_blocked_by_environment", message="Repair is forbidden while required verification environment is unavailable; human/environment action is required.", blocker_kind=BlockerKind.MISSING_ENVIRONMENT_DEPENDENCY))
            if facts.repair_attempt_count >= facts.max_repair_attempts:
                violations.append(PolicyViolation(code="repair_attempt_limit_reached", message="Repair attempt limit reached; controller must stop automatic repair and finalize non-success.", blocker_kind=BlockerKind.POLICY_BLOCKED))
            if not facts.publish_failed_checks and not facts.publish_has_blockers:
                violations.append(PolicyViolation(code="repair_requires_failed_publish_checks", message="Repair is only allowed after structured publish/check failures or blockers.", blocker_kind=BlockerKind.POLICY_BLOCKED))
            allowed = not violations
            return LifecyclePolicyDecision(allowed=allowed, query=query, reasons=[] if allowed else [item.message for item in violations], violations=violations, engine="dev_fallback")

        if query == "can_reenter":
            if not facts.reentry_required:
                return LifecyclePolicyDecision(allowed=True, query=query, reasons=["No re-entry requested."], engine="dev_fallback")
            if facts.reentry_budget_exhausted:
                violations.append(PolicyViolation(code="pipeline_reentry_budget_exhausted", message="Pipeline-wide re-entry budget is exhausted; controller must finalize non-success instead of looping.", blocker_kind=BlockerKind.POLICY_BLOCKED))
            if facts.reentry_target_stage.value == "continue":
                violations.append(PolicyViolation(code="pipeline_reentry_missing_target", message="Re-entry was requested but no target stage was selected.", blocker_kind=BlockerKind.POLICY_BLOCKED))
            allowed = not violations
            return LifecyclePolicyDecision(allowed=allowed, query=query, reasons=[] if allowed else [item.message for item in violations], violations=violations, engine="dev_fallback")

        if query == "can_finalize_success":
            if facts.acceptance is None or not facts.acceptance.accepted:
                violations.append(PolicyViolation(code="finalize_requires_acceptance", message="Completed finalization is forbidden unless acceptance status is accepted."))
            allowed = not violations
            return LifecyclePolicyDecision(allowed=allowed, query=query, reasons=[] if allowed else [item.message for item in violations], violations=violations, engine="dev_fallback")

        return LifecyclePolicyDecision(allowed=True, query=query, engine="dev_fallback")

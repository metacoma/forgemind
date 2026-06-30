from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from artifact_workflow_runtime.models import BlockerKind

from .models import LifecycleFacts, LifecyclePolicyDecision, PolicyViolation


POLICY_PATH = Path(__file__).with_name("policies") / "runtime.rego"


class OpaPolicyEvaluator:
    """OPA/Rego-backed policy evaluator with a deterministic in-process fallback.

    In production, install the `opa` binary and this evaluator can ask Rego for
    decisions. In tests/dev containers without OPA, it evaluates the same hard
    control-plane invariants in Python so the runtime never silently disables the
    policy gate.
    """

    def __init__(self, *, opa_binary: str | None = None, policy_path: Path | None = None) -> None:
        self.opa_binary = opa_binary or shutil.which("opa")
        self.policy_path = policy_path or POLICY_PATH

    def evaluate(self, query: str, facts: LifecycleFacts) -> LifecyclePolicyDecision:
        if self.opa_binary and self.policy_path.exists():
            decision = self._evaluate_with_opa(query, facts)
            if decision is not None:
                return decision
        return self._evaluate_fallback(query, facts)

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
            if facts.execute_pr_created:
                violations.append(PolicyViolation(code="execute_created_pr", message="OpenHands execute packet created or updated a pull request; publish is only allowed in publish stage.", blocker_kind=BlockerKind.POLICY_BLOCKED))
            if facts.execute_git_push:
                violations.append(PolicyViolation(code="execute_pushed_git", message="OpenHands execute packet pushed git changes; push is only allowed in publish stage.", blocker_kind=BlockerKind.POLICY_BLOCKED))
            if facts.execute_git_commit:
                violations.append(PolicyViolation(code="execute_committed_git", message="OpenHands execute packet committed git changes; commit is only allowed in publish stage.", blocker_kind=BlockerKind.POLICY_BLOCKED))
            if facts.execute_forbidden_action_detected:
                violations.append(PolicyViolation(code="execute_forbidden_action", message="Execution evidence indicates a forbidden action for the execute packet.", blocker_kind=BlockerKind.POLICY_BLOCKED))
            allowed = not violations
            return LifecyclePolicyDecision(allowed=allowed, query=query, reasons=[] if allowed else [item.message for item in violations], violations=violations, engine="fallback")

        if query == "can_publish":
            if facts.environment_blocked:
                violations.append(PolicyViolation(code="publish_blocked_by_environment", message="Publish is forbidden while mandatory verification is blocked by missing environment/runtime prerequisites.", blocker_kind=BlockerKind.MISSING_ENVIRONMENT_DEPENDENCY))
            if facts.mandatory_verification_required and not facts.mandatory_verification_satisfied:
                violations.append(PolicyViolation(code="publish_requires_mandatory_verification", message="Publish is forbidden until mandatory verification obligations are satisfied.", blocker_kind=BlockerKind.MISSING_EVIDENCE))
            if not facts.execution_succeeded:
                violations.append(PolicyViolation(code="publish_requires_successful_execution", message="Publish is forbidden unless execution status is succeeded and blocker-free.", blocker_kind=BlockerKind.EXECUTION_FAILURE))
            allowed = not violations
            return LifecyclePolicyDecision(allowed=allowed, query=query, reasons=[] if allowed else [item.message for item in violations], violations=violations, engine="fallback")

        if query == "can_finalize_success":
            if facts.acceptance is None or not facts.acceptance.accepted:
                violations.append(PolicyViolation(code="finalize_requires_acceptance", message="Completed finalization is forbidden unless acceptance status is accepted."))
            allowed = not violations
            return LifecyclePolicyDecision(allowed=allowed, query=query, reasons=[] if allowed else [item.message for item in violations], violations=violations, engine="fallback")

        return LifecyclePolicyDecision(allowed=True, query=query, engine="fallback")

from __future__ import annotations

from typing import Any

from artifact_workflow_runtime.models import AcceptanceObligationKind, FinalReport

from .models import EvaluationRunReport, PackSummary, ScenarioRunResult, ScenarioScorecard, ScenarioSpec, ScoreComponent


def _extract_evidence_names(report: FinalReport) -> set[str]:
    names: set[str] = set()
    for attr in ("observation", "research", "execution", "publish", "verification"):
        obj = getattr(report, attr, None)
        if obj is None:
            continue
        structured = getattr(obj, "structured_evidence", None)
        if structured is None:
            bundle = getattr(obj, "evidence_bundle", None)
            structured = getattr(bundle, "structured", None)
        if structured is None:
            continue
        if getattr(structured, "commands_run", None):
            names.add("commands_run")
        if getattr(structured, "files_changed", None):
            names.add("files_changed")
        if getattr(structured, "files_observed", None):
            names.add("files_observed")
        if getattr(structured, "tests", None):
            names.add("tests")
        if getattr(structured, "blockers", None):
            names.add("blockers")
        postcheck = getattr(structured, "postcheck_summary", None)
        if postcheck is not None and (getattr(postcheck, "attempted", False) or getattr(postcheck, "summary", "")):
            names.add("postcheck_summary")
    if report.verification is not None:
        if report.verification.pr_checks_passed or report.verification.pr_checks_failed or report.verification.pr_checks_pending:
            names.add("pr_checks")
    return names




_OBLIGATION_ALIASES = {
    "tests_passed": AcceptanceObligationKind.RELEVANT_TESTS_PASSED.value,
    "unit_tests": AcceptanceObligationKind.RELEVANT_TESTS_PASSED.value,
    "integration_tests": AcceptanceObligationKind.INTEGRATION_TESTS_PASSED.value,
    "docs_update": AcceptanceObligationKind.DOCUMENTATION_UPDATED.value,
    "documentation_updated": AcceptanceObligationKind.DOCUMENTATION_UPDATED.value,
    "examples_updated": AcceptanceObligationKind.EXAMPLES_UPDATED.value,
    "ci_or_build_updated": AcceptanceObligationKind.CI_OR_BUILD_UPDATED.value,
    "codegen_or_tooling_updated": AcceptanceObligationKind.CODEGEN_OR_TOOLING_UPDATED.value,
    "required_evidence_present": AcceptanceObligationKind.REQUIRED_EVIDENCE_PRESENT.value,
}


def _canonical_expected_obligation(name: str) -> str:
    key = str(name).strip().lower()
    return _OBLIGATION_ALIASES.get(key, key)

def _extract_obligation_names(report: FinalReport) -> set[str]:
    names: set[str] = set()
    contract = report.acceptance_contract
    if contract is not None:
        for item in contract.obligations:
            names.add(item.name.lower())
            names.add(item.kind.value)
    decision = report.acceptance_decision
    if decision is not None:
        for item in decision.obligation_results:
            names.add(item.obligation_name.lower())
            names.add(item.kind.value)
    obligations = report.obligations
    if obligations is not None:
        if obligations.required_documentation_updates:
            names.add(AcceptanceObligationKind.DOCUMENTATION_UPDATED.value)
        if obligations.required_examples_updates:
            names.add(AcceptanceObligationKind.EXAMPLES_UPDATED.value)
        if obligations.required_ci_updates:
            names.add(AcceptanceObligationKind.CI_OR_BUILD_UPDATED.value)
        if obligations.required_codegen_or_build_updates:
            names.add(AcceptanceObligationKind.CODEGEN_OR_TOOLING_UPDATED.value)
        if obligations.required_test_levels:
            names.add(AcceptanceObligationKind.RELEVANT_TESTS_PASSED.value)
            names.add("tests_passed")
    return names


def score_scenario_run(spec: ScenarioSpec, result: ScenarioRunResult) -> ScenarioScorecard:
    try:
        final_report = FinalReport.model_validate(result.final_report) if result.final_report else None
    except Exception:
        final_report = None
    hard_failures: list[str] = []
    soft_failures: list[str] = []
    notes: list[str] = []
    components: list[ScoreComponent] = []

    # completion
    completion_score = 0
    if result.terminal_status in spec.allowed_terminal_statuses and result.terminal_status not in spec.forbidden_terminal_statuses:
        completion_score = 25
        completion_reason = f"terminal status {result.terminal_status} is allowed"
        completion_passed = True
    else:
        completion_reason = f"terminal status {result.terminal_status} not in allowed set {spec.allowed_terminal_statuses}"
        completion_passed = False
        hard_failures.append(completion_reason)
    components.append(ScoreComponent(name="completion", score=completion_score, max_score=25, passed=completion_passed, reason=completion_reason))

    # acceptance
    obligation_names = _extract_obligation_names(final_report) if final_report is not None else set()
    missing_expected = [item for item in spec.expected_obligations if _canonical_expected_obligation(item) not in obligation_names and item not in obligation_names]
    acceptance_ok = not missing_expected
    acceptance_score = 25 if acceptance_ok else max(0, 25 - 10 * len(missing_expected))
    if not acceptance_ok:
        soft_failures.append(f"missing expected obligations: {', '.join(missing_expected)}")
    components.append(ScoreComponent(name="acceptance", score=acceptance_score, max_score=25, passed=acceptance_ok, reason="all expected obligations present" if acceptance_ok else "; ".join(missing_expected)))

    # evidence
    evidence_names = set(result.required_evidence_found) or (_extract_evidence_names(final_report) if final_report is not None else set())
    result.required_evidence_found = sorted(evidence_names)
    missing_evidence = [item for item in spec.required_evidence if item not in evidence_names]
    evidence_ok = not missing_evidence
    evidence_score = 15 if evidence_ok else max(0, 15 - 5 * len(missing_evidence))
    if not evidence_ok:
        soft_failures.append(f"missing required evidence: {', '.join(missing_evidence)}")
    components.append(ScoreComponent(name="evidence", score=evidence_score, max_score=15, passed=evidence_ok, reason="required evidence present" if evidence_ok else "; ".join(missing_evidence)))

    # loop / reentry
    loop_expectations = spec.expected_reentry_behavior
    loop_ok = True
    loop_reason = "no specific re-entry expectation"
    if loop_expectations:
        if any("must_reenter" in item for item in loop_expectations):
            loop_ok = result.reentry_count > 0
            loop_reason = f"reentry_count={result.reentry_count}"
        elif any("must_not_finalize_if_env_missing" in item for item in loop_expectations):
            loop_ok = result.terminal_status != "completed"
            loop_reason = f"terminal_status={result.terminal_status}"
        elif any("must_not" in item for item in loop_expectations):
            loop_ok = result.reentry_count == 0
            loop_reason = f"reentry_count={result.reentry_count}"
    loop_score = 10 if loop_ok else 0
    if not loop_ok:
        soft_failures.append(f"unexpected loop/reentry behavior: {loop_reason}")
    components.append(ScoreComponent(name="loop", score=loop_score, max_score=10, passed=loop_ok, reason=loop_reason))

    # packets
    missing_packet_patterns = [item for item in spec.expected_packet_patterns if item not in result.packet_types]
    packet_ok = not missing_packet_patterns and result.packet_count >= 1
    packet_score = 10 if packet_ok else (5 if result.packet_count >= 1 else 0)
    if not packet_ok:
        soft_failures.append(f"missing expected packet patterns: {', '.join(missing_packet_patterns)}")
    components.append(ScoreComponent(name="packets", score=packet_score, max_score=10, passed=packet_ok, reason="packet flow matched expectations" if packet_ok else "; ".join(missing_packet_patterns) or "no packets"))

    # policy / safety
    policy_ok = result.terminal_status not in spec.forbidden_terminal_statuses and final_report is not None and final_report.status != "control_plane_violation"
    if any(tag == "blocked_env" for tag in spec.tags):
        policy_ok = policy_ok and result.terminal_status != "completed"
    policy_score = 15 if policy_ok else 0
    if not policy_ok:
        hard_failures.append("policy/safety invariant violated or forbidden terminal status observed")
    components.append(ScoreComponent(name="policy", score=policy_score, max_score=15, passed=policy_ok, reason="policy outcome acceptable" if policy_ok else "forbidden outcome or control plane violation"))

    overall = completion_score + acceptance_score + evidence_score + loop_score + packet_score + policy_score
    passed = not hard_failures and overall >= 70
    return ScenarioScorecard(
        scenario_id=spec.scenario_id,
        overall_score=overall,
        completion_score=completion_score,
        acceptance_score=acceptance_score,
        evidence_score=evidence_score,
        loop_score=loop_score,
        packet_score=packet_score,
        policy_score=policy_score,
        passed=passed,
        hard_failures=hard_failures,
        soft_failures=soft_failures,
        notes=notes,
        components=components,
    )


def summarize_pack(pack_id: str, results: list[ScenarioRunResult]) -> PackSummary:
    scenario_count = len(results)
    passed_count = sum(1 for item in results if item.scorecard and item.scorecard.passed)
    acceptance_pass_count = sum(1 for item in results if item.acceptance_status in {"accepted", "completed", "fully_satisfied"})
    false_success_count = sum(1 for item in results if item.terminal_status == "completed" and item.scorecard and item.scorecard.hard_failures)

    def _avg(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    durations: list[float] = []
    from datetime import datetime
    for item in results:
        if item.finished_at and item.started_at:
            try:
                start = datetime.fromisoformat(item.started_at.replace("Z", "+00:00"))
                end = datetime.fromisoformat(item.finished_at.replace("Z", "+00:00"))
                durations.append(max((end - start).total_seconds(), 0.0))
            except Exception:
                pass

    return PackSummary(
        pack_id=pack_id,
        scenario_count=scenario_count,
        passed_count=passed_count,
        completion_rate=(sum(1 for item in results if item.terminal_status == "completed") / scenario_count) if scenario_count else 0.0,
        acceptance_pass_rate=(acceptance_pass_count / scenario_count) if scenario_count else 0.0,
        false_success_rate=(false_success_count / scenario_count) if scenario_count else 0.0,
        average_loops=_avg([float(item.reentry_count) for item in results]),
        average_packets=_avg([float(item.packet_count) for item in results]),
        average_repairs=_avg([float(item.repair_count) for item in results]),
        average_duration_seconds=_avg(durations),
    )

from __future__ import annotations

from .models import EvaluationRunReport, PackComparison, ScenarioComparison


def compare_reports(before: EvaluationRunReport, after: EvaluationRunReport) -> PackComparison:
    before_map = {item.scenario_id: item for item in before.scenario_results}
    after_map = {item.scenario_id: item for item in after.scenario_results}
    regressions: list[ScenarioComparison] = []
    improvements: list[ScenarioComparison] = []
    for scenario_id in sorted(set(before_map) | set(after_map)):
        prev = before_map.get(scenario_id)
        curr = after_map.get(scenario_id)
        if prev is None or curr is None:
            continue
        prev_score = prev.scorecard.overall_score if prev.scorecard else 0
        curr_score = curr.scorecard.overall_score if curr.scorecard else 0
        delta = curr_score - prev_score
        notes: list[str] = []
        if prev.execution_mode != curr.execution_mode:
            notes.append(f"mode changed: {prev.execution_mode} -> {curr.execution_mode}")
        comparison = ScenarioComparison(
            scenario_id=scenario_id,
            before_status=prev.terminal_status,
            after_status=curr.terminal_status,
            before_score=prev_score,
            after_score=curr_score,
            delta=delta,
            regression=delta < 0 or (prev.terminal_status == "completed" and curr.terminal_status != "completed"),
            improvement=delta > 0 or (prev.terminal_status != "completed" and curr.terminal_status == "completed"),
            before_mode=prev.execution_mode,
            after_mode=curr.execution_mode,
            notes=notes,
        )
        if comparison.regression:
            regressions.append(comparison)
        elif comparison.improvement:
            improvements.append(comparison)
    return PackComparison(
        pack_id=after.pack_id,
        before_summary=before.summary,
        after_summary=after.summary,
        regressions=regressions,
        improvements=improvements,
        overall_delta=after.summary.completion_rate - before.summary.completion_rate,
        before_mode=before.execution_mode,
        after_mode=after.execution_mode,
    )

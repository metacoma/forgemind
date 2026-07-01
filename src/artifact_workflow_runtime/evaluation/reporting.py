from __future__ import annotations

from .models import EvaluationRunReport


def render_markdown_report(report: EvaluationRunReport) -> str:
    lines = [
        f"# Evaluation Report — {report.pack_id}",
        "",
        f"Scenarios: **{report.summary.scenario_count}**",
        f"Passed: **{report.summary.passed_count}**",
        f"Completion rate: **{report.summary.completion_rate:.2%}**",
        f"Acceptance pass rate: **{report.summary.acceptance_pass_rate:.2%}**",
        f"False success rate: **{report.summary.false_success_rate:.2%}**",
        f"Average loops: **{report.summary.average_loops:.2f}**",
        f"Average packets: **{report.summary.average_packets:.2f}**",
        f"Average repairs: **{report.summary.average_repairs:.2f}**",
        "",
        "## Scenario results",
        "",
        "| Scenario | Status | Score | Notes |",
        "| --- | --- | ---: | --- |",
    ]
    for result in report.scenario_results:
        score = result.scorecard.overall_score if result.scorecard is not None else 0
        notes = "; ".join(result.fail_reasons[:2]) if result.fail_reasons else "ok"
        lines.append(f"| {result.scenario_id} | {result.terminal_status} | {score} | {notes} |")
    return "\n".join(lines) + "\n"

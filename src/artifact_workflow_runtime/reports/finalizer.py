from __future__ import annotations

from artifact_workflow_runtime.models import ApprovalRequest, ExecutionPlan, FinalReport, ObservationResult, PolicyDecision, Task, TaskClassification, ExecutionResult, VerificationResult


class FinalReportBuilder:
    def build(
        self,
        *,
        task: Task,
        classification: TaskClassification | None,
        plan: ExecutionPlan | None,
        policy: PolicyDecision | None,
        approval: ApprovalRequest | None,
        observation: ObservationResult | None,
        execution: ExecutionResult | None,
        verification: VerificationResult | None,
        artifact_ids: list[str],
    ) -> FinalReport:
        if approval and approval.required and approval.approved is False:
            status = "blocked"
            summary = "Execution was blocked because approval was denied."
        elif verification and verification.passed:
            status = "completed"
            summary = verification.summary or "Execution completed and verification passed."
        elif execution and execution.ok:
            status = "executed_unverified"
            summary = execution.summary or "Execution completed but verification did not pass."
        else:
            status = "planned_only"
            summary = "Workflow completed planning stages without execution."
        return FinalReport(
            task_id=task.id,
            status=status,
            summary=summary,
            classification=classification,
            plan=plan,
            policy=policy,
            approval=approval,
            observation=observation,
            execution=execution,
            verification=verification,
            artifact_ids=artifact_ids,
        )

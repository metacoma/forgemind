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
        elif policy and policy.blocked:
            status = "blocked"
            summary = "; ".join(policy.reasons) or "Workflow was blocked by policy."
        elif observation and not observation.ok and observation.transport_error:
            status = "observation_failed"
            summary = observation.summary or "Observation failed before planning due to unusable evidence."
        elif execution and not execution.ok:
            status = "execution_failed"
            summary = execution.summary or "Execution did not produce usable evidence."
        elif verification and verification.passed:
            status = "completed"
            summary = verification.summary or "Execution completed and verification passed."
        elif verification and not verification.passed:
            status = "executed_unverified"
            summary = verification.summary or "Execution completed but evidence-backed verification did not pass."
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

from __future__ import annotations

from artifact_workflow_runtime.models import (
    AcceptanceDecision,
    ApprovalRequest,
    ExecutionPlan,
    ExecutionResult,
    FinalReport,
    ObservationResult,
    PolicyDecision,
    PublishResult,
    RepairResult,
    RoutingDecision,
    ObligationAnalysis,
    Task,
    TaskAcceptanceContract,
    TaskClassification,
    VerificationResult,
)


class FinalReportBuilder:
    def build(
        self,
        *,
        task: Task,
        classification: TaskClassification | None,
        route: RoutingDecision | None,
        obligations: ObligationAnalysis | None,
        done_contract: dict | None,
        environment_plan: dict | None,
        plan: ExecutionPlan | None,
        policy: PolicyDecision | None,
        approval: ApprovalRequest | None,
        research: ObservationResult | None,
        observation: ObservationResult | None,
        execution: ExecutionResult | None,
        publish: PublishResult | None,
        repair_results: list[RepairResult] | None = None,
        verification: VerificationResult | None = None,
        qa_plan: dict | None = None,
        qa_execution_report: dict | None = None,
        qa_review_result: dict | None = None,
        acceptance_contract: TaskAcceptanceContract | None,
        acceptance_decision: AcceptanceDecision | None,
        artifact_ids: list[str],
    ) -> FinalReport:
        if approval and approval.required and approval.approved is False:
            status = "blocked"
            summary = "Execution was blocked because approval was denied."
        elif policy and policy.blocked:
            status = "blocked"
            summary = "; ".join(policy.reasons) or "Workflow was blocked by policy."
        elif research and not research.ok and research.stage_failure is not None:
            status = "research_failed"
            summary = research.summary or "Fresh external research failed before planning due to unusable evidence."
        elif observation and not observation.ok and observation.stage_failure is not None:
            status = "observation_failed"
            summary = observation.summary or "Observation failed before planning due to unusable evidence."
        elif acceptance_decision:
            status = acceptance_decision.final_workflow_status
            summary = acceptance_decision.summary
        elif execution and not execution.ok and execution.stage_failure is not None:
            status = "agent_failed"
            summary = execution.summary or "Execution did not produce usable operational evidence."
        elif execution and not execution.ok:
            status = "execution_failed"
            summary = execution.summary or "Execution did not produce usable evidence."
        elif publish and not publish.ok and publish.stage_failure is not None:
            status = "publish_failed"
            summary = publish.summary or "Publish step did not produce usable operational evidence."
        elif publish and not publish.ok:
            status = "publish_failed"
            summary = publish.summary or "Publish step did not produce usable evidence."
        elif verification:
            status = "needs_human_review" if plan and (plan.requires_mutation or plan.must_change_world) else (verification.completion_status if verification.completion_status else ("completed" if verification.passed else "executed_unverified"))
            summary = verification.summary or "Verification completed, but no acceptance decision was recorded."
        elif execution and execution.ok:
            status = "implemented_only"
            summary = execution.summary or "Execution completed but verification did not run."
        else:
            status = "planned_only"
            summary = "Workflow completed planning stages without execution."
        return FinalReport(
            task_id=task.id,
            status=status,
            summary=summary,
            acceptance_contract=acceptance_contract,
            acceptance_decision=acceptance_decision,
            classification=classification,
            route=route,
            obligations=obligations,
            done_contract=done_contract,
            environment_plan=environment_plan,
            plan=plan,
            policy=policy,
            approval=approval,
            research=research,
            observation=observation,
            execution=execution,
            publish=publish,
            repair_results=repair_results or [],
            verification=verification,
            qa_plan=qa_plan,
            qa_execution_report=qa_execution_report,
            qa_review_result=qa_review_result,
            artifact_ids=artifact_ids,
        )

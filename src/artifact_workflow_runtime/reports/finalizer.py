from __future__ import annotations

from artifact_workflow_runtime.decomposition.models import DecompositionPlan, DecompositionProgressDecision, ExecutionPacketStatus
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
        plan: ExecutionPlan | None,
        policy: PolicyDecision | None,
        approval: ApprovalRequest | None,
        research: ObservationResult | None,
        observation: ObservationResult | None,
        execution: ExecutionResult | None,
        publish: PublishResult | None,
        repair_results: list[RepairResult] | None = None,
        verification: VerificationResult | None = None,
        acceptance_contract: TaskAcceptanceContract | None,
        acceptance_decision: AcceptanceDecision | None,
        decomposition_plan: DecompositionPlan | None = None,
        packet_progression: DecompositionProgressDecision | None = None,
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
        elif packet_progression is not None and packet_progression.blocked:
            status = "blocked"
            summary = packet_progression.reason
        elif decomposition_plan is not None and any(packet.status in {ExecutionPacketStatus.BLOCKED, ExecutionPacketStatus.FAILED} for packet in decomposition_plan.packets):
            status = "blocked"
            blocked_packets = [packet.packet_id for packet in decomposition_plan.packets if packet.status in {ExecutionPacketStatus.BLOCKED, ExecutionPacketStatus.FAILED}]
            summary = "Decomposition plan did not complete because blocked/failed packets remain: " + ", ".join(blocked_packets)
        elif decomposition_plan is not None and decomposition_plan.packets and any(packet.status not in {ExecutionPacketStatus.COMPLETED, ExecutionPacketStatus.SKIPPED} for packet in decomposition_plan.packets):
            status = "partially_completed"
            summary = "Decomposition plan has unfinished packets; workflow stopped before full verification/acceptance."
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
            plan=plan,
            policy=policy,
            approval=approval,
            research=research,
            observation=observation,
            execution=execution,
            publish=publish,
            repair_results=repair_results or [],
            verification=verification,
            artifact_ids=artifact_ids,
        )

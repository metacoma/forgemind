from __future__ import annotations

from typing import Any, Mapping

from artifact_workflow_runtime.models import BlockerKind, ExecutionResult
from artifact_workflow_runtime.strategy import StrategyId

from .models import (
    DecompositionOutcome,
    DecompositionPlan,
    DecompositionProgressDecision,
    ExecutionPacket,
    ExecutionPacketStatus,
    ExecutionPacketType,
    PacketHistoryEntry,
)


def planner_for(services: Any):
    from .planner import DecompositionPlanner

    return getattr(services, "decomposition_planner", None) or DecompositionPlanner()


def selector_for(services: Any):
    from .selector import PacketSelector

    return getattr(services, "packet_selector", None) or PacketSelector()


def update_packet_status(
    plan: DecompositionPlan,
    *,
    packet_id: str,
    new_status: ExecutionPacketStatus,
    reason: str,
    stage: str,
    execution_result_id: str | None = None,
) -> tuple[DecompositionPlan, PacketHistoryEntry]:
    packets: list[ExecutionPacket] = []
    previous = None
    for packet in plan.packets:
        if packet.packet_id == packet_id:
            previous = packet.status
            metadata = dict(packet.metadata or {})
            if previous == ExecutionPacketStatus.FAILED and new_status == ExecutionPacketStatus.COMPLETED and execution_result_id:
                metadata["superseded_by_execution_result_id"] = execution_result_id
            packets.append(packet.model_copy(update={"status": new_status, "metadata": metadata}))
        else:
            packets.append(packet)
    if previous is None:
        raise KeyError(f"Unknown packet id: {packet_id}")
    history = PacketHistoryEntry(
        packet_id=packet_id,
        previous_status=previous,
        new_status=new_status,
        reason=reason,
        stage=stage,
        execution_result_id=execution_result_id,
    )
    return plan.model_copy(update={"packets": packets, "updated_at": history.created_at}), history


def packet_prompt_block(packet: ExecutionPacket | None) -> str:
    if packet is None:
        return "No bounded execution packet was selected. Fall back to the minimal bounded interpretation of the approved plan."
    lines = [
        "ExecutionPacket:",
        f"- id: {packet.packet_id}",
        f"- type: {packet.packet_type.value}",
        f"- title: {packet.title}",
        f"- goal: {packet.goal}",
        f"- scope: {packet.scope}",
    ]
    local_contract = getattr(packet, "local_contract", None)
    if packet.allowed_files:
        lines.append(f"- allowed_files: {packet.allowed_files}")
    if packet.target_areas:
        lines.append(f"- target_areas: {packet.target_areas}")
    if local_contract is not None:
        if getattr(local_contract, "environment_nodes", None):
            lines.append(f"- environment_nodes: {local_contract.environment_nodes}")
        if getattr(local_contract, "work_surfaces", None):
            lines.append(f"- work_surfaces: {local_contract.work_surfaces}")
        if getattr(local_contract, "verification_levels", None):
            lines.append(f"- verification_levels: {local_contract.verification_levels}")
        if getattr(local_contract, "publish_requirements", None):
            lines.append(f"- publish_requirements: {local_contract.publish_requirements}")
    if packet.forbidden_actions:
        lines.append(f"- forbidden_actions: {packet.forbidden_actions}")
    if packet.success_criteria:
        lines.append("Packet success criteria:")
        lines.extend(f"  - {item}" for item in packet.success_criteria)
    if packet.required_evidence:
        lines.append("Packet required evidence:")
        lines.extend(f"  - {item}" for item in packet.required_evidence)
    return "\n".join(lines)


def packet_metadata(packet: ExecutionPacket | None) -> dict[str, Any]:
    if packet is None:
        return {}
    return {
        "active_packet_id": packet.packet_id,
        "active_packet_type": packet.packet_type.value,
        "active_packet_strategy": packet.strategy_id,
    }


def packet_from_state(state: Mapping[str, Any], plan: DecompositionPlan) -> ExecutionPacket | None:
    packet_id = state.get("active_packet_id")
    if packet_id is None:
        return None
    for packet in plan.packets:
        if packet.packet_id == packet_id:
            return packet
    return None


def status_from_execution_result(result: ExecutionResult) -> ExecutionPacketStatus:
    status_text = str(result.execution_status.value if hasattr(result.execution_status, "value") else result.execution_status).lower()
    blockers = list(result.structured_evidence.blockers)
    env_blocked = any(
        getattr(item, "blocker_kind", None) in {
            BlockerKind.MISSING_ENVIRONMENT_DEPENDENCY,
            BlockerKind.MISSING_RUNTIME_PREREQUISITE,
            BlockerKind.INTEGRATION_ENVIRONMENT_UNAVAILABLE,
        }
        for item in blockers
    )
    non_deferred_blockers = [item for item in blockers if not _is_deferred_publish_blocker(item)]
    if result.ok and status_text in {"succeeded", "partial"} and not non_deferred_blockers:
        return ExecutionPacketStatus.COMPLETED
    if status_text == "blocked" or env_blocked:
        return ExecutionPacketStatus.BLOCKED
    return ExecutionPacketStatus.FAILED


def _is_deferred_publish_blocker(blocker) -> bool:
    kind = getattr(getattr(blocker, "blocker_kind", None), "value", getattr(blocker, "blocker_kind", None))
    summary = str(getattr(blocker, "summary", "")).lower()
    if str(kind) != BlockerKind.POLICY_BLOCKED.value:
        return False
    publish_terms = ("git commit", "push", "pr", "publish")
    return ("forbidden execute actions" in summary) or any(term in summary for term in publish_terms)


def plan_completed(plan: DecompositionPlan) -> bool:
    return bool(plan.packets) and all(packet.status in {ExecutionPacketStatus.COMPLETED, ExecutionPacketStatus.SKIPPED} for packet in plan.packets)


def runnable_packets_remaining(plan: DecompositionPlan) -> bool:
    from .selector import PacketSelector

    selection = PacketSelector().select(plan=plan, active_strategy=plan.strategy_id)
    return bool(selection.ready and selection.selected_packet_id)


def progression_decision(
    plan: DecompositionPlan,
    *,
    active_strategy: StrategyId | str | None = None,
    current_packet_id: str | None = None,
) -> DecompositionProgressDecision:
    from .selector import PacketSelector

    strategy = StrategyId.coerce(active_strategy or plan.strategy_id or StrategyId.DEFAULT)
    if plan_completed(plan):
        return DecompositionProgressDecision(
            outcome=DecompositionOutcome.DECOMPOSITION_COMPLETED,
            current_packet_id=current_packet_id,
            selected_next_packet_id=None,
            selected_next_stage="qa_plan",
            plan_completed=True,
            terminal=False,
            blocked=False,
            reason="All execution packets are completed or skipped; runtime may continue to QA/verification.",
        )

    selection = PacketSelector().select(plan=plan, active_strategy=strategy)
    selected_packet = _packet_by_id(plan, selection.selected_packet_id) if selection.selected_packet_id else None
    if selection.ready and selection.selected_packet_id:
        outcome = (
            DecompositionOutcome.REPAIR_REQUIRED
            if strategy == StrategyId.REPAIR_ONLY or (selected_packet is not None and selected_packet.packet_type == ExecutionPacketType.REPAIR)
            else DecompositionOutcome.RUNNABLE_PACKET
        )
        return DecompositionProgressDecision(
            outcome=outcome,
            current_packet_id=current_packet_id,
            selected_next_packet_id=selection.selected_packet_id,
            selected_next_stage="execute",
            plan_completed=False,
            terminal=False,
            blocked=False,
            repair_required=outcome == DecompositionOutcome.REPAIR_REQUIRED,
            reason=selection.reason,
        )

    failed_packets = [packet.packet_id for packet in plan.packets if packet.status == ExecutionPacketStatus.FAILED]
    blocked_packets = [packet.packet_id for packet in plan.packets if packet.status == ExecutionPacketStatus.BLOCKED]
    pending_packets = [packet.packet_id for packet in plan.packets if packet.status == ExecutionPacketStatus.PENDING]

    if failed_packets:
        reason = f"Decomposition plan cannot continue because failed packets remain unresolved: {', '.join(failed_packets)}."
        return DecompositionProgressDecision(
            outcome=DecompositionOutcome.FAILED_TERMINAL,
            current_packet_id=current_packet_id,
            selected_next_packet_id=None,
            selected_next_stage="finalize",
            plan_completed=False,
            terminal=True,
            blocked=False,
            failed=True,
            final_status_hint="failed",
            blocked_reason="failed_packets_present",
            reason=reason,
        )

    if blocked_packets:
        blocked_packet_models = [packet for packet in plan.packets if packet.status == ExecutionPacketStatus.BLOCKED]
        blocked_implementation = any(packet.packet_type == ExecutionPacketType.IMPLEMENTATION for packet in blocked_packet_models)
        if blocked_implementation:
            reason = f"Decomposition packet is blocked by runtime/environment evidence; route to verification/acceptance instead of re-executing blindly: {', '.join(blocked_packets)}."
            return DecompositionProgressDecision(
                outcome=DecompositionOutcome.RUNTIME_PROOF_BLOCKED,
                current_packet_id=current_packet_id,
                selected_next_packet_id=None,
                selected_next_stage="verify",
                plan_completed=False,
                terminal=False,
                blocked=True,
                final_status_hint="needs_environment",
                blocked_reason="runtime_proof_blocked",
                reason=reason,
            )
        environment_like = bool(blocked_packet_models)
        outcome = DecompositionOutcome.NEEDS_ENVIRONMENT if environment_like else DecompositionOutcome.BLOCKED_TERMINAL
        blocked_reason = "needs_environment" if environment_like else "blocked_packets_present"
        final_status_hint = "needs_environment" if environment_like else "blocked"
        reason = f"Decomposition plan is blocked because blocked packets remain unresolved: {', '.join(blocked_packets)}."
        return DecompositionProgressDecision(
            outcome=outcome,
            current_packet_id=current_packet_id,
            selected_next_packet_id=None,
            selected_next_stage="finalize",
            plan_completed=False,
            terminal=True,
            blocked=True,
            final_status_hint=final_status_hint,
            blocked_reason=blocked_reason,
            reason=reason,
        )

    if pending_packets:
        reason = selection.reason or "Decomposition plan still has pending packets but none are runnable."
        return DecompositionProgressDecision(
            outcome=DecompositionOutcome.MANUAL_INTERVENTION_REQUIRED,
            current_packet_id=current_packet_id,
            selected_next_packet_id=None,
            selected_next_stage="finalize",
            plan_completed=False,
            terminal=True,
            blocked=True,
            manual_intervention_required=True,
            final_status_hint="blocked",
            blocked_reason=selection.blocked_reason or "no_runnable_packets",
            pending_dependencies=list(selection.pending_dependencies),
            reason=reason,
        )

    return DecompositionProgressDecision(
        outcome=DecompositionOutcome.BLOCKED_TERMINAL,
        current_packet_id=current_packet_id,
        selected_next_packet_id=None,
        selected_next_stage="finalize",
        plan_completed=False,
        terminal=True,
        blocked=True,
        final_status_hint="blocked",
        blocked_reason=selection.blocked_reason or "no_runnable_packets",
        pending_dependencies=list(selection.pending_dependencies),
        reason="Decomposition plan is not complete and no runnable packet could be selected.",
    )


def _packet_by_id(plan: DecompositionPlan, packet_id: str | None) -> ExecutionPacket | None:
    if packet_id is None:
        return None
    for packet in plan.packets:
        if packet.packet_id == packet_id:
            return packet
    return None

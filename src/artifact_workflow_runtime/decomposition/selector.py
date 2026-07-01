from __future__ import annotations

from artifact_workflow_runtime.strategy import StrategyId

from .models import DecompositionPlan, ExecutionPacketStatus, ExecutionPacketType, PacketSelection


class PacketSelector:
    def select(self, *, plan: DecompositionPlan, active_strategy: StrategyId | str | None = None) -> PacketSelection:
        strategy = StrategyId.coerce(active_strategy or plan.strategy_id or StrategyId.DEFAULT)
        packets = list(plan.packets)

        if strategy == StrategyId.REPAIR_ONLY:
            for packet in packets:
                if packet.packet_type == ExecutionPacketType.REPAIR and packet.status == ExecutionPacketStatus.PENDING:
                    if _deps_ready(packet, packets):
                        return PacketSelection(selected_packet_id=packet.packet_id, ready=True, reason="repair_only selected explicit repair packet")
                    return PacketSelection(selected_packet_id=packet.packet_id, ready=False, reason="repair packet blocked on dependencies", blocked_reason="dependencies_not_ready", pending_dependencies=_pending_deps(packet, packets))
            for packet in packets:
                if packet.status == ExecutionPacketStatus.FAILED:
                    return PacketSelection(selected_packet_id=packet.packet_id, ready=True, reason="repair_only selected failed packet for repair")

        pending = [packet for packet in packets if packet.status == ExecutionPacketStatus.PENDING]
        if not pending:
            return PacketSelection(selected_packet_id=None, ready=False, reason="all packets completed or skipped")

        first = pending[0]
        if not _deps_ready(first, packets):
            return PacketSelection(
                selected_packet_id=first.packet_id,
                ready=False,
                reason="pending_packet_blocked_by_dependencies",
                blocked_reason="dependencies_not_ready",
                pending_dependencies=_pending_deps(first, packets),
            )
        return PacketSelection(selected_packet_id=first.packet_id, ready=True, reason="next_dependency_satisfied_packet_selected")


def _deps_ready(packet, packets) -> bool:
    status_map = {item.packet_id: item.status for item in packets}
    return all(status_map.get(dep) in {ExecutionPacketStatus.COMPLETED, ExecutionPacketStatus.SKIPPED} for dep in packet.dependencies)


def _pending_deps(packet, packets) -> list[str]:
    status_map = {item.packet_id: item.status for item in packets}
    return [dep for dep in packet.dependencies if status_map.get(dep) not in {ExecutionPacketStatus.COMPLETED, ExecutionPacketStatus.SKIPPED}]

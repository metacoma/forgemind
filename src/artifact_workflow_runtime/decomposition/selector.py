from __future__ import annotations

from artifact_workflow_runtime.strategy import StrategyId

from .models import DecompositionPlan, ExecutionPacket, ExecutionPacketStatus, ExecutionPacketType, PacketSelection

_PACKET_PRIORITY = {
    ExecutionPacketType.SETUP: 0,
    ExecutionPacketType.REPAIR: 1,
    ExecutionPacketType.IMPLEMENTATION: 2,
    ExecutionPacketType.INTEGRATION: 3,
    ExecutionPacketType.TEST: 4,
    ExecutionPacketType.DOCS: 5,
    ExecutionPacketType.PUBLISH_PREPARATION: 6,
    ExecutionPacketType.VERIFICATION: 7,
    ExecutionPacketType.SPIKE: 8,
    ExecutionPacketType.REFACTOR: 9,
}


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

        ready_packets = [packet for packet in pending if _deps_ready(packet, packets)]
        if ready_packets:
            selected = sorted(ready_packets, key=_packet_priority_key)[0]
            return PacketSelection(selected_packet_id=selected.packet_id, ready=True, reason=f"selected highest-priority ready packet ({selected.packet_type.value}) from typed dependency ordering")

        blocked_pending = sorted(pending, key=_packet_priority_key)
        first = blocked_pending[0]
        return PacketSelection(
            selected_packet_id=first.packet_id,
            ready=False,
            reason=f"highest-priority pending packet ({first.packet_type.value}) is blocked on dependencies",
            blocked_reason="dependencies_not_ready",
            pending_dependencies=_pending_deps(first, packets),
        )


def _packet_priority_key(packet: ExecutionPacket) -> tuple[int, int, str]:
    return (_PACKET_PRIORITY.get(packet.packet_type, 99), len(packet.dependencies), packet.packet_id)


def _deps_ready(packet, packets) -> bool:
    status_map = {item.packet_id: item.status for item in packets}
    return all(status_map.get(dep) in {ExecutionPacketStatus.COMPLETED, ExecutionPacketStatus.SKIPPED} for dep in packet.dependencies)


def _pending_deps(packet, packets) -> list[str]:
    status_map = {item.packet_id: item.status for item in packets}
    return [dep for dep in packet.dependencies if status_map.get(dep) not in {ExecutionPacketStatus.COMPLETED, ExecutionPacketStatus.SKIPPED}]

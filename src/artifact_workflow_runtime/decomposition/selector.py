from __future__ import annotations

from artifact_workflow_runtime.strategy import StrategyId

from .models import DecompositionPlan, ExecutionPacket, ExecutionPacketStatus, ExecutionPacketType, PacketSelection

_PACKET_PRIORITY: dict[ExecutionPacketType, int] = {
    ExecutionPacketType.SETUP: 0,
    ExecutionPacketType.REPAIR: 1,
    ExecutionPacketType.IMPLEMENTATION: 2,
    ExecutionPacketType.SPIKE: 3,
    ExecutionPacketType.REFACTOR: 3,
    ExecutionPacketType.INTEGRATION: 4,
    ExecutionPacketType.TEST: 5,
    ExecutionPacketType.DOCS: 6,
    ExecutionPacketType.PUBLISH_PREPARATION: 7,
    ExecutionPacketType.VERIFICATION: 8,
}


class PacketSelector:
    def select(self, *, plan: DecompositionPlan, active_strategy: StrategyId | str | None = None) -> PacketSelection:
        strategy = StrategyId.coerce(active_strategy or plan.strategy_id or StrategyId.DEFAULT)
        packets = list(plan.packets)

        if strategy == StrategyId.REPAIR_ONLY:
            for packet in _ready_packets(packets, preferred_types={ExecutionPacketType.REPAIR}):
                return PacketSelection(selected_packet_id=packet.packet_id, ready=True, reason="repair_only selected explicit repair packet")
            for packet in packets:
                if packet.status == ExecutionPacketStatus.FAILED:
                    return PacketSelection(selected_packet_id=packet.packet_id, ready=True, reason="repair_only selected failed packet for repair")

        ready_packets = _ready_packets(packets)
        if ready_packets:
            selected = ready_packets[0]
            return PacketSelection(selected_packet_id=selected.packet_id, ready=True, reason=f"dependency-ready packet selected by priority ({selected.packet_type.value})")

        pending = [packet for packet in packets if packet.status == ExecutionPacketStatus.PENDING]
        if not pending:
            return PacketSelection(selected_packet_id=None, ready=False, reason="all packets completed or skipped")

        blocked = sorted(pending, key=_packet_sort_key)[0]
        return PacketSelection(
            selected_packet_id=blocked.packet_id,
            ready=False,
            reason="no dependency-ready packet available; highest-priority pending packet is blocked on dependencies",
            blocked_reason="dependencies_not_ready",
            pending_dependencies=_pending_deps(blocked, packets),
        )


def _ready_packets(packets: list[ExecutionPacket], preferred_types: set[ExecutionPacketType] | None = None) -> list[ExecutionPacket]:
    ready = [packet for packet in packets if packet.status == ExecutionPacketStatus.PENDING and _deps_ready(packet, packets)]
    if preferred_types is not None:
        ready = [packet for packet in ready if packet.packet_type in preferred_types]
    return sorted(ready, key=_packet_sort_key)


def _packet_sort_key(packet: ExecutionPacket) -> tuple[int, int, str]:
    packet_index = int(packet.metadata.get("packet_index", 9999)) if isinstance(packet.metadata, dict) else 9999
    return (_PACKET_PRIORITY.get(packet.packet_type, 999), packet_index, packet.packet_id)


def _deps_ready(packet: ExecutionPacket, packets: list[ExecutionPacket]) -> bool:
    status_map = {item.packet_id: item.status for item in packets}
    return all(status_map.get(dep) in {ExecutionPacketStatus.COMPLETED, ExecutionPacketStatus.SKIPPED} for dep in packet.dependencies)


def _pending_deps(packet: ExecutionPacket, packets: list[ExecutionPacket]) -> list[str]:
    status_map = {item.packet_id: item.status for item in packets}
    return [dep for dep in packet.dependencies if status_map.get(dep) not in {ExecutionPacketStatus.COMPLETED, ExecutionPacketStatus.SKIPPED}]

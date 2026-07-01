from __future__ import annotations

from artifact_workflow_runtime.strategy import StrategyId

from .models import DecompositionPlan, DecompositionValidationResult, ExecutionPacket, ExecutionPacketStatus, ExecutionPacketType


class DecompositionValidator:
    def validate(
        self,
        plan: DecompositionPlan,
        *,
        fallback_to_single_packet: bool = True,
    ) -> DecompositionValidationResult:
        issues: list[str] = []
        packet_ids = [packet.packet_id for packet in plan.packets]
        if len(packet_ids) != len(set(packet_ids)):
            issues.append("duplicate packet_id")
        known = set(packet_ids)
        for packet in plan.packets:
            if not packet.title.strip():
                issues.append(f"packet {packet.packet_id} missing title")
            if not packet.goal.strip():
                issues.append(f"packet {packet.packet_id} missing goal")
            if not packet.scope.strip():
                issues.append(f"packet {packet.packet_id} missing scope")
            if not packet.success_criteria:
                issues.append(f"packet {packet.packet_id} missing success criteria")
            if not packet.required_evidence:
                issues.append(f"packet {packet.packet_id} missing required evidence")
            if len(packet.dependencies) != len(set(packet.dependencies)):
                issues.append(f"packet {packet.packet_id} has duplicate dependencies")
            if packet.packet_id in packet.dependencies:
                issues.append(f"packet {packet.packet_id} depends on itself")
            for dep in packet.dependencies:
                if dep not in known:
                    issues.append(f"packet {packet.packet_id} depends on unknown packet {dep}")
            if packet.packet_type == ExecutionPacketType.REPAIR and "do not expand scope" not in [action.strip().lower() for action in packet.forbidden_actions]:
                issues.append(f"repair packet {packet.packet_id} must forbid scope expansion")
        if self._has_cycle(plan):
            issues.append("dependency cycle detected")
        issues.extend(self._status_graph_issues(plan))
        if (plan.strategy_id or "").strip() == StrategyId.REPAIR_ONLY.value:
            unrelated = [
                packet.packet_id
                for packet in plan.packets
                if packet.packet_type not in {ExecutionPacketType.REPAIR, ExecutionPacketType.VERIFICATION}
            ]
            if unrelated:
                issues.append("repair_only plan contains unrelated expansion packets")
        if not issues:
            return DecompositionValidationResult(valid=True, normalized_plan=plan)
        if fallback_to_single_packet and plan.packets:
            fallback_packet = self._single_packet_from(plan.packets[0], strategy_id=plan.strategy_id)
            fallback_plan = plan.model_copy(update={"packets": [fallback_packet], "updated_at": fallback_packet.updated_at})
            return DecompositionValidationResult(valid=False, issues=issues, fallback_used=True, normalized_plan=fallback_plan)
        return DecompositionValidationResult(valid=False, issues=issues)

    def _single_packet_from(self, packet: ExecutionPacket, *, strategy_id: str | None) -> ExecutionPacket:
        return packet.model_copy(
            update={
                "dependencies": [],
                "strategy_id": strategy_id,
                "status": ExecutionPacketStatus.PENDING,
                "forbidden_actions": _dedupe(packet.forbidden_actions + ["do not expand scope"]),
            }
        )

    def _has_cycle(self, plan: DecompositionPlan) -> bool:
        graph = {packet.packet_id: list(packet.dependencies) for packet in plan.packets}
        temp: set[str] = set()
        perm: set[str] = set()

        def visit(node: str) -> bool:
            if node in perm:
                return False
            if node in temp:
                return True
            temp.add(node)
            for dep in graph.get(node, []):
                if visit(dep):
                    return True
            temp.remove(node)
            perm.add(node)
            return False

        return any(visit(node) for node in graph)

    def _status_graph_issues(self, plan: DecompositionPlan) -> list[str]:
        issues: list[str] = []
        packets_by_id = {packet.packet_id: packet for packet in plan.packets}
        for packet in plan.packets:
            dependency_statuses = [packets_by_id[dep].status for dep in packet.dependencies if dep in packets_by_id]
            if packet.status in {ExecutionPacketStatus.COMPLETED, ExecutionPacketStatus.SKIPPED}:
                unresolved = [dep for dep in packet.dependencies if packets_by_id[dep].status not in {ExecutionPacketStatus.COMPLETED, ExecutionPacketStatus.SKIPPED}]
                if unresolved:
                    issues.append(f"packet {packet.packet_id} is terminal but depends on unresolved packets: {', '.join(unresolved)}")
            if packet.status == ExecutionPacketStatus.PENDING and any(status == ExecutionPacketStatus.FAILED for status in dependency_statuses):
                issues.append(f"packet {packet.packet_id} is pending behind failed dependency")
            if packet.status == ExecutionPacketStatus.PENDING and any(status == ExecutionPacketStatus.BLOCKED for status in dependency_statuses):
                issues.append(f"packet {packet.packet_id} is pending behind blocked dependency")
        unfinished = [packet for packet in plan.packets if packet.status not in {ExecutionPacketStatus.COMPLETED, ExecutionPacketStatus.SKIPPED}]
        if not unfinished and plan.packets:
            terminal_packets = [packet for packet in plan.packets if packet.status in {ExecutionPacketStatus.BLOCKED, ExecutionPacketStatus.FAILED}]
            if terminal_packets:
                issues.append("plan has no unfinished packets but still contains blocked/failed packet statuses")
        return issues


def _dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        text = str(item).strip().lower()
        if text and text not in out:
            out.append(text)
    return out

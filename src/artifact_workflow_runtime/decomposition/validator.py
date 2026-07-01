from __future__ import annotations

from artifact_workflow_runtime.strategy import StrategyId

from .models import (
    DecompositionPlan,
    DecompositionValidationResult,
    ExecutionPacket,
    ExecutionPacketType,
)


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
            if packet.packet_type == ExecutionPacketType.REPAIR and not any(
                action.strip().lower() == "do not expand scope" for action in packet.forbidden_actions
            ):
                issues.append(f"repair packet {packet.packet_id} must forbid scope expansion")
        if self._has_cycle(plan):
            issues.append("dependency cycle detected")
        if (plan.strategy_id or "").strip() == StrategyId.REPAIR_ONLY.value:
            unrelated = [packet.packet_id for packet in plan.packets if packet.packet_type not in {ExecutionPacketType.REPAIR, ExecutionPacketType.VERIFICATION}]
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


def _dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING, Iterable

from artifact_workflow_runtime.models import AcceptanceObligationKind, DiscoveredImpactKind, ObligationAnalysis, Task, TaskAcceptanceContract
from artifact_workflow_runtime.strategy import StrategyId

from .models import DecompositionComplexity, DecompositionPlan, ExecutionPacket, ExecutionPacketStatus, ExecutionPacketType
from .validator import DecompositionValidator

if TYPE_CHECKING:
    from artifact_workflow_runtime.models.state import WorkflowStateSnapshot


class DecompositionPlanner:
    def __init__(self, validator: DecompositionValidator | None = None, *, fallback_to_single_packet: bool = True) -> None:
        self.validator = validator or DecompositionValidator()
        self.fallback_to_single_packet = fallback_to_single_packet

    def build_plan(
        self,
        *,
        task: Task | str,
        strategy_id: StrategyId | str | None,
        acceptance_contract: TaskAcceptanceContract | dict | None = None,
        obligations: ObligationAnalysis | dict | None = None,
        snapshot: WorkflowStateSnapshot | None = None,
    ) -> DecompositionPlan:
        task_obj = task if isinstance(task, Task) else Task(description=str(task))
        strategy = StrategyId.coerce(strategy_id or StrategyId.DEFAULT)
        acceptance = _normalize_acceptance(acceptance_contract)
        obligation_model = _normalize_obligations(obligations)
        runtime_facts = _runtime_facts(snapshot)
        complexity = _infer_complexity(task_obj.description, acceptance=acceptance, obligations=obligation_model, runtime_facts=runtime_facts)
        plan_id = _stable_plan_id(task_obj.description, strategy.value, complexity.value)
        flags = _obligation_flags(acceptance, obligation_model, runtime_facts=runtime_facts)
        packets = self._packets_for_strategy(plan_id=plan_id, task=task_obj, strategy=strategy, complexity=complexity, flags=flags)
        plan = DecompositionPlan(
            plan_id=plan_id,
            task_summary=task_obj.description,
            strategy_id=strategy.value,
            complexity=complexity,
            packets=packets,
            risks=_dedupe(flags["risks"]),
            assumptions=_dedupe(flags["assumptions"]),
            decomposition_reason=_decomposition_reason(strategy, complexity, flags),
            metadata={"planner": "rule_based_v2", "task_id": task_obj.id, "runtime_facts": runtime_facts},
        )
        validation = self.validator.validate(plan, fallback_to_single_packet=self.fallback_to_single_packet)
        return validation.normalized_plan or plan

    def _packets_for_strategy(
        self,
        *,
        plan_id: str,
        task: Task,
        strategy: StrategyId,
        complexity: DecompositionComplexity,
        flags: dict[str, object],
    ) -> list[ExecutionPacket]:
        allowed_files = list(flags["allowed_files"])
        target_areas = list(flags["target_areas"])
        base_forbidden = ["do not choose the next workflow step", "do not expand scope without controller approval"]
        completed_types = set(flags["completed_packet_types"])

        if strategy == StrategyId.REPAIR_ONLY:
            return [
                _packet(
                    plan_id,
                    1,
                    "repair current bounded failure",
                    task.description,
                    scope="Repair only the currently failing packet/checks. Do not broaden scope.",
                    packet_type=ExecutionPacketType.REPAIR,
                    strategy_id=strategy.value,
                    success_criteria=["The targeted failure is repaired without unrelated scope expansion."],
                    required_evidence=["repair summary", "changed files", "relevant local checks"],
                    allowed_files=allowed_files,
                    target_areas=target_areas,
                    forbidden_actions=base_forbidden + ["do not expand scope"],
                )
            ]

        packets: list[ExecutionPacket] = []
        next_index = 1

        def append_packet(
            title: str,
            *,
            scope: str,
            packet_type: ExecutionPacketType,
            success_criteria: list[str],
            required_evidence: list[str],
            dependencies: list[str] | None = None,
            prepend: bool = False,
            metadata: dict[str, object] | None = None,
        ) -> ExecutionPacket:
            nonlocal next_index, packets
            packet = _packet(
                plan_id,
                next_index,
                title,
                task.description,
                scope=scope,
                packet_type=packet_type,
                strategy_id=strategy.value,
                success_criteria=success_criteria,
                required_evidence=required_evidence,
                dependencies=dependencies,
                allowed_files=allowed_files,
                target_areas=target_areas,
                forbidden_actions=base_forbidden,
                metadata=metadata,
            )
            next_index += 1
            if prepend:
                packets = [packet, *[item.model_copy(update={"dependencies": [packet.packet_id, *item.dependencies] if not item.dependencies else item.dependencies}) for item in packets]]
            else:
                packets.append(packet)
            return packet

        if flags["has_setup"] and ExecutionPacketType.SETUP not in completed_types:
            setup_success = [f"Environment dependency node ready: {name}" for name in flags["environment_nodes"]] or ["Required setup or environment prerequisites are satisfied for the bounded task."]
            setup_evidence = ["bootstrap attempt evidence", "runtime/toolchain readiness probe evidence"]
            append_packet(
                "prepare environment and setup prerequisites",
                scope="Materialize only the required environment dependency nodes. Do not implement product changes in this packet.",
                packet_type=ExecutionPacketType.SETUP,
                success_criteria=setup_success,
                required_evidence=setup_evidence,
                metadata={
                    "packet_scope_class": "environment_materialization",
                    "environment_nodes": list(flags["environment_nodes"]),
                    "verification_levels": list(flags["verification_levels"]),
                },
            )

        lead_id = packets[-1].packet_id if packets else None
        implementation_completed = bool(flags["implementation_completed"])

        if strategy == StrategyId.BDD_INCREMENTAL:
            if ExecutionPacketType.TEST not in completed_types:
                lead_id = append_packet(
                    "capture behavior and tests",
                    scope="Define or update behavior-oriented tests/spec examples for the targeted feature before broader implementation.",
                    packet_type=ExecutionPacketType.TEST,
                    success_criteria=["Behavior/test packet exists for the targeted change."],
                    required_evidence=["new or updated tests/spec examples"],
                    dependencies=[lead_id] if lead_id else None,
                ).packet_id
            if not implementation_completed:
                lead_id = append_packet(
                    "implement behavior to satisfy tests",
                    scope="Implement the bounded feature required by the preceding behavior/tests.",
                    packet_type=ExecutionPacketType.IMPLEMENTATION,
                    success_criteria=["Implementation satisfies the behavior/test packet."],
                    required_evidence=["changed source files", "local test/build results"],
                    dependencies=[lead_id] if lead_id else None,
                ).packet_id
        elif strategy == StrategyId.SPIKE_THEN_HARDEN:
            if ExecutionPacketType.SPIKE not in completed_types:
                lead_id = append_packet(
                    "spike discovery for feature",
                    scope="Run a bounded discovery/spike to understand the API, integration points, or environment before production hardening.",
                    packet_type=ExecutionPacketType.SPIKE,
                    success_criteria=["Key unknowns are reduced and concrete next implementation areas are identified."],
                    required_evidence=["discovery findings", "affected areas", "bounded next-step notes"],
                    dependencies=[lead_id] if lead_id else None,
                ).packet_id
            if not implementation_completed:
                lead_id = append_packet(
                    "harden minimal implementation after spike",
                    scope="Use spike findings to produce the first bounded implementation slice.",
                    packet_type=ExecutionPacketType.IMPLEMENTATION,
                    success_criteria=["Bounded implementation slice exists after spike."],
                    required_evidence=["changed source files", "local checks"],
                    dependencies=[lead_id] if lead_id else None,
                ).packet_id
        elif strategy == StrategyId.SAFE_REFACTOR:
            if ExecutionPacketType.REFACTOR not in completed_types:
                lead_id = append_packet(
                    "characterize current behavior",
                    scope="Add or identify low-risk characterization checks before refactoring.",
                    packet_type=ExecutionPacketType.REFACTOR,
                    success_criteria=["Characterization or refactor-prep evidence exists."],
                    required_evidence=["existing or added characterization tests/checks"],
                    dependencies=[lead_id] if lead_id else None,
                ).packet_id
            if not implementation_completed:
                lead_id = append_packet(
                    "apply small safe refactor",
                    scope="Perform a bounded low-risk refactor informed by characterization checks.",
                    packet_type=ExecutionPacketType.REFACTOR,
                    success_criteria=["Small refactor completed without regressions."],
                    required_evidence=["changed files", "characterization or regression checks"],
                    dependencies=[lead_id] if lead_id else None,
                ).packet_id
        else:
            if not implementation_completed:
                first_scope = (
                    "Deliver the smallest coherent working slice for the requested task."
                    if strategy == StrategyId.MVP_FIRST
                    else "Implement the bounded requested change."
                )
                first_title = "minimal working slice" if strategy == StrategyId.MVP_FIRST else "bounded implementation"
                lead_id = append_packet(
                    first_title,
                    scope=first_scope,
                    packet_type=ExecutionPacketType.IMPLEMENTATION,
                    success_criteria=["A bounded implementation slice exists."],
                    required_evidence=["changed source files", "local build/test evidence"],
                    dependencies=[lead_id] if lead_id else None,
                ).packet_id

        if strategy in {StrategyId.DEFAULT, StrategyId.MVP_FIRST} and packets and not implementation_completed and complexity in {DecompositionComplexity.TINY, DecompositionComplexity.SMALL, DecompositionComplexity.MEDIUM} and not flags["has_gap_reentry"]:
            return packets

        if flags["has_integration"] and ExecutionPacketType.INTEGRATION not in completed_types:
            lead_id = append_packet(
                "integrate bounded implementation surface",
                scope="Update the bounded integration path, wiring, or end-to-end surface required by the discovered obligations.",
                packet_type=ExecutionPacketType.INTEGRATION,
                success_criteria=["Integration surface for the bounded change is updated or verified."],
                required_evidence=["integration changes", "integration check evidence"],
                dependencies=[lead_id] if lead_id else None,
            ).packet_id

        if flags["has_tests"] and ExecutionPacketType.TEST not in completed_types and strategy != StrategyId.BDD_INCREMENTAL:
            lead_id = append_packet(
                "verify tests for bounded change",
                scope="Run or update the required bounded tests for this task.",
                packet_type=ExecutionPacketType.TEST,
                success_criteria=["Required tests for the bounded change are updated or executed."],
                required_evidence=["test commands", "test results"],
                dependencies=[lead_id] if lead_id else None,
            ).packet_id

        if flags["has_docs"] and ExecutionPacketType.DOCS not in completed_types:
            lead_id = append_packet(
                "update docs for bounded change",
                scope="Update only the directly impacted documentation/examples for this bounded change.",
                packet_type=ExecutionPacketType.DOCS,
                success_criteria=["Relevant docs/examples are updated."],
                required_evidence=["changed docs/examples"],
                dependencies=[lead_id] if lead_id else None,
            ).packet_id

        if flags["has_ci"] and ExecutionPacketType.PUBLISH_PREPARATION not in completed_types:
            lead_id = append_packet(
                "update ci or build surface",
                scope="Apply only the required CI/build changes for the bounded task surface.",
                packet_type=ExecutionPacketType.PUBLISH_PREPARATION,
                success_criteria=["Required CI/build surface updates are present."],
                required_evidence=["ci/build changes", "build verification evidence"],
                dependencies=[lead_id] if lead_id else None,
            ).packet_id

        if complexity != DecompositionComplexity.TINY and ExecutionPacketType.VERIFICATION not in completed_types:
            lead_id = append_packet(
                "verification checkpoint",
                scope="Collect bounded verification evidence that the packet set satisfies its acceptance surface.",
                packet_type=ExecutionPacketType.VERIFICATION,
                success_criteria=["Verification evidence exists for the bounded packet set."],
                required_evidence=["verification summary", "test/build status"],
                dependencies=[lead_id] if lead_id else None,
            ).packet_id

        if not packets:
            append_packet(
                "verification checkpoint",
                scope="Confirm the already completed bounded work still satisfies the current acceptance surface.",
                packet_type=ExecutionPacketType.VERIFICATION,
                success_criteria=["Verification evidence exists for the bounded work surface."],
                required_evidence=["verification summary", "bounded evidence"],
            )

        if strategy in {StrategyId.DEFAULT, StrategyId.MVP_FIRST} and len(packets) == 1 and complexity in {DecompositionComplexity.TINY, DecompositionComplexity.SMALL, DecompositionComplexity.MEDIUM} and not flags["has_docs"] and not flags["has_ci"] and not flags["has_integration"] and not flags["has_setup"]:
            return packets
        if complexity in {DecompositionComplexity.TINY, DecompositionComplexity.SMALL} and len(packets) == 1:
            return packets
        return packets[:5]


def _packet(
    plan_id: str,
    index: int,
    title: str,
    task_summary: str,
    *,
    scope: str,
    packet_type: ExecutionPacketType,
    strategy_id: str,
    success_criteria: list[str],
    required_evidence: list[str],
    dependencies: list[str] | None = None,
    allowed_files: list[str] | None = None,
    target_areas: list[str] | None = None,
    forbidden_actions: list[str] | None = None,
    metadata: dict[str, object] | None = None,
) -> ExecutionPacket:
    return ExecutionPacket(
        packet_id=_stable_packet_id(plan_id, index, title),
        title=title,
        goal=task_summary,
        scope=scope,
        packet_type=packet_type,
        strategy_id=strategy_id,
        status=ExecutionPacketStatus.PENDING,
        dependencies=list(dependencies or []),
        allowed_files=list(allowed_files or []),
        target_areas=list(target_areas or []),
        forbidden_actions=_dedupe(forbidden_actions or []),
        success_criteria=_dedupe(success_criteria),
        required_evidence=_dedupe(required_evidence),
        metadata={"packet_index": index, **(metadata or {})},
    )


def _normalize_acceptance(value: TaskAcceptanceContract | dict | None) -> TaskAcceptanceContract | None:
    if value is None:
        return None
    return value if isinstance(value, TaskAcceptanceContract) else TaskAcceptanceContract.model_validate(value)


def _normalize_obligations(value: ObligationAnalysis | dict | None) -> ObligationAnalysis | None:
    if value is None:
        return None
    return value if isinstance(value, ObligationAnalysis) else ObligationAnalysis.model_validate(value)


def _runtime_facts(snapshot: WorkflowStateSnapshot | None) -> dict[str, object]:
    if snapshot is None:
        return {
            "known_blockers": [],
            "evidence_gaps": [],
            "target_areas": [],
            "allowed_files": [],
            "environment_gaps": [],
            "completed_packet_ids": [],
            "completed_packet_types": [],
            "mutation_scope": [],
            "has_existing_mutation": False,
            "discovered_obligation_types": [],
        }
    blockers: list[str] = []
    evidence_gaps: list[str] = []
    target_areas: list[str] = []
    allowed_files: list[str] = []
    environment_gaps: list[str] = []
    completed_packet_ids: list[str] = []
    completed_packet_types: list[str] = []
    mutation_scope: list[str] = []
    discovered_obligation_types: list[str] = []
    if snapshot.execution_result is not None:
        blockers.extend(item.summary for item in snapshot.execution_result.structured_evidence.blockers)
        environment_gaps.extend(_environment_gap_summaries(snapshot.execution_result.structured_evidence.blockers))
        target_areas.extend(item.path for item in snapshot.execution_result.structured_evidence.files_changed)
        mutation_scope.extend(item.path for item in snapshot.execution_result.structured_evidence.files_changed)
    if snapshot.observation_result is not None:
        target_areas.extend(item.path for item in snapshot.observation_result.structured_evidence.files_changed)
        blockers.extend(item.summary for item in snapshot.observation_result.structured_evidence.blockers)
        environment_gaps.extend(_environment_gap_summaries(snapshot.observation_result.structured_evidence.blockers))
    if snapshot.verification_result is not None:
        evidence_gaps.extend(snapshot.verification_result.missing_evidence)
        evidence_gaps.extend(snapshot.verification_result.missing_obligations)
        evidence_gaps.extend(snapshot.verification_result.missing_test_levels)
        environment_gaps.extend(snapshot.verification_result.missing_setup_steps)
    if snapshot.obligations is not None:
        target_areas.extend(snapshot.obligations.affected_surfaces)
        allowed_files.extend(snapshot.obligations.affected_surfaces)
        blockers.extend(snapshot.obligations.blocker_conditions)
        discovered_obligation_types.extend(["tests"] if snapshot.obligations.required_test_levels else [])
        discovered_obligation_types.extend(["docs"] if snapshot.obligations.required_documentation_updates or snapshot.obligations.required_examples_updates else [])
        discovered_obligation_types.extend(["ci"] if snapshot.obligations.required_ci_updates or snapshot.obligations.required_codegen_or_build_updates else [])
        for impact in snapshot.obligations.discovered_impacts:
            discovered_obligation_types.append(impact.kind.value)
            target_areas.extend(impact.affected_paths)
            if impact.kind == DiscoveredImpactKind.SETUP:
                environment_gaps.append(impact.summary)
            if impact.blocking:
                blockers.append(impact.summary)
    if snapshot.packet_history:
        for item in snapshot.packet_history:
            if item.new_status == ExecutionPacketStatus.COMPLETED:
                completed_packet_ids.append(item.packet_id)
    if snapshot.decomposition_plan is not None:
        for packet in snapshot.decomposition_plan.packets:
            if packet.status == ExecutionPacketStatus.COMPLETED:
                completed_packet_ids.append(packet.packet_id)
                completed_packet_types.append(packet.packet_type.value)
                if packet.packet_type == ExecutionPacketType.IMPLEMENTATION:
                    mutation_scope.extend(packet.allowed_files or packet.target_areas)
    return {
        "known_blockers": _dedupe(blockers),
        "evidence_gaps": _dedupe(evidence_gaps),
        "target_areas": _dedupe(target_areas),
        "allowed_files": _dedupe(allowed_files or target_areas),
        "environment_gaps": _dedupe(environment_gaps),
        "completed_packet_ids": _dedupe(completed_packet_ids),
        "completed_packet_types": _dedupe(completed_packet_types),
        "mutation_scope": _dedupe(mutation_scope),
        "has_existing_mutation": bool(mutation_scope),
        "discovered_obligation_types": _dedupe(discovered_obligation_types),
    }


def _obligation_flags(
    acceptance: TaskAcceptanceContract | None,
    obligations: ObligationAnalysis | None,
    *,
    runtime_facts: dict[str, object],
) -> dict[str, object]:
    kinds = {item.kind for item in acceptance.obligations} if acceptance is not None else set()
    completed_packet_types = set(str(item) for item in runtime_facts.get("completed_packet_types", []))
    verification_levels = [str(item).lower() for item in ((acceptance.required_verification_levels if acceptance is not None else []) or (obligations.required_test_levels if obligations is not None else []))]
    runtime_levels = {"integration", "smoke", "e2e", "end-to-end", "runtime", "runtime_proof"}
    impact_kinds = {impact.kind for impact in obligations.discovered_impacts} if obligations is not None else set()
    has_tests = bool(verification_levels) or any(kind in kinds for kind in {AcceptanceObligationKind.RELEVANT_TESTS_RUN, AcceptanceObligationKind.RELEVANT_TESTS_PASSED})
    has_docs = (
        any(kind in kinds for kind in {AcceptanceObligationKind.DOCUMENTATION_UPDATED, AcceptanceObligationKind.EXAMPLES_UPDATED})
        or bool(obligations and (obligations.required_documentation_updates or obligations.required_examples_updates))
    )
    has_ci = (
        any(kind == AcceptanceObligationKind.CI_OR_BUILD_UPDATED for kind in kinds)
        or bool(obligations and (obligations.required_ci_updates or obligations.required_codegen_or_build_updates))
    )
    has_integration = (
        bool(set(verification_levels) & runtime_levels)
        or any(kind in kinds for kind in {AcceptanceObligationKind.INTEGRATION_TESTS_RUN, AcceptanceObligationKind.INTEGRATION_TESTS_PASSED})
        or DiscoveredImpactKind.INTEGRATION in impact_kinds
    )
    environment_nodes = list(acceptance.materializable_environment_nodes if acceptance is not None else [])
    has_setup = bool(runtime_facts.get("environment_gaps")) or DiscoveredImpactKind.SETUP in impact_kinds
    target_areas = list((acceptance.required_work_surfaces if acceptance is not None else [])) + list(obligations.affected_surfaces if obligations else []) + list(runtime_facts.get("target_areas", []))
    allowed_files = list(obligations.affected_surfaces if obligations else []) + list(runtime_facts.get("allowed_files", []))
    risks = list(obligations.blocker_conditions if obligations else []) + list(runtime_facts.get("known_blockers", []))
    assumptions = list(environment_nodes)
    if obligations is not None:
        assumptions.extend(obligations.required_environment_conditions)
    if has_ci:
        risks.append("ci/build surface may require dedicated follow-up")
    if runtime_facts.get("has_existing_mutation"):
        risks.append("runtime already observed mutation evidence; keep packet scope bounded to unfinished work")
    return {
        "has_tests": has_tests,
        "has_docs": has_docs,
        "has_ci": has_ci,
        "has_integration": has_integration,
        "has_setup": has_setup,
        "environment_nodes": _dedupe(environment_nodes),
        "verification_levels": _dedupe(verification_levels),
        "target_areas": _dedupe(target_areas),
        "allowed_files": _dedupe(allowed_files),
        "risks": _dedupe(risks),
        "assumptions": _dedupe(assumptions),
        "completed_packet_types": completed_packet_types,
        "implementation_completed": ExecutionPacketType.IMPLEMENTATION.value in completed_packet_types,
        "has_gap_reentry": bool(runtime_facts.get("evidence_gaps")) or bool(runtime_facts.get("known_blockers")),
    }


def _infer_complexity(task_summary: str, *, acceptance: TaskAcceptanceContract | None, obligations: ObligationAnalysis | None, runtime_facts: dict[str, object]) -> DecompositionComplexity:
    text = task_summary.lower()
    score = 0
    if len(task_summary) > 140:
        score += 1
    if any(word in text for word in ("feature", "implement", "integration", "migrate", "refactor", "pipeline", "workflow")):
        score += 1
    if obligations is not None:
        score += min(
            4,
            int(bool(obligations.required_test_levels))
            + int(bool(obligations.required_documentation_updates or obligations.required_examples_updates))
            + int(bool(obligations.required_ci_updates or obligations.required_codegen_or_build_updates))
            + int(bool(obligations.required_environment_conditions or obligations.required_setup_steps)),
        )
        score += 1 if len(obligations.affected_surfaces) >= 3 else 0
        score += 1 if any(impact.kind in {DiscoveredImpactKind.INTEGRATION, DiscoveredImpactKind.SETUP} for impact in obligations.discovered_impacts) else 0
    if acceptance is not None and len(acceptance.obligations) >= 5:
        score += 1
    if any(word in text for word in ("large", "end-to-end", "end to end", "system-wide", "platform")):
        score += 2
    if runtime_facts.get("known_blockers"):
        score += 1
    if len(runtime_facts.get("target_areas", [])) >= 3:
        score += 1
    if runtime_facts.get("environment_gaps"):
        score += 1
    if score <= 1:
        return DecompositionComplexity.TINY
    if score == 2:
        return DecompositionComplexity.SMALL
    if score <= 5:
        return DecompositionComplexity.MEDIUM
    return DecompositionComplexity.LARGE


def _decomposition_reason(strategy: StrategyId, complexity: DecompositionComplexity, flags: dict[str, object]) -> str:
    parts = [f"strategy={strategy.value}", f"complexity={complexity.value}"]
    if flags.get("has_setup"):
        parts.append("setup_obligation_present")
    if flags.get("has_tests"):
        parts.append("tests_obligation_present")
    if flags.get("has_integration"):
        parts.append("integration_obligation_present")
    if flags.get("has_docs"):
        parts.append("docs_obligation_present")
    if flags.get("has_ci"):
        parts.append("ci_obligation_present")
    if flags.get("implementation_completed"):
        parts.append("existing_implementation_work_detected")
    if flags.get("risks"):
        parts.append("runtime_risks_considered")
    return ", ".join(parts)


def _stable_plan_id(task_summary: str, strategy_id: str, complexity: str) -> str:
    digest = hashlib.sha1(f"{task_summary}|{strategy_id}|{complexity}".encode("utf-8")).hexdigest()[:12]
    return f"decomp_{digest}"


def _stable_packet_id(plan_id: str, index: int, title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:24] or "packet"
    return f"{plan_id}_pkt_{index}_{slug}"


def _dedupe(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out


def _environment_gap_summaries(blockers: Iterable[object]) -> list[str]:
    gaps: list[str] = []
    for blocker in blockers:
        raw_kind = getattr(blocker, "blocker_kind", "")
        kind = str(getattr(raw_kind, "value", raw_kind) or "").lower()
        summary = str(getattr(blocker, "summary", "") or "").strip()
        if not summary:
            continue
        if kind in {"integration_environment_unavailable", "missing_environment_dependency", "missing_runtime_prerequisite"}:
            gaps.append(summary)
    return _dedupe(gaps)

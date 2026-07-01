from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING, Iterable

from artifact_workflow_runtime.models import AcceptanceObligationKind, ObligationAnalysis, Task, TaskAcceptanceContract
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
        packets = self._packets_for_strategy(
            plan_id=plan_id,
            task=task_obj,
            strategy=strategy,
            complexity=complexity,
            flags=flags,
        )
        plan = DecompositionPlan(
            plan_id=plan_id,
            task_summary=task_obj.description,
            strategy_id=strategy.value,
            complexity=complexity,
            packets=packets,
            risks=_dedupe(flags["risks"]),
            assumptions=_dedupe(flags["assumptions"]),
            decomposition_reason=_decomposition_reason(strategy, complexity, flags),
            metadata={"planner": "rule_based_v1", "task_id": task_obj.id, "runtime_facts": runtime_facts},
        )
        validation = self.validator.validate(plan, fallback_to_single_packet=self.fallback_to_single_packet)
        return validation.normalized_plan or plan

    def _packets_for_strategy(self, *, plan_id: str, task: Task, strategy: StrategyId, complexity: DecompositionComplexity, flags: dict[str, object]) -> list[ExecutionPacket]:
        has_tests = bool(flags["has_tests"])
        has_docs = bool(flags["has_docs"])
        needs_verification = True
        allowed_files = list(flags["allowed_files"])
        target_areas = list(flags["target_areas"])
        base_forbidden = ["do not choose the next workflow step", "do not expand scope without controller approval"]

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
        if strategy == StrategyId.BDD_INCREMENTAL:
            packets.append(_packet(plan_id, 1, "capture behavior and tests", task.description,
                scope="Define or update behavior-oriented tests/spec examples for the targeted feature before broader implementation.",
                packet_type=ExecutionPacketType.TEST,
                strategy_id=strategy.value,
                success_criteria=["Behavior/test packet exists for the targeted change."],
                required_evidence=["new or updated tests/spec examples"],
                allowed_files=allowed_files,
                target_areas=target_areas,
                forbidden_actions=base_forbidden))
            packets.append(_packet(plan_id, 2, "implement behavior to satisfy tests", task.description,
                scope="Implement the bounded feature required by the preceding behavior/tests.",
                packet_type=ExecutionPacketType.IMPLEMENTATION,
                strategy_id=strategy.value,
                dependencies=[packets[0].packet_id],
                success_criteria=["Implementation satisfies the behavior/test packet."],
                required_evidence=["changed source files", "local test/build results"],
                allowed_files=allowed_files,
                target_areas=target_areas,
                forbidden_actions=base_forbidden))
        elif strategy == StrategyId.SPIKE_THEN_HARDEN:
            packets.append(_packet(plan_id, 1, "spike discovery for feature", task.description,
                scope="Run a bounded discovery/spike to understand the API, integration points, or environment before production hardening.",
                packet_type=ExecutionPacketType.SPIKE,
                strategy_id=strategy.value,
                success_criteria=["Key unknowns are reduced and concrete next implementation areas are identified."],
                required_evidence=["discovery findings", "affected areas", "bounded next-step notes"],
                allowed_files=allowed_files,
                target_areas=target_areas,
                forbidden_actions=base_forbidden + ["avoid broad production refactors in the spike"]))
            packets.append(_packet(plan_id, 2, "harden minimal implementation after spike", task.description,
                scope="Use spike findings to produce the first bounded implementation slice.",
                packet_type=ExecutionPacketType.IMPLEMENTATION,
                strategy_id=strategy.value,
                dependencies=[packets[0].packet_id],
                success_criteria=["Bounded implementation slice exists after spike."],
                required_evidence=["changed source files", "local checks"],
                allowed_files=allowed_files,
                target_areas=target_areas,
                forbidden_actions=base_forbidden))
        elif strategy == StrategyId.SAFE_REFACTOR:
            packets.append(_packet(plan_id, 1, "characterize current behavior", task.description,
                scope="Add or identify low-risk characterization checks before refactoring.",
                packet_type=ExecutionPacketType.REFACTOR,
                strategy_id=strategy.value,
                success_criteria=["Characterization or refactor-prep evidence exists."],
                required_evidence=["existing or added characterization tests/checks"],
                allowed_files=allowed_files,
                target_areas=target_areas,
                forbidden_actions=base_forbidden))
            packets.append(_packet(plan_id, 2, "apply small safe refactor", task.description,
                scope="Perform a bounded low-risk refactor informed by characterization checks.",
                packet_type=ExecutionPacketType.REFACTOR,
                strategy_id=strategy.value,
                dependencies=[packets[0].packet_id],
                success_criteria=["Small refactor completed without regressions."],
                required_evidence=["changed files", "characterization or regression checks"],
                allowed_files=allowed_files,
                target_areas=target_areas,
                forbidden_actions=base_forbidden))
        else:
            first_scope = "Deliver the smallest coherent working slice for the requested task." if strategy == StrategyId.MVP_FIRST else "Implement the bounded requested change."
            first_title = "minimal working slice" if strategy == StrategyId.MVP_FIRST else "bounded implementation"
            packets.append(_packet(plan_id, 1, first_title, task.description,
                scope=first_scope,
                packet_type=ExecutionPacketType.IMPLEMENTATION,
                strategy_id=strategy.value,
                success_criteria=["A bounded implementation slice exists."],
                required_evidence=["changed source files", "local build/test evidence"],
                allowed_files=allowed_files,
                target_areas=target_areas,
                forbidden_actions=base_forbidden))

        lead_id = packets[-1].packet_id if packets else None
        if strategy in {StrategyId.DEFAULT, StrategyId.MVP_FIRST} and len(packets) == 1 and complexity in {DecompositionComplexity.TINY, DecompositionComplexity.SMALL, DecompositionComplexity.MEDIUM} and not has_docs and not bool(flags["has_ci"]):
            return packets
        if complexity in {DecompositionComplexity.TINY, DecompositionComplexity.SMALL} and len(packets) == 1:
            return packets
        if has_tests:
            packets.append(_packet(plan_id, len(packets)+1, "verify tests for bounded change", task.description,
                scope="Run or update the required bounded tests for this task.",
                packet_type=ExecutionPacketType.TEST,
                strategy_id=strategy.value,
                dependencies=[lead_id] if lead_id else [],
                success_criteria=["Required tests for the bounded change are updated or executed."],
                required_evidence=["test commands", "test results"],
                allowed_files=allowed_files,
                target_areas=target_areas,
                forbidden_actions=base_forbidden))
            lead_id = packets[-1].packet_id
        if has_docs:
            packets.append(_packet(plan_id, len(packets)+1, "update docs for bounded change", task.description,
                scope="Update only the directly impacted documentation/examples for this bounded change.",
                packet_type=ExecutionPacketType.DOCS,
                strategy_id=strategy.value,
                dependencies=[lead_id] if lead_id else [],
                success_criteria=["Relevant docs/examples are updated."],
                required_evidence=["changed docs/examples"],
                allowed_files=allowed_files,
                target_areas=target_areas,
                forbidden_actions=base_forbidden))
            lead_id = packets[-1].packet_id
        if needs_verification and complexity != DecompositionComplexity.TINY:
            packets.append(_packet(plan_id, len(packets)+1, "verification checkpoint", task.description,
                scope="Collect bounded verification evidence that the packet set satisfies its acceptance surface.",
                packet_type=ExecutionPacketType.VERIFICATION,
                strategy_id=strategy.value,
                dependencies=[lead_id] if lead_id else [],
                success_criteria=["Verification evidence exists for the bounded packet set."],
                required_evidence=["verification summary", "test/build status"],
                allowed_files=allowed_files,
                target_areas=target_areas,
                forbidden_actions=base_forbidden))
        return packets[:5]


def _packet(plan_id: str, index: int, title: str, task_summary: str, *, scope: str, packet_type: ExecutionPacketType, strategy_id: str, success_criteria: list[str], required_evidence: list[str], dependencies: list[str] | None = None, allowed_files: list[str] | None = None, target_areas: list[str] | None = None, forbidden_actions: list[str] | None = None) -> ExecutionPacket:
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
        metadata={"packet_index": index},
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
            "mutation_scope": [],
            "has_existing_mutation": False,
        }
    blockers: list[str] = []
    evidence_gaps: list[str] = []
    target_areas: list[str] = []
    allowed_files: list[str] = []
    environment_gaps: list[str] = []
    completed_packet_ids: list[str] = []
    mutation_scope: list[str] = []
    if snapshot.execution_result is not None:
        blockers.extend(item.summary for item in snapshot.execution_result.structured_evidence.blockers)
        target_areas.extend(item.path for item in snapshot.execution_result.structured_evidence.files_changed)
        mutation_scope.extend(item.path for item in snapshot.execution_result.structured_evidence.files_changed)
    if snapshot.observation_result is not None:
        target_areas.extend(item.path for item in snapshot.observation_result.structured_evidence.files_changed)
        blockers.extend(item.summary for item in snapshot.observation_result.structured_evidence.blockers)
    if snapshot.verification_result is not None:
        evidence_gaps.extend(snapshot.verification_result.missing_evidence)
        evidence_gaps.extend(snapshot.verification_result.missing_obligations)
        evidence_gaps.extend(snapshot.verification_result.missing_test_levels)
        environment_gaps.extend(snapshot.verification_result.missing_setup_steps)
    if snapshot.obligations is not None:
        target_areas.extend(snapshot.obligations.affected_surfaces)
        allowed_files.extend(snapshot.obligations.affected_surfaces)
        blockers.extend(snapshot.obligations.blocker_conditions)
        environment_gaps.extend(snapshot.obligations.required_environment_conditions)
    if snapshot.packet_history:
        completed_packet_ids.extend(item.packet_id for item in snapshot.packet_history if item.new_status == ExecutionPacketStatus.COMPLETED)
    return {
        "known_blockers": _dedupe(blockers),
        "evidence_gaps": _dedupe(evidence_gaps),
        "target_areas": _dedupe(target_areas),
        "allowed_files": _dedupe(allowed_files or target_areas),
        "environment_gaps": _dedupe(environment_gaps),
        "completed_packet_ids": _dedupe(completed_packet_ids),
        "mutation_scope": _dedupe(mutation_scope),
        "has_existing_mutation": bool(mutation_scope),
    }


def _obligation_flags(acceptance: TaskAcceptanceContract | None, obligations: ObligationAnalysis | None, *, runtime_facts: dict[str, object]) -> dict[str, object]:
    kinds = {item.kind for item in acceptance.obligations} if acceptance is not None else set()
    evidence_gaps = [str(item).lower() for item in runtime_facts.get("evidence_gaps", [])]
    has_tests = any(kind in kinds for kind in {AcceptanceObligationKind.RELEVANT_TESTS_RUN, AcceptanceObligationKind.RELEVANT_TESTS_PASSED, AcceptanceObligationKind.INTEGRATION_TESTS_RUN, AcceptanceObligationKind.INTEGRATION_TESTS_PASSED}) or bool(obligations and obligations.required_test_levels) or any("test" in item or "behavior" in item for item in evidence_gaps)
    has_docs = any(kind in kinds for kind in {AcceptanceObligationKind.DOCUMENTATION_UPDATED, AcceptanceObligationKind.EXAMPLES_UPDATED}) or bool(obligations and (obligations.required_documentation_updates or obligations.required_examples_updates)) or any("doc" in item or "example" in item or "readme" in item for item in evidence_gaps)
    has_ci = any(kind == AcceptanceObligationKind.CI_OR_BUILD_UPDATED for kind in kinds) or bool(obligations and obligations.required_ci_updates) or any("ci" in item or "build" in item for item in evidence_gaps)
    target_areas = list(obligations.affected_surfaces if obligations else []) + list(runtime_facts.get("target_areas", []))
    allowed_files = list(obligations.affected_surfaces if obligations else []) + list(runtime_facts.get("allowed_files", []))
    risks = list(obligations.blocker_conditions if obligations else []) + list(runtime_facts.get("known_blockers", []))
    assumptions = list(obligations.required_environment_conditions if obligations else []) + list(runtime_facts.get("environment_gaps", []))
    if has_ci:
        risks.append("ci/build surface may require dedicated follow-up")
    if runtime_facts.get("has_existing_mutation"):
        risks.append("runtime already observed mutation evidence; keep packet scope bounded to unfinished work")
    return {
        "has_tests": has_tests,
        "has_docs": has_docs,
        "has_ci": has_ci,
        "target_areas": _dedupe(target_areas),
        "allowed_files": _dedupe(allowed_files),
        "risks": _dedupe(risks),
        "assumptions": _dedupe(assumptions),
    }


def _infer_complexity(task_summary: str, *, acceptance: TaskAcceptanceContract | None, obligations: ObligationAnalysis | None, runtime_facts: dict[str, object]) -> DecompositionComplexity:
    text = task_summary.lower()
    score = 0
    if len(task_summary) > 140:
        score += 1
    if any(word in text for word in ("feature", "implement", "integration", "migrate", "refactor", "pipeline", "workflow")):
        score += 1
    if obligations is not None:
        score += min(3, int(bool(obligations.required_test_levels)) + int(bool(obligations.required_documentation_updates or obligations.required_examples_updates)) + int(bool(obligations.required_ci_updates)))
        score += 1 if len(obligations.affected_surfaces) >= 3 else 0
    if acceptance is not None and len(acceptance.obligations) >= 5:
        score += 1
    if any(word in text for word in ("large", "end-to-end", "end to end", "system-wide", "platform")):
        score += 2
    if runtime_facts.get("known_blockers"):
        score += 1
    if len(runtime_facts.get("target_areas", [])) >= 3:
        score += 1
    if score <= 1:
        return DecompositionComplexity.TINY
    if score == 2:
        return DecompositionComplexity.SMALL
    if score <= 4:
        return DecompositionComplexity.MEDIUM
    return DecompositionComplexity.LARGE


def _decomposition_reason(strategy: StrategyId, complexity: DecompositionComplexity, flags: dict[str, object]) -> str:
    parts = [f"strategy={strategy.value}", f"complexity={complexity.value}"]
    if flags.get("has_tests"):
        parts.append("tests_obligation_present")
    if flags.get("has_docs"):
        parts.append("docs_obligation_present")
    if flags.get("has_ci"):
        parts.append("ci_obligation_present")
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

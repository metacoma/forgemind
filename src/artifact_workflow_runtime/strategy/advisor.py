from __future__ import annotations

import json
from typing import Any

from artifact_workflow_runtime.contracts import ContractViolationError
from artifact_workflow_runtime.models import BackendKind, LLMRequest
from artifact_workflow_runtime.models.base import RuntimeModel
from artifact_workflow_runtime.models.state import WorkflowStateSnapshot

from .catalog import DEFAULT_STRATEGY_CATALOG, StrategyCatalog
from .signals import ALLOWED_STRATEGY_SIGNAL_NAMES

from .models import (
    LLMStrategyRecommendation,
    StrategyAdvisorContext,
    StrategyAdvisorStatus,
    StrategyCheckpointSignals,
    StrategyId,
)


class StrategyContextBuilder:
    """Build a compact typed context for optional LLM strategy advice.

    The advisor does not receive the full workflow state, raw agent chat, or any
    filesystem/runtime access. This context is deliberately small and durable so
    policy validation can reason about exactly which signals were exposed.
    """

    def __init__(self, catalog: StrategyCatalog | None = None, *, max_previous_decisions: int = 5) -> None:
        self.catalog = catalog or DEFAULT_STRATEGY_CATALOG
        self.max_previous_decisions = max_previous_decisions

    def build(
        self,
        *,
        snapshot: WorkflowStateSnapshot,
        signals: StrategyCheckpointSignals,
        current_packet_summary: str | None = None,
    ) -> StrategyAdvisorContext:
        execution = snapshot.execution_result
        verification = snapshot.verification_result
        previous = [item.model_dump(mode="json") for item in snapshot.strategy_decisions[-self.max_previous_decisions :]]
        return StrategyAdvisorContext(
            task_summary=_task_summary(snapshot),
            current_stage=signals.current_stage,
            allowed_signal_names=list(ALLOWED_STRATEGY_SIGNAL_NAMES),
            active_strategy=snapshot.active_strategy,
            previous_strategy_decisions=previous,
            available_strategies=self.catalog.list(),
            checkpoint_signals=signals,
            missing_evidence=list(signals.missing_evidence),
            failed_checks=_failed_checks(snapshot),
            blockers=list(signals.blockers),
            execution_status=signals.execution_status,
            verification_status=signals.verification_status,
            acceptance_status=signals.acceptance_status,
            repair_count=signals.repair_count,
            changed_files_summary=_changed_files_summary(execution),
            known_constraints=[
                "choose_only_available_strategy",
                "do_not_choose_lifecycle_transition",
                "do_not_execute_tools",
                "do_not_approve_completion",
                "do_not_disable_verification",
                "do_not_enable_publish_push_or_merge",
            ],
            current_bounded_packet_summary=current_packet_summary,
        )


class LLMStrategyAdvisor:
    """One-shot text-only strategy advisor backed by the existing LLM backend."""

    def __init__(self, llm_backend: Any, artifact_store: Any | None = None, *, model_override: str | None = None) -> None:
        self.llm_backend = llm_backend
        self.artifact_store = artifact_store
        self.model_override = model_override

    async def recommend(self, context: StrategyAdvisorContext, *, task_id: str) -> LLMStrategyRecommendation:
        request = LLMRequest(
            kind="strategy_advisor",
            prompt=_build_strategy_advisor_prompt(context),
            task_id=task_id,
            task_text=context.task_summary,
            purpose="typed strategy recommendation only",
            instructions=[
                "choose one available strategy only",
                "do not choose lifecycle transitions",
                "do not execute tools",
                "return strict JSON",
            ],
            input_artifact_ids=[],
            backend=BackendKind.DIRECT_LLM,
            allowed_inputs=["task_text", "strategy_catalog", "checkpoint_signals", "schema_text"],
            forbidden_inputs=["filesystem", "shell", "git", "hosts", "kubernetes", "network_runtime_state", "openhands", "tools"],
            metadata={"model_slot": "strategy", "model_override": self.model_override},
        )
        try:
            llm_result, parsed = await self.llm_backend.complete_json(request, LLMStrategyRecommendationPayload)
        except ContractViolationError as exc:
            return LLMStrategyRecommendation(
                advisor_status=StrategyAdvisorStatus.INVALID_JSON,
                reason="Strategy advisor returned invalid JSON or schema-invalid content.",
                error=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 - fallback must protect runtime from advisor/backend failures
            return LLMStrategyRecommendation(
                advisor_status=StrategyAdvisorStatus.BACKEND_ERROR,
                reason="Strategy advisor backend failed; falling back to deterministic baseline.",
                error=str(exc),
            )

        artifact_id = None
        if self.artifact_store is not None:
            artifact = self.artifact_store.add_json(
                "strategy_llm_recommendation_raw",
                {
                    "llm_request": request.model_dump(mode="json"),
                    "llm_result": llm_result.model_dump(mode="json"),
                    "recommendation": parsed.model_dump(mode="json"),
                },
                metadata={"task_id": task_id, "advisor_status": "success"},
            )
            artifact_id = artifact.id
        return LLMStrategyRecommendation(
            selected_strategy=parsed.selected_strategy,
            reason=parsed.reason,
            confidence=parsed.confidence,
            signals_used=list(parsed.signals_used),
            constraints=list(parsed.constraints),
            raw_response_artifact_id=artifact_id,
            advisor_status=StrategyAdvisorStatus.SUCCESS,
        )


class LLMStrategyRecommendationPayload(RuntimeModel):
    selected_strategy: str
    reason: str
    confidence: float
    signals_used: list[str] = []
    constraints: list[str] = []


def _build_strategy_advisor_prompt(context: StrategyAdvisorContext) -> str:
    schema = {
        "selected_strategy": "one exact id from available_strategies",
        "reason": "non-empty string grounded only in checkpoint context",
        "confidence": "number between 0.0 and 1.0",
        "signals_used": ["exact names from allowed_signal_names only"],
        "constraints": ["constraints preserved by the recommendation"],
    }
    payload = {
        "available_strategies": [item.model_dump(mode="json") for item in context.available_strategies],
        "allowed_signal_names": list(context.allowed_signal_names),
        "active_strategy": context.active_strategy.value if isinstance(context.active_strategy, StrategyId) else context.active_strategy,
        "current_active_strategy": context.active_strategy.value if isinstance(context.active_strategy, StrategyId) else context.active_strategy,
        "current_stage": context.current_stage,
        "task_description": context.task_summary,
        "task_summary": context.task_summary,
        "checkpoint_signals": context.checkpoint_signals.model_dump(mode="json"),
        "missing_evidence": context.missing_evidence,
        "failed_checks": context.failed_checks,
        "blockers": context.blockers,
        "execution_status": context.execution_status,
        "verification_status": context.verification_status,
        "acceptance_status": context.acceptance_status,
        "repair_count": context.repair_count,
        "changed_files_summary": context.changed_files_summary,
        "known_constraints": context.known_constraints,
        "current_bounded_packet_summary": context.current_bounded_packet_summary,
        "previous_strategy_decisions": context.previous_strategy_decisions,
    }
    return (
        "You are a Strategy Advisor inside a deterministic workflow runtime.\n"
        "You do not decide what task to do next.\n"
        "You do not execute tools.\n"
        "You do not approve completion.\n"
        "You only recommend which available strategy should guide the next workflow segment.\n"
        "Choose exactly one strategy from the provided StrategyCatalog.\n"
        "Base the recommendation only on the provided checkpoint context and signals.\n"
        "Use only these exact names in signals_used. Do not invent new signal names.\n"
        "You cannot change lifecycle transitions, verifier requirements, publish/push/merge permissions, or create new strategies.\n"
        "Return strict JSON only. Do not include markdown fences or prose around JSON.\n\n"
        "Allowed signal names:\n"
        f"{_format_allowed_signal_names(context.allowed_signal_names)}\n\n"
        "Expected JSON shape:\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
        "Checkpoint context:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _task_summary(snapshot: WorkflowStateSnapshot) -> str:
    classification = snapshot.classification
    if classification is not None and classification.normalized_task:
        return classification.normalized_task
    return snapshot.task.description


def _changed_files_summary(execution: Any | None) -> list[str]:
    if execution is None:
        return []
    changed = []
    for item in execution.structured_evidence.files_changed:
        changed.append(f"{item.path}: {item.action}; {item.summary}")
    if not changed and execution.structured_evidence.mutation_summary.files_changed:
        changed.extend(execution.structured_evidence.mutation_summary.files_changed)
    return changed[:30]


def _failed_checks(snapshot: WorkflowStateSnapshot) -> list[str]:
    values: list[str] = []
    if snapshot.verification_result is not None:
        values.extend(snapshot.verification_result.checks_failed)
    if snapshot.qa_review_result is not None:
        values.extend(snapshot.qa_review_result.failing_checks)
    if snapshot.publish_result is not None:
        for item in snapshot.publish_result.structured_evidence.tests:
            if item.passed is False or str(item.status).lower() in {"failed", "error", "blocked"}:
                values.append(item.name)
    return _unique(values)


def _unique(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out


def _format_allowed_signal_names(signal_names: list[str]) -> str:
    return "\n".join(f"- {name}" for name in signal_names)

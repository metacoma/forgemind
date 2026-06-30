from __future__ import annotations

from types import SimpleNamespace

import pytest

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.control_plane import RuntimeKernel
from artifact_workflow_runtime.llm_backend import ScriptedLLMBackend
from artifact_workflow_runtime.models import Capability, ExecutionFamily, ExecutionPlan, ExecutionResult, ExecutionStatus, Task
from artifact_workflow_runtime.models.state import WorkflowStateSnapshot
from artifact_workflow_runtime.openhands_adapter import FakeOpenHandsAdapter
from artifact_workflow_runtime.stages import WorkflowStageNodes
from artifact_workflow_runtime.strategy import StrategyArbitrator, StrategyGovernor, StrategyId, StrategySelectionMode
from artifact_workflow_runtime.strategy.runtime import record_strategy_checkpoint_async

pytestmark = pytest.mark.asyncio


def _plan() -> ExecutionPlan:
    return ExecutionPlan(
        summary="Fix failing behavior",
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        task_intent="modify",
        deliverable_kind="repository_changes",
        capabilities=[Capability.REPO_WRITE],
        steps=["fix code", "run tests"],
        success_criteria=["tests pass"],
        verification_checks=["unit tests pass"],
        requires_mutation=True,
        must_change_world=True,
        expected_repo_changes=["src/app.py"],
        required_test_levels=["unit"],
        reasoning="Small bounded code repair.",
    )


def _failed_execution() -> ExecutionResult:
    return ExecutionResult(
        request_id="exec_1",
        ok=False,
        execution_status=ExecutionStatus.FAILED,
        summary="unit test failed",
        evidence_text="missing evidence: unit test failed",
    )


def _services(tmp_path, *, llm: ScriptedLLMBackend | None = None, mode: StrategySelectionMode | str = StrategySelectionMode.RULE_BASED, openhands=None):
    store = ArtifactStore(tmp_path)
    return SimpleNamespace(
        artifact_store=store,
        llm_backend=llm or ScriptedLLMBackend({}),
        openhands_adapter=openhands or FakeOpenHandsAdapter(store, scripts={"repair": ["Fixed src/app.py and re-ran unit tests successfully."]}),
        event_sink=None,
        model_routing=None,
        runtime_kernel=RuntimeKernel(),
        strategy_governor=StrategyGovernor(),
        strategy_selection_mode=StrategySelectionMode.coerce(mode),
        strategy_arbitrator=StrategyArbitrator(),
    )


async def test_repair_node_receives_active_strategy_prompt_and_metadata(tmp_path) -> None:
    services = _services(tmp_path)
    nodes = WorkflowStageNodes(services)
    state = WorkflowStateSnapshot(
        task=Task(description="repair failed tests"),
        plan=_plan(),
        execution_result=_failed_execution(),
        active_strategy=StrategyId.REPAIR_ONLY,
    ).to_graph_state()

    update = await nodes.repair_node(state)

    request = services.openhands_adapter.calls["repair"][0]
    assert request.metadata["active_strategy"] == StrategyId.REPAIR_ONLY.value
    assert request.metadata["strategy_description"]
    assert "id: repair_only" in request.prompt
    assert update["active_strategy"] == StrategyId.REPAIR_ONLY.value


async def test_rule_based_mode_skips_llm_advisor_and_uses_baseline(tmp_path) -> None:
    llm = ScriptedLLMBackend({"strategy_advisor": [{"selected_strategy": "safe_refactor", "reason": "would be unused", "confidence": 0.9}]})
    services = _services(tmp_path, llm=llm, mode=StrategySelectionMode.RULE_BASED)
    state = WorkflowStateSnapshot(task=Task(description="implement feature"), plan=_plan()).to_graph_state()

    update = await record_strategy_checkpoint_async(services, state, checkpoint_stage="plan")

    assert update["active_strategy"] == StrategyId.MVP_FIRST.value
    assert llm.calls.get("strategy_advisor", []) == []
    artifact = services.artifact_store.get(update["artifact_ids"][-1])
    payload = services.artifact_store.read_json(artifact.id)
    assert payload["mode"] == StrategySelectionMode.RULE_BASED.value
    assert payload["llm_recommendation"] is None


async def test_hybrid_accepts_valid_llm_recommendation(tmp_path) -> None:
    llm = ScriptedLLMBackend({"strategy_advisor": [{"selected_strategy": "safe_refactor", "reason": "Task text is stabilization/refactor-like.", "confidence": 0.84, "signals_used": ["task_description"], "constraints": ["preserve_existing_runtime_invariants"]}]})
    services = _services(tmp_path, llm=llm, mode=StrategySelectionMode.HYBRID)
    state = WorkflowStateSnapshot(task=Task(description="stabilize runtime internals"), plan=_plan()).to_graph_state()

    update = await record_strategy_checkpoint_async(services, state, checkpoint_stage="plan")

    assert update["active_strategy"] == StrategyId.SAFE_REFACTOR.value
    payload = services.artifact_store.read_json(update["artifact_ids"][-1])
    assert payload["llm_recommendation"]["selected_strategy"] == StrategyId.SAFE_REFACTOR.value
    assert payload["validation_result"]["accepted"] is True
    assert payload["decision"]["selected_strategy"] == StrategyId.SAFE_REFACTOR.value
    assert any(services.artifact_store.get(artifact_id).kind == "strategy_llm_recommendation_raw" for artifact_id in update["artifact_ids"])


async def test_hybrid_falls_back_on_unknown_llm_strategy(tmp_path) -> None:
    llm = ScriptedLLMBackend({"strategy_advisor": [{"selected_strategy": "magic_strategy", "reason": "invented", "confidence": 0.9, "signals_used": ["current_stage"], "constraints": []}]})
    services = _services(tmp_path, llm=llm, mode=StrategySelectionMode.HYBRID)
    state = WorkflowStateSnapshot(task=Task(description="implement feature"), plan=_plan()).to_graph_state()

    update = await record_strategy_checkpoint_async(services, state, checkpoint_stage="plan")

    assert update["active_strategy"] == StrategyId.MVP_FIRST.value
    payload = services.artifact_store.read_json(update["artifact_ids"][-1])
    assert payload["validation_result"]["accepted"] is False
    assert "unknown strategy" in payload["validation_result"]["rejection_reason"]


async def test_hybrid_falls_back_on_invalid_json_or_backend_error(tmp_path) -> None:
    llm = ScriptedLLMBackend({"strategy_advisor": [{"not_the_schema": True}]}, max_contract_repair_attempts=0)
    services = _services(tmp_path, llm=llm, mode=StrategySelectionMode.HYBRID)
    state = WorkflowStateSnapshot(task=Task(description="implement feature"), plan=_plan()).to_graph_state()

    update = await record_strategy_checkpoint_async(services, state, checkpoint_stage="plan")

    assert update["active_strategy"] == StrategyId.MVP_FIRST.value
    payload = services.artifact_store.read_json(update["artifact_ids"][-1])
    assert payload["llm_recommendation"]["advisor_status"] == "invalid_json"
    assert payload["validation_result"]["accepted"] is False


async def test_hard_policy_override_rejects_default_on_failed_execution(tmp_path) -> None:
    llm = ScriptedLLMBackend({"strategy_advisor": [{"selected_strategy": "default", "reason": "try normal flow", "confidence": 0.9, "signals_used": ["execution_status"], "constraints": []}]})
    services = _services(tmp_path, llm=llm, mode=StrategySelectionMode.HYBRID)
    state = WorkflowStateSnapshot(task=Task(description="fix failing execution"), plan=_plan(), execution_result=_failed_execution()).to_graph_state()

    update = await record_strategy_checkpoint_async(services, state, checkpoint_stage="execution_review")

    assert update["active_strategy"] == StrategyId.REPAIR_ONLY.value
    payload = services.artifact_store.read_json(update["artifact_ids"][-1])
    assert payload["validation_result"]["accepted"] is False
    assert payload["validation_result"]["final_strategy"] == StrategyId.REPAIR_ONLY.value
    assert "hard_failure_policy_override" in payload["validation_result"]["policy_notes"]


async def test_final_strategy_decision_serializes_in_snapshot_after_hybrid(tmp_path) -> None:
    llm = ScriptedLLMBackend({"strategy_advisor": [{"selected_strategy": "safe_refactor", "reason": "safe stabilization", "confidence": 0.8, "signals_used": ["task_description"], "constraints": []}]})
    services = _services(tmp_path, llm=llm, mode=StrategySelectionMode.HYBRID)
    snapshot = WorkflowStateSnapshot(task=Task(description="stabilize and refactor runtime"), plan=_plan())

    update = await record_strategy_checkpoint_async(services, snapshot.to_graph_state(), checkpoint_stage="plan")
    restored = WorkflowStateSnapshot.from_graph_state({**snapshot.to_graph_state(), **update})

    assert restored.active_strategy == StrategyId.SAFE_REFACTOR
    assert restored.strategy_decisions[-1].selected_strategy == StrategyId.SAFE_REFACTOR

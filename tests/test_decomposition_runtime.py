from __future__ import annotations

from types import SimpleNamespace

import pytest

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.control_plane import RuntimeKernel
from artifact_workflow_runtime.decomposition import (
    DecompositionComplexity,
    DecompositionPlan,
    ExecutionPacket,
    ExecutionPacketStatus,
    ExecutionPacketType,
)
from artifact_workflow_runtime.done_contract import DoneContract
from artifact_workflow_runtime.llm_backend import ScriptedLLMBackend
from artifact_workflow_runtime.models import (
    Capability,
    ContextPacket,
    ExecutionFamily,
    ExecutionPlan,
    ExecutionResult,
    ObligationAnalysis,
    Task,
    TaskClassification,
)
from artifact_workflow_runtime.models.state import WorkflowStateSnapshot
from artifact_workflow_runtime.openhands_adapter import FakeOpenHandsAdapter
from artifact_workflow_runtime.reports import FinalReportBuilder
from artifact_workflow_runtime.stages import WorkflowStageNodes
from artifact_workflow_runtime.strategy import StrategyArbitrator, StrategyGovernor, StrategyId, StrategySelectionMode

pytestmark = pytest.mark.asyncio


def _plan_payload() -> dict:
    return ExecutionPlan(
        summary="Implement feature x safely",
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        task_intent="implement",
        deliverable_kind="repository_changes",
        capabilities=[Capability.REPO_WRITE],
        steps=["edit src/app.py", "run unit tests"],
        success_criteria=["unit tests pass"],
        verification_checks=["unit tests pass"],
        requires_mutation=True,
        must_change_world=True,
        expected_repo_changes=["src/app.py"],
        required_test_levels=["unit"],
        reasoning="Bounded implementation.",
    ).model_dump(mode="json")


def _services(tmp_path, *, execute_scripts=None, repair_scripts=None):
    store = ArtifactStore(tmp_path)
    scripts = {"execute": list(execute_scripts or ["Changed src/app.py and ran unit tests successfully."])}
    if repair_scripts is not None:
        scripts["repair"] = list(repair_scripts)
    return SimpleNamespace(
        artifact_store=store,
        llm_backend=ScriptedLLMBackend({"planning": [_plan_payload()]}),
        openhands_adapter=FakeOpenHandsAdapter(store, scripts=scripts),
        event_sink=None,
        model_routing=None,
        runtime_kernel=RuntimeKernel(),
        strategy_governor=StrategyGovernor(),
        strategy_selection_mode=StrategySelectionMode.RULE_BASED,
        strategy_arbitrator=StrategyArbitrator(),
        final_report_builder=FinalReportBuilder(),
    )


def _packet(packet_id: str, title: str, packet_type: ExecutionPacketType, *, dependencies=None):
    return ExecutionPacket(
        packet_id=packet_id,
        title=title,
        goal="implement feature x in repo y",
        scope=f"bounded scope for {title}",
        packet_type=packet_type,
        strategy_id=StrategyId.DEFAULT.value,
        dependencies=list(dependencies or []),
        success_criteria=[f"{title} complete"],
        required_evidence=["changed files", "local checks"],
    )


def _merge_state(base: dict, update: dict) -> dict:
    merged = dict(base)
    merged.update(update)
    return merged


def _base_snapshot(*, task: Task, decomposition_plan: DecompositionPlan | None = None, active_strategy: StrategyId = StrategyId.DEFAULT, active_packet_id: str | None = None) -> dict:
    return WorkflowStateSnapshot(
        task=task,
        plan=ExecutionPlan.model_validate(_plan_payload()),
        context_packet=ContextPacket(task_id=task.id, text="repo context"),
        obligations=ObligationAnalysis(required_test_levels=["unit"], reasoning_summary="unit tests required"),
        done_contract=DoneContract(task_id=task.id, primary_goal=task.description),
        decomposition_plan=decomposition_plan,
        active_strategy=active_strategy,
        active_packet_id=active_packet_id,
    ).to_graph_state()


async def test_plan_node_persists_decomposition_plan_and_active_packet(tmp_path) -> None:
    services = _services(tmp_path)
    nodes = WorkflowStageNodes(services)
    task = Task(description="implement feature x in repo y")
    state = WorkflowStateSnapshot(
        task=task,
        classification=TaskClassification(
            normalized_task=task.description,
            needs_world_facts=False,
            execution_family=ExecutionFamily.REPOSITORY_CHANGE,
            task_intent="implement",
            capabilities=[Capability.REPO_WRITE],
            reasoning="repo change",
        ),
        context_packet=ContextPacket(task_id=task.id, text="repo context"),
        obligations=ObligationAnalysis(required_test_levels=["unit"], reasoning_summary="unit tests required"),
        done_contract=DoneContract(task_id=task.id, primary_goal=task.description),
        active_strategy=StrategyId.MVP_FIRST,
    ).to_graph_state()

    update = await nodes.plan_node(state)

    assert update["decomposition_plan"]["strategy_id"] == StrategyId.MVP_FIRST.value
    assert update["active_packet_id"] is not None
    artifacts = [services.artifact_store.get(artifact_id) for artifact_id in update["artifact_ids"]]
    assert any(artifact.kind == "decomposition_plan" for artifact in artifacts)
    assert any(artifact.kind == "packet_selection" for artifact in artifacts)


async def test_execute_node_uses_selected_packet_in_prompt_and_updates_packet_status(tmp_path) -> None:
    services = _services(tmp_path)
    nodes = WorkflowStageNodes(services)
    task = Task(description="implement feature x in repo y")
    state = WorkflowStateSnapshot(
        task=task,
        plan=ExecutionPlan.model_validate(_plan_payload()),
        context_packet=ContextPacket(task_id=task.id, text="repo context"),
        obligations=ObligationAnalysis(required_test_levels=["unit"], reasoning_summary="unit tests required"),
        acceptance_contract=None,
        active_strategy=StrategyId.MVP_FIRST,
    ).to_graph_state()

    update = await nodes.execute_node(state)

    request = services.openhands_adapter.calls["execute"][0]
    assert "ExecutionPacket:" in request.prompt
    assert "minimal working slice" in request.prompt
    assert request.metadata["active_packet_id"] == update["active_packet_id"]
    assert update["decomposition_plan"]["packets"][0]["status"] in {"completed", "failed", "blocked"}
    assert update["packet_history"]
    artifacts = [services.artifact_store.get(artifact_id) for artifact_id in update["artifact_ids"]]
    assert any(artifact.kind == "packet_selection" for artifact in artifacts)
    assert any(artifact.kind == "packet_status_update" for artifact in artifacts)


async def test_runtime_progresses_sequential_packets_and_respects_dependencies(tmp_path) -> None:
    services = _services(tmp_path, execute_scripts=[
        "Changed tests/test_app.py and ran unit tests successfully.",
        "Changed src/app.py and ran unit tests successfully.",
    ])
    nodes = WorkflowStageNodes(services)
    task = Task(description="implement feature x in repo y")
    plan = DecompositionPlan(
        plan_id="plan_seq",
        task_summary=task.description,
        strategy_id=StrategyId.BDD_INCREMENTAL.value,
        complexity=DecompositionComplexity.SMALL,
        packets=[
            _packet("p1", "capture behavior and tests", ExecutionPacketType.TEST),
            _packet("p2", "implement behavior", ExecutionPacketType.IMPLEMENTATION, dependencies=["p1"]),
        ],
        decomposition_reason="bdd progression",
    )
    state = _base_snapshot(task=task, decomposition_plan=plan, active_strategy=StrategyId.BDD_INCREMENTAL)

    exec1 = await nodes.execute_node(state)
    state = _merge_state(state, exec1)
    assert state["active_packet_id"] == "p1"

    review1 = await nodes.review_node(state)
    assert review1["packet_progression"]["selected_next_stage"] == "execute"
    assert review1["packet_progression"]["selected_next_packet_id"] == "p2"
    assert review1["active_packet_id"] == "p2"
    assert nodes.review_next(_merge_state(state, review1)) == "execute"

    state = _merge_state(state, review1)
    exec2 = await nodes.execute_node(state)
    request2 = services.openhands_adapter.calls["execute"][1]
    assert "- id: p2" in request2.prompt
    assert "implement behavior" in request2.prompt


async def test_decomposition_completion_routes_to_qa_only_after_all_packets_complete(tmp_path) -> None:
    services = _services(tmp_path, execute_scripts=[
        "Changed tests/test_app.py and ran unit tests successfully.",
        "Changed src/app.py and ran unit tests successfully.",
    ])
    nodes = WorkflowStageNodes(services)
    task = Task(description="implement feature x in repo y")
    plan = DecompositionPlan(
        plan_id="plan_complete",
        task_summary=task.description,
        strategy_id=StrategyId.DEFAULT.value,
        complexity=DecompositionComplexity.SMALL,
        packets=[
            _packet("p1", "bounded tests", ExecutionPacketType.TEST),
            _packet("p2", "bounded implementation", ExecutionPacketType.IMPLEMENTATION, dependencies=["p1"]),
        ],
        decomposition_reason="sequential",
    )
    state = _base_snapshot(task=task, decomposition_plan=plan, active_strategy=StrategyId.DEFAULT)

    state = _merge_state(state, await nodes.execute_node(state))
    state = _merge_state(state, await nodes.review_node(state))
    assert state["active_packet_id"] == "p2"
    assert nodes.review_next(state) == "execute"

    state = _merge_state(state, await nodes.execute_node(state))
    review2 = await nodes.review_node(state)
    final_state = _merge_state(state, review2)

    assert review2["packet_progression"]["plan_completed"] is True
    assert review2["active_packet_id"] is None
    assert nodes.review_next(final_state) == "qa_plan"


async def test_blocked_packet_path_does_not_become_soft_success(tmp_path) -> None:
    services = _services(tmp_path)
    nodes = WorkflowStageNodes(services)
    task = Task(description="implement feature x in repo y")
    plan = DecompositionPlan(
        plan_id="plan_blocked",
        task_summary=task.description,
        strategy_id=StrategyId.DEFAULT.value,
        complexity=DecompositionComplexity.SMALL,
        packets=[
            _packet("p1", "bounded implementation", ExecutionPacketType.IMPLEMENTATION),
            _packet("p2", "verification", ExecutionPacketType.VERIFICATION, dependencies=["missing_packet"]),
        ],
        decomposition_reason="blocked dependencies",
    )
    # Pretend packet 1 already completed.
    plan = plan.model_copy(update={"packets": [plan.packets[0].model_copy(update={"status": ExecutionPacketStatus.COMPLETED}), plan.packets[1]]})
    state = _base_snapshot(task=task, decomposition_plan=plan, active_strategy=StrategyId.DEFAULT, active_packet_id="p1")
    state["execution_result"] = ExecutionResult(request_id="exec_1", ok=True, summary="Packet 1 finished.", evidence_text="Changed src/app.py and ran unit tests successfully.").model_dump(mode="json")

    review = await nodes.review_node(state)
    reviewed_state = _merge_state(state, review)

    assert review["packet_progression"]["blocked"] is True
    assert nodes.review_next(reviewed_state) == "finalize"

    finalize = await nodes.finalize_node(reviewed_state)
    assert finalize["final_report"]["status"] == "blocked"


async def test_repair_only_packet_lifecycle_progresses_to_completion(tmp_path) -> None:
    services = _services(tmp_path, repair_scripts=["Repaired src/app.py and reran failing unit tests successfully."])
    nodes = WorkflowStageNodes(services)
    task = Task(description="repair feature x in repo y")
    plan = DecompositionPlan(
        plan_id="plan_repair",
        task_summary=task.description,
        strategy_id=StrategyId.REPAIR_ONLY.value,
        complexity=DecompositionComplexity.SMALL,
        packets=[
            _packet("p1", "repair current bounded failure", ExecutionPacketType.REPAIR),
        ],
        decomposition_reason="repair only",
    )
    state = _base_snapshot(task=task, decomposition_plan=plan, active_strategy=StrategyId.REPAIR_ONLY, active_packet_id="p1")
    state["execution_result"] = ExecutionResult(request_id="exec_fail", ok=False, summary="Unit tests failed.", evidence_text="test failure in src/app.py").model_dump(mode="json")

    repair = await nodes.repair_node(state)
    repaired_state = _merge_state(state, repair)
    review = await nodes.review_node(repaired_state)
    final_reviewed = _merge_state(repaired_state, review)

    assert review["packet_progression"]["plan_completed"] is True
    assert review["active_packet_id"] is None
    assert nodes.review_next(final_reviewed) == "qa_plan"

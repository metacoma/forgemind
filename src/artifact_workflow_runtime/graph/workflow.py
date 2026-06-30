from __future__ import annotations

from artifact_workflow_runtime.graph.services import WorkflowServices
from artifact_workflow_runtime.models.state import WorkflowState
from artifact_workflow_runtime.stages import WorkflowStageNodes
from artifact_workflow_runtime.state import wrap_stage_node_with_checkpoint
from .topology import wire_workflow_graph

try:
    from langgraph.graph import END, StateGraph  # type: ignore
except Exception:  # pragma: no cover - exercised when langgraph is absent
    from .compat import END, StateGraph


def build_workflow_graph(services: WorkflowServices):
    nodes = WorkflowStageNodes(services)
    graph = StateGraph(WorkflowState)
    graph.set_entry_point("intake")
    stage_nodes = {
        "intake": nodes.intake_node,
        "classify": nodes.classify_node,
        "route": nodes.route_node,
        "research": nodes.research_node,
        "observe": nodes.observe_node,
        "build_context": nodes.build_context_node,
        "obligations": nodes.obligation_analysis_node,
        "plan": nodes.plan_node,
        "policy": nodes.policy_node,
        "approval": nodes.approval_node,
        "execute": nodes.execute_node,
        "execution_review": nodes.execution_review_node,
        "publish": nodes.publish_node,
        "publish_review": nodes.publish_review_node,
        "repair": nodes.repair_node,
        "verify": nodes.verify_node,
        "acceptance": nodes.acceptance_node,
        "finalize": nodes.finalize_node,
    }
    checkpointed_nodes = {
        name: wrap_stage_node_with_checkpoint(name, node, services.checkpoint_recorder)
        for name, node in stage_nodes.items()
    }
    wire_workflow_graph(
        graph,
        nodes=checkpointed_nodes,
        routers={
            "route": nodes.route_next,
            "research": nodes.research_next,
            "observe": nodes.observe_next,
            "policy": nodes.policy_next,
            "approval": nodes.approval_next,
            "execute": nodes.execute_next,
            "execution_review": nodes.execution_review_next,
            "publish_review": nodes.publish_review_next,
            "verify": nodes.verify_next,
            "acceptance": nodes.acceptance_next,
        },
        end=END,
    )
    return graph.compile()


__all__ = ["WorkflowServices", "build_workflow_graph"]

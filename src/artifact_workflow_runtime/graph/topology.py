from __future__ import annotations

from typing import Any, Callable, Mapping

NodeFn = Callable[..., Any]
RouteFn = Callable[..., str]


PIPELINE_ENTRY_POINTS = (
    "intake",
    "classify",
    "route",
    "research",
    "observe",
    "build_context",
    "obligations",
    "done_contract",
    "plan",
    "policy",
    "approval",
    "workspace_prepare",
    "execute",
    "review",
    "qa_plan",
    "qa_execute",
    "qa_review",
    "repair",
    "acceptance",
    "publish",
    "post_publish_verify",
    "finalize",
)


PIPELINE_NODE_ORDER = (
    "intake",
    "classify",
    "route",
    "research",
    "observe",
    "build_context",
    "obligations",
    "done_contract",
    "plan",
    "policy",
    "approval",
    "workspace_prepare",
    "execute",
    "review",
    "qa_plan",
    "qa_execute",
    "qa_review",
    "repair",
    "acceptance",
    "publish",
    "post_publish_verify",
    "finalize",
)


def wire_workflow_graph(graph: Any, *, nodes: Mapping[str, NodeFn], routers: Mapping[str, RouteFn], end: object) -> Any:
    graph.add_node("dispatch", nodes["dispatch"])
    for name in PIPELINE_NODE_ORDER:
        graph.add_node(name, nodes[name])

    graph.add_conditional_edges("dispatch", routers["dispatch"], {name: name for name in PIPELINE_ENTRY_POINTS})
    graph.add_edge("intake", "classify")
    graph.add_edge("classify", "route")
    graph.add_conditional_edges("route", routers["route"], {"research": "research", "observe": "observe", "build_context": "build_context"})
    graph.add_conditional_edges("research", routers["research"], {"observe": "observe", "build_context": "build_context", "finalize": "finalize"})
    graph.add_conditional_edges("observe", routers["observe"], {"research": "research", "build_context": "build_context", "finalize": "finalize"})
    graph.add_edge("build_context", "obligations")
    graph.add_edge("obligations", "done_contract")
    graph.add_edge("done_contract", "plan")
    graph.add_edge("plan", "policy")
    graph.add_conditional_edges("policy", routers["policy"], {"approval": "approval", "workspace_prepare": "workspace_prepare", "finalize": "finalize"})
    graph.add_conditional_edges("approval", routers["approval"], {"workspace_prepare": "workspace_prepare", "finalize": "finalize"})
    graph.add_edge("workspace_prepare", "execute")
    graph.add_conditional_edges("execute", routers["execute"], {"review": "review"})
    graph.add_conditional_edges("review", routers["review"], {"execute": "execute", "qa_plan": "qa_plan", "repair": "repair", "finalize": "finalize"})
    graph.add_edge("qa_plan", "qa_execute")
    graph.add_edge("qa_execute", "qa_review")
    graph.add_conditional_edges("qa_review", routers["qa_review"], {"acceptance": "acceptance", "repair": "repair", "finalize": "finalize", "research": "research", "observe": "observe", "build_context": "build_context", "obligations": "obligations", "plan": "plan"})
    graph.add_edge("repair", "review")
    graph.add_conditional_edges("acceptance", routers["acceptance"], {"publish": "publish", "finalize": "finalize", "research": "research", "observe": "observe", "build_context": "build_context", "obligations": "obligations", "plan": "plan"})
    graph.add_edge("publish", "post_publish_verify")
    graph.add_conditional_edges("post_publish_verify", routers["post_publish_verify"], {"repair": "repair", "finalize": "finalize"})
    graph.add_edge("finalize", end)
    return graph

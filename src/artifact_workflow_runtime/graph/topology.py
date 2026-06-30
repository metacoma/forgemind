from __future__ import annotations

from typing import Any, Callable, Mapping

NodeFn = Callable[..., Any]
RouteFn = Callable[..., str]


PIPELINE_NODE_ORDER = (
    "intake",
    "classify",
    "route",
    "research",
    "observe",
    "build_context",
    "obligations",
    "plan",
    "policy",
    "approval",
    "execute",
    "execution_review",
    "publish",
    "publish_review",
    "repair",
    "verify",
    "acceptance",
    "finalize",
)


def wire_workflow_graph(graph: Any, *, nodes: Mapping[str, NodeFn], routers: Mapping[str, RouteFn], end: object) -> Any:
    """Register the canonical runtime topology on an already-created graph.

    Stage implementations live in workflow/stage modules; this file owns the
    logical graph shape. Keeping topology separate makes it easier to review
    gates and re-entry edges without reading node business logic.
    """

    for name in PIPELINE_NODE_ORDER:
        graph.add_node(name, nodes[name])

    graph.add_edge("intake", "classify")
    graph.add_edge("classify", "route")
    graph.add_conditional_edges("route", routers["route"], {"research": "research", "observe": "observe", "build_context": "build_context"})
    graph.add_conditional_edges("research", routers["research"], {"observe": "observe", "build_context": "build_context", "finalize": "finalize"})
    graph.add_conditional_edges("observe", routers["observe"], {"build_context": "build_context", "finalize": "finalize"})
    graph.add_edge("build_context", "obligations")
    graph.add_edge("obligations", "plan")
    graph.add_edge("plan", "policy")
    graph.add_conditional_edges("policy", routers["policy"], {"approval": "approval", "execute": "execute", "finalize": "finalize"})
    graph.add_conditional_edges("approval", routers["approval"], {"execute": "execute", "finalize": "finalize"})
    graph.add_conditional_edges("execute", routers["execute"], {"execution_review": "execution_review"})
    graph.add_conditional_edges("execution_review", routers["execution_review"], {"publish": "publish", "verify": "verify", "acceptance": "acceptance", "finalize": "finalize"})
    graph.add_edge("publish", "publish_review")
    graph.add_conditional_edges("publish_review", routers["publish_review"], {"repair": "repair", "verify": "verify", "acceptance": "acceptance", "finalize": "finalize", "research": "research", "observe": "observe", "build_context": "build_context", "obligations": "obligations", "plan": "plan"})
    graph.add_edge("repair", "execution_review")
    graph.add_conditional_edges("verify", routers["verify"], {"acceptance": "acceptance", "research": "research", "observe": "observe", "build_context": "build_context", "obligations": "obligations", "plan": "plan", "finalize": "finalize"})
    graph.add_conditional_edges("acceptance", routers["acceptance"], {"publish": "publish", "finalize": "finalize", "research": "research", "observe": "observe", "build_context": "build_context", "obligations": "obligations", "plan": "plan"})
    graph.add_edge("finalize", end)
    return graph

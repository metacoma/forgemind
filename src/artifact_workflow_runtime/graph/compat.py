from __future__ import annotations

from typing import Any, Callable

END = "__end__"


class CompiledStateGraph:
    def __init__(self, graph: "StateGraph") -> None:
        self.graph = graph

    async def ainvoke(self, initial_state: dict[str, Any]) -> dict[str, Any]:
        state = dict(initial_state)
        node_name = self.graph.entry_point
        if not node_name:
            raise RuntimeError("StateGraph entry point is not set")
        while node_name != END:
            node = self.graph.nodes[node_name]
            update = await node(state)
            if update:
                state.update(update)
            edge = self.graph.conditional_edges.get(node_name)
            if edge is not None:
                selector, mapping = edge
                selected = selector(state)
                node_name = mapping[selected]
            else:
                node_name = self.graph.edges.get(node_name, END)
        return state


class StateGraph:
    def __init__(self, _state_type: object | None = None) -> None:
        self.nodes: dict[str, Callable[[dict[str, Any]], Any]] = {}
        self.edges: dict[str, str] = {}
        self.conditional_edges: dict[str, tuple[Callable[[dict[str, Any]], str], dict[str, str]]] = {}
        self.entry_point: str | None = None

    def add_node(self, name: str, func: Callable[[dict[str, Any]], Any]) -> None:
        self.nodes[name] = func

    def set_entry_point(self, name: str) -> None:
        self.entry_point = name

    def add_edge(self, source: str, target: str) -> None:
        self.edges[source] = target

    def add_conditional_edges(self, source: str, selector: Callable[[dict[str, Any]], str], mapping: dict[str, str]) -> None:
        self.conditional_edges[source] = (selector, mapping)

    def compile(self) -> CompiledStateGraph:
        return CompiledStateGraph(self)

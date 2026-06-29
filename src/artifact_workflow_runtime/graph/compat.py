from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

END = "__end__"


@dataclass
class _CompiledGraph:
    nodes: dict[str, Callable[[dict[str, Any]], Any]]
    edges: dict[str, str]
    conditional_edges: dict[str, tuple[Callable[[dict[str, Any]], str], dict[str, str]]]
    entry_point: str

    async def ainvoke(self, state: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
        current = self.entry_point
        data = dict(state)
        while current != END:
            node = self.nodes[current]
            update = await node(data)
            if isinstance(update, dict):
                data.update(update)
            if current in self.conditional_edges:
                chooser, mapping = self.conditional_edges[current]
                branch = chooser(data)
                current = mapping[branch]
            else:
                current = self.edges.get(current, END)
        return data


class StateGraph:
    def __init__(self, _state_type: object | None = None) -> None:
        self.nodes: dict[str, Callable[[dict[str, Any]], Any]] = {}
        self.edges: dict[str, str] = {}
        self.conditional_edges: dict[str, tuple[Callable[[dict[str, Any]], str], dict[str, str]]] = {}
        self.entry_point: str | None = None

    def add_node(self, name: str, fn: Callable[[dict[str, Any]], Any]) -> None:
        self.nodes[name] = fn

    def set_entry_point(self, name: str) -> None:
        self.entry_point = name

    def add_edge(self, source: str, target: str) -> None:
        self.edges[source] = target

    def add_conditional_edges(self, source: str, chooser: Callable[[dict[str, Any]], str], mapping: dict[str, str]) -> None:
        self.conditional_edges[source] = (chooser, mapping)

    def compile(self) -> _CompiledGraph:
        if self.entry_point is None:
            raise RuntimeError("entry point is not set")
        return _CompiledGraph(self.nodes, self.edges, self.conditional_edges, self.entry_point)

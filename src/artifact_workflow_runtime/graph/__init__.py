from __future__ import annotations

from .services import WorkflowServices


def build_workflow_graph(*args, **kwargs):
    from .workflow import build_workflow_graph as _build_workflow_graph

    return _build_workflow_graph(*args, **kwargs)


__all__ = ["WorkflowServices", "build_workflow_graph"]

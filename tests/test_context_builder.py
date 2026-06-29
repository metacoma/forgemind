from __future__ import annotations

from artifact_workflow_runtime.context import ContextBuilder
from artifact_workflow_runtime.models import Artifact, Task


def test_context_builder_includes_task_and_artifacts() -> None:
    builder = ContextBuilder()
    task = Task(description="Inspect repo")
    artifact = Artifact(kind="observation_evidence", path="/tmp/x.txt", text_preview="repo has failing tests")
    packet = builder.build(task, [artifact])
    assert packet.task_id == task.id
    assert "Inspect repo" in packet.text
    assert "repo has failing tests" in packet.text

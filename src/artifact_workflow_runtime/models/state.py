from __future__ import annotations

from typing import Any, TypedDict

JsonDict = dict[str, Any]


class WorkflowState(TypedDict, total=False):
    task: JsonDict
    task_artifact: JsonDict | None
    classification_request: JsonDict | None
    classification_result: JsonDict | None
    classification: JsonDict | None
    observation_request: JsonDict | None
    observation_result: JsonDict | None
    context_packet: JsonDict | None
    plan_request: JsonDict | None
    plan_result: JsonDict | None
    plan: JsonDict | None
    policy_decision: JsonDict | None
    approval_request: JsonDict | None
    execution_request: JsonDict | None
    execution_result: JsonDict | None
    verification_request: JsonDict | None
    verification_result: JsonDict | None
    final_report: JsonDict | None
    artifact_ids: list[str]
    status: str
    errors: list[str]

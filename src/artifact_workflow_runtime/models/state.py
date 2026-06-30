from __future__ import annotations

from typing import Any, TypedDict

JsonDict = dict[str, Any]


class WorkflowState(TypedDict, total=False):
    task: JsonDict
    task_artifact: JsonDict | None
    classification_request: JsonDict | None
    classification_result: JsonDict | None
    classification: JsonDict | None
    route_request: JsonDict | None
    route_result: JsonDict | None
    route_decision: JsonDict | None
    research_request: JsonDict | None
    research_result: JsonDict | None
    observation_request: JsonDict | None
    observation_result: JsonDict | None
    context_packet: JsonDict | None
    obligation_request: JsonDict | None
    obligation_result: JsonDict | None
    obligations: JsonDict | None
    plan_request: JsonDict | None
    plan_result: JsonDict | None
    plan: JsonDict | None
    policy_decision: JsonDict | None
    approval_request: JsonDict | None
    execution_request: JsonDict | None
    execution_result: JsonDict | None
    publish_request: JsonDict | None
    publish_result: JsonDict | None
    verification_request: JsonDict | None
    verification_result: JsonDict | None
    final_report: JsonDict | None
    artifact_ids: list[str]
    status: str
    errors: list[str]

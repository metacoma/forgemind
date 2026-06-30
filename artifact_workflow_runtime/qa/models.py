from __future__ import annotations

from pydantic import Field

from artifact_workflow_runtime.models.base import RuntimeModel, new_id, utc_now


class QACheck(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("qa_check"))
    name: str
    kind: str = "evidence_review"
    command: str | None = None
    required: bool = True
    reason: str = ""


class QAPlan(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("qa_plan"))
    task_id: str
    checks: list[QACheck] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)


class QAExecutionItem(RuntimeModel):
    check_id: str
    name: str
    kind: str
    status: str
    command: str | None = None
    exit_code: int | None = None
    output: str = ""
    reason: str = ""


class QAExecutionReport(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("qa_exec"))
    task_id: str
    plan_id: str
    items: list[QAExecutionItem] = Field(default_factory=list)
    summary: str = ""
    created_at: str = Field(default_factory=utc_now)


class QAReview(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("qa_review"))
    task_id: str
    status: str
    summary: str
    failing_checks: list[str] = Field(default_factory=list)
    environment_blockers: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)

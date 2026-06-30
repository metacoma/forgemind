from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from artifact_workflow_runtime.models import JsonDict, new_id, utc_now


class ContractViolationSeverity(str, Enum):
    REPAIRABLE = "repairable"
    FATAL = "fatal"


class ContractViolation(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    path: str
    message: str
    expected: str | None = None
    actual: Any = None
    severity: ContractViolationSeverity = ContractViolationSeverity.REPAIRABLE


class ContractSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, arbitrary_types_allowed=True)

    schema_id: str
    schema_version: str = "1"
    response_model_name: str
    json_schema: JsonDict
    strict: bool = True
    max_repair_attempts: int = 1
    repair_instruction: str = (
        "Return the same semantic answer corrected to the JSON schema. "
        "Do not add prose, markdown, or new task assumptions."
    )


class ContractResult(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(default_factory=lambda: new_id("contract"))
    schema_id: str
    schema_version: str = "1"
    ok: bool
    repaired: bool = False
    repair_attempts: int = 0
    raw_payload: Any = None
    validated_payload: JsonDict | None = None
    violations: list[ContractViolation] = Field(default_factory=list)
    repair_raw_texts: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)

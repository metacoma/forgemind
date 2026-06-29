from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone

from artifact_workflow_runtime.models import ApprovalRequest


class ApprovalProvider(ABC):
    @abstractmethod
    async def review(self, request: ApprovalRequest) -> ApprovalRequest:
        raise NotImplementedError


class StaticApprovalProvider(ApprovalProvider):
    def __init__(self, approve: bool, reviewer: str = "static-policy") -> None:
        self.approve = approve
        self.reviewer = reviewer

    async def review(self, request: ApprovalRequest) -> ApprovalRequest:
        return request.model_copy(update={
            "approved": self.approve,
            "reviewer": self.reviewer,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        })

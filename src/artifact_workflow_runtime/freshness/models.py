from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field

from artifact_workflow_runtime.models import RuntimeModel, new_id, utc_now

JsonDict = dict[str, Any]


class RetrievalMode(str, Enum):
    NONE = "none"
    DOCS = "docs"
    VERSIONS = "versions"
    CHANGELOG = "changelog"
    WEB = "web"
    DOCS_PLUS_VERSIONS = "docs_plus_versions"




class FreshnessStagePreference(str, Enum):
    IMMEDIATE = "immediate"
    AFTER_OBSERVE = "after_observe"
    PACKET_SCOPED = "packet_scoped"

class RetrievalSourceKind(str, Enum):
    OFFICIAL_DOCUMENTATION = "official_documentation"
    OFFICIAL_RELEASE_NOTES = "official_release_notes"
    OFFICIAL_GITHUB_RELEASES = "official_github_releases"
    OFFICIAL_PACKAGE_REGISTRY = "official_package_registry"
    ISSUE_TRACKER_OR_DISCUSSION = "issue_tracker_or_discussion"
    THIRD_PARTY_ARTICLE = "third_party_article"
    GENERAL_WEB = "general_web"
    UNKNOWN = "unknown"


class SourcePreference(RuntimeModel):
    source_kind: RetrievalSourceKind
    rank: int
    reason: str


class FreshnessDecision(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("freshness"))
    freshness_required: bool = False
    retrieval_mode: RetrievalMode = RetrievalMode.NONE
    retrieval_reason: str = "No freshness-sensitive indicators were detected."
    preferred_sources: list[SourcePreference] = Field(default_factory=list)
    version_resolution_required: bool = False
    docs_resolution_required: bool = False
    changelog_resolution_required: bool = False
    web_resolution_required: bool = False
    stage_preference: FreshnessStagePreference = FreshnessStagePreference.IMMEDIATE
    targets: list[str] = Field(default_factory=list)
    triggered_by: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)

    @property
    def retrieval_artifact_kinds(self) -> list[str]:
        kinds = ["retrieval_summary", "retrieval_sources"]
        if self.docs_resolution_required:
            kinds.append("docs_snapshot")
        if self.version_resolution_required:
            kinds.append("version_resolution")
        if self.changelog_resolution_required:
            kinds.append("release_notes_snapshot")
        return kinds


class RetrievalSource(RuntimeModel):
    title: str = ""
    url: str | None = None
    source_kind: RetrievalSourceKind = RetrievalSourceKind.UNKNOWN
    rank: int = 999
    official: bool = False
    summary: str = ""
    evidence_artifact_ids: list[str] = Field(default_factory=list)


class VersionResolution(RuntimeModel):
    target: str
    latest_stable_version: str | None = None
    recommended_version: str | None = None
    compatible_version: str | None = None
    pinned_version: str | None = None
    breaking_major_upgrade: bool = False
    migration_notes: list[str] = Field(default_factory=list)
    source_artifact_ids: list[str] = Field(default_factory=list)
    confidence: str = "low"


class RetrievalSnapshot(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("retrieval"))
    task_id: str
    decision_id: str
    retrieval_mode: RetrievalMode
    freshness_required: bool
    summary: str
    facts: list[str] = Field(default_factory=list)
    sources: list[RetrievalSource] = Field(default_factory=list)
    version_resolutions: list[VersionResolution] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    metadata: JsonDict = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)

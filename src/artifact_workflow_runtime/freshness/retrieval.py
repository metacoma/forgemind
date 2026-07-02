from __future__ import annotations

import json
import re
from urllib.parse import urlparse

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.models import ObservationRequest, ObservationResult, Task, TaskClassification, WorkPacketKind

from .models import FreshnessDecision, RetrievalMode, RetrievalSnapshot, RetrievalSource, RetrievalSourceKind, VersionResolution

_SOURCE_POLICY_TEXT = """Source preference policy, in priority order:
1. official documentation
2. official release notes / changelog
3. official GitHub releases / tags
4. official package registry / version source
5. issue tracker / discussions
6. third-party articles only as fallback

Do not treat a blog post as official docs. Do not treat a random article as release notes. Do not let community answers override official compatibility matrices.
""".strip()

_VERSION_RE = re.compile(r"(?<![A-Za-z0-9])v?(\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9_.-]+)?)")


class RetrievalService:
    """Thin retrieval boundary around the real OpenHands observation backend.

    The service does not implement a search engine. It turns a control-plane
    FreshnessDecision into a bounded retrieval packet and normalizes the returned
    evidence into durable retrieval artifacts that downstream stages can trust.
    """

    def build_request(self, *, task: Task, classification: TaskClassification, decision: FreshnessDecision) -> ObservationRequest:
        targets = "\n".join(f"- {target}" for target in decision.targets) or "- infer the minimum target names from the task text"
        source_policy = "\n".join(f"{pref.rank}. {pref.source_kind.value}: {pref.reason}" for pref in decision.preferred_sources)
        required_snapshots = "\n".join(f"- {kind}" for kind in decision.retrieval_artifact_kinds)
        prompt = (
            "You are executing a bounded freshness/retrieval packet for a controller-driven workflow.\n"
            "This is not planning and not implementation. Do not edit files, mutate hosts/clusters, commit, push, publish, or decide workflow success.\n"
            "Resolve current external facts that are unsafe to answer from stale model memory.\n"
            "Use official sources first and include source attribution for every resolved fact.\n\n"
            f"Task:\n{task.description}\n\n"
            f"Retrieval mode: {decision.retrieval_mode.value}\n"
            f"Retrieval reason: {decision.retrieval_reason}\n"
            f"Version resolution required: {decision.version_resolution_required}\n"
            f"Docs resolution required: {decision.docs_resolution_required}\n"
            f"Changelog resolution required: {decision.changelog_resolution_required}\n\n"
            f"Targets:\n{targets}\n\n"
            "Preferred source policy:\n"
            f"{source_policy}\n\n"
            "Required output categories:\n"
            f"{required_snapshots}\n\n"
            "Return concise source-backed facts only. Include official URLs/titles where possible, resolved versions, compatibility notes, breaking changes, migration notes, unknowns, and blockers.\n"
            "Do not dump raw search results. Summarize the few facts needed for plan/execute/verify grounding."
        )
        return ObservationRequest(
            task_id=task.id,
            execution_family=classification.execution_family,
            capabilities=[],
            prompt=prompt,
            objective="collect fresh source-backed docs/version/changelog facts without mutation",
            focus=list(decision.targets),
            required_facts=list(decision.retrieval_artifact_kinds),
            scope_constraints=["freshness retrieval only", "official sources preferred", "observe only", "do not produce a plan"],
            work_packet_kind=WorkPacketKind.RESEARCH,
            allowed_actions=["internet_research", "read_official_docs", "inspect_release_notes", "inspect_package_registry", "inspect_public_metadata", "collect_source_attribution"],
            forbidden_actions=["edit_files", "write_files", "run_mutating_commands", "commit", "push", "git push", "git push --force", "git tag", "git merge", "git rebase", "create_pr", "open_pull_request", "publish", "release", "change_hosts", "change_cluster_state", "change_workflow_decision", "declare_task_completed_or_accepted"],
            expected_outputs=["retrieval_summary", "source_urls", "version_resolution", "docs_snapshot", "release_notes", "blockers", "unknowns"],
            metadata={
                "mode": "freshness_retrieval",
                "freshness_decision_id": decision.id,
                "retrieval_mode": decision.retrieval_mode.value,
                "source_policy": "official_sources_first",
                "evidence_required": True,
            },
        )

    def persist_decision(self, *, artifact_store: ArtifactStore, task: Task, decision: FreshnessDecision) -> object:
        return artifact_store.add_json(
            "freshness_decision",
            decision.model_dump(mode="json"),
            metadata={"task_id": task.id, "freshness_required": decision.freshness_required, "retrieval_mode": decision.retrieval_mode.value},
        )

    def normalize_result(self, *, artifact_store: ArtifactStore, task: Task, decision: FreshnessDecision, result: ObservationResult) -> tuple[RetrievalSnapshot, list[object]]:
        facts = self._facts_from_result(result)
        sources = self._sources_from_result(result)
        blockers = [item.summary for item in result.structured_evidence.blockers]
        version_resolutions = self._version_resolutions(decision=decision, facts=facts, raw=result.evidence_text, source_artifact_ids=[artifact.id for artifact in result.artifacts])
        summary = self._summary(decision=decision, facts=facts, sources=sources, version_resolutions=version_resolutions, blockers=blockers)

        artifacts: list[object] = []
        source_artifact = artifact_store.add_json(
            "retrieval_sources",
            {
                "source_policy": _SOURCE_POLICY_TEXT,
                "preferred_sources": [pref.model_dump(mode="json") for pref in decision.preferred_sources],
                "sources": [source.model_dump(mode="json") for source in sources],
            },
            metadata={"task_id": task.id, "freshness_decision_id": decision.id, "retrieval_mode": decision.retrieval_mode.value},
        )
        artifacts.append(source_artifact)
        if decision.docs_resolution_required:
            artifacts.append(artifact_store.add_json(
                "docs_snapshot",
                {"facts": facts, "sources": [source.model_dump(mode="json") for source in sources if source.source_kind == RetrievalSourceKind.OFFICIAL_DOCUMENTATION]},
                metadata={"task_id": task.id, "freshness_decision_id": decision.id},
            ))
        if decision.version_resolution_required:
            artifacts.append(artifact_store.add_json(
                "version_resolution",
                [item.model_dump(mode="json") for item in version_resolutions],
                metadata={"task_id": task.id, "freshness_decision_id": decision.id},
            ))
        if decision.changelog_resolution_required:
            artifacts.append(artifact_store.add_json(
                "release_notes_snapshot",
                {"facts": facts, "sources": [source.model_dump(mode="json") for source in sources if source.source_kind in {RetrievalSourceKind.OFFICIAL_RELEASE_NOTES, RetrievalSourceKind.OFFICIAL_GITHUB_RELEASES}]},
                metadata={"task_id": task.id, "freshness_decision_id": decision.id},
            ))
        summary_artifact = artifact_store.add_text(
            "retrieval_summary",
            summary,
            metadata={"task_id": task.id, "freshness_decision_id": decision.id, "retrieval_mode": decision.retrieval_mode.value},
        )
        artifacts.append(summary_artifact)
        artifact_ids = [artifact.id for artifact in result.artifacts] + [artifact.id for artifact in artifacts]
        snapshot = RetrievalSnapshot(
            task_id=task.id,
            decision_id=decision.id,
            retrieval_mode=decision.retrieval_mode,
            freshness_required=decision.freshness_required,
            summary=summary,
            facts=facts,
            sources=sources,
            version_resolutions=version_resolutions,
            blockers=blockers,
            artifact_ids=artifact_ids,
            metadata={"source_policy": "official_sources_first", "retrieval_mode": decision.retrieval_mode.value},
        )
        snapshot_artifact = artifact_store.add_json(
            "retrieval_snapshot",
            snapshot.model_dump(mode="json"),
            metadata={"task_id": task.id, "freshness_decision_id": decision.id, "retrieval_mode": decision.retrieval_mode.value},
        )
        artifacts.append(snapshot_artifact)
        snapshot.artifact_ids.append(snapshot_artifact.id)
        return snapshot, artifacts

    @staticmethod
    def _facts_from_result(result: ObservationResult) -> list[str]:
        facts: list[str] = []
        for item in result.structured_evidence.extracted_facts:
            text = f"{item.subject}: {item.fact}".strip(": ")
            if item.source:
                text += f" (source: {item.source})"
            facts.append(text)
        if not facts and result.summary:
            facts.append(result.summary)
        return _dedupe(facts)[:40]

    @staticmethod
    def _sources_from_result(result: ObservationResult) -> list[RetrievalSource]:
        sources: list[RetrievalSource] = []
        for item in result.structured_evidence.extracted_facts:
            source_text = str(item.source or "").strip()
            if not source_text:
                continue
            sources.append(_source_from_text(source_text, summary=f"{item.subject}: {item.fact}", evidence_artifact_ids=[artifact.id for artifact in result.artifacts]))
        # Also scan raw evidence for URLs when structured facts did not capture source fields.
        for url in re.findall(r"https?://[^\s)\]>\"']+", result.evidence_text or ""):
            sources.append(_source_from_text(url, summary="Source URL found in retrieval evidence.", evidence_artifact_ids=[artifact.id for artifact in result.artifacts]))
        return sorted(_dedupe_sources(sources), key=lambda item: item.rank)[:20]

    @staticmethod
    def _version_resolutions(*, decision: FreshnessDecision, facts: list[str], raw: str, source_artifact_ids: list[str]) -> list[VersionResolution]:
        if not decision.version_resolution_required:
            return []
        text = "\n".join([*facts, raw or ""])
        versions = _dedupe(_VERSION_RE.findall(text))
        targets = decision.targets or ["requested dependency/tool"]
        resolutions: list[VersionResolution] = []
        for target in targets[:8]:
            version = versions[0] if versions else None
            resolutions.append(VersionResolution(
                target=target,
                latest_stable_version=version,
                recommended_version=version,
                compatible_version=None,
                pinned_version=None,
                breaking_major_upgrade=bool(decision.changelog_resolution_required),
                migration_notes=[fact for fact in facts if any(term in fact.lower() for term in ("breaking", "migration", "deprecated", "deprecation"))][:5],
                source_artifact_ids=list(source_artifact_ids),
                confidence="medium" if version else "low",
            ))
        return resolutions

    @staticmethod
    def _summary(*, decision: FreshnessDecision, facts: list[str], sources: list[RetrievalSource], version_resolutions: list[VersionResolution], blockers: list[str]) -> str:
        parts = [
            "Freshness retrieval summary",
            f"decision_id: {decision.id}",
            f"mode: {decision.retrieval_mode.value}",
            f"reason: {decision.retrieval_reason}",
            "Use this retrieval snapshot as the truth layer for current docs, versions, changelog, CLI flags, compatibility, and migration-sensitive planning/execution/verification.",
        ]
        if version_resolutions:
            parts.append("Resolved versions:")
            for item in version_resolutions:
                resolved = item.recommended_version or item.latest_stable_version or item.compatible_version or "unresolved"
                parts.append(f"- {item.target}: {resolved} (confidence={item.confidence})")
        if facts:
            parts.append("Source-backed facts:")
            parts.extend(f"- {fact}" for fact in facts[:12])
        if sources:
            parts.append("Sources by priority:")
            parts.extend(f"- rank {source.rank}: {source.source_kind.value} {source.url or source.title}" for source in sources[:8])
        if blockers:
            parts.append("Retrieval blockers:")
            parts.extend(f"- {blocker}" for blocker in blockers)
        parts.append("Source policy: official docs/release notes/GitHub releases/package registry outrank issues/discussions and third-party articles.")
        return "\n".join(parts)


def _source_from_text(text: str, *, summary: str, evidence_artifact_ids: list[str]) -> RetrievalSource:
    url = text if text.startswith("http://") or text.startswith("https://") else None
    source_kind, rank, official = _classify_source(text)
    return RetrievalSource(title=text if not url else "", url=url, source_kind=source_kind, rank=rank, official=official, summary=summary, evidence_artifact_ids=evidence_artifact_ids)


def _classify_source(text: str) -> tuple[RetrievalSourceKind, int, bool]:
    lowered = text.lower()
    host = urlparse(text).netloc.lower() if text.startswith(("http://", "https://")) else ""
    official_doc_markers = ("docs.", "/docs", "documentation", "developer.", "kubernetes.io", "helm.sh", "argo-cd.readthedocs.io", "docs.github.com", "docs.docker.com", "terraform.io", "opentofu.org")
    if any(marker in lowered or marker in host for marker in official_doc_markers):
        return RetrievalSourceKind.OFFICIAL_DOCUMENTATION, 1, True
    if any(marker in lowered for marker in ("changelog", "release notes", "/releases", "github.com")):
        return RetrievalSourceKind.OFFICIAL_RELEASE_NOTES if "release" in lowered or "changelog" in lowered else RetrievalSourceKind.OFFICIAL_GITHUB_RELEASES, 2, "github.com" in host or "official" in lowered
    if any(marker in host for marker in ("pypi.org", "npmjs.com", "crates.io", "mvnrepository.com", "pkg.go.dev", "rubygems.org", "packagist.org")):
        return RetrievalSourceKind.OFFICIAL_PACKAGE_REGISTRY, 4, True
    if any(marker in lowered for marker in ("issue", "discussion", "stackoverflow", "serverfault")):
        return RetrievalSourceKind.ISSUE_TRACKER_OR_DISCUSSION, 5, False
    if text.startswith(("http://", "https://")):
        return RetrievalSourceKind.GENERAL_WEB, 6, False
    return RetrievalSourceKind.UNKNOWN, 999, False


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        out.append(text)
    return out


def _dedupe_sources(sources: list[RetrievalSource]) -> list[RetrievalSource]:
    seen: set[str] = set()
    out: list[RetrievalSource] = []
    for source in sources:
        key = (source.url or source.title or source.summary).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(source)
    return out

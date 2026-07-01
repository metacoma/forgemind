from __future__ import annotations

import re
from collections.abc import Iterable

from artifact_workflow_runtime.models import RoutingDecision, Task, TaskClassification

from .models import FreshnessDecision, FreshnessStagePreference, RetrievalMode, RetrievalSourceKind, SourcePreference

_VERSION_TERMS = (
    "latest",
    "current version",
    "current stable",
    "stable version",
    "recommended version",
    "supported version",
    "version",
    "versions",
    "pin",
    "pinned",
    "upgrade",
    "compatibility matrix",
    "compatible version",
    "release",
    "releases",
)
_DOC_TERMS = (
    "docs",
    "documentation",
    "official docs",
    "current docs",
    "api docs",
    "sdk",
    "api",
    "cli flag",
    "cli flags",
    "command syntax",
    "examples",
    "best practice",
    "best practices",
)
_CHANGELOG_TERMS = (
    "changelog",
    "change log",
    "release notes",
    "breaking change",
    "breaking changes",
    "migration",
    "migrate",
    "deprecated",
    "deprecation",
)
_WEB_TERMS = (
    "current",
    "supported",
    "recommended",
    "today",
    "now",
    "recent",
)
_TOOL_TERMS = (
    "github actions",
    "actions/checkout",
    "actions/setup-node",
    "actions/setup-python",
    "docker compose",
    "compose",
    "kubernetes",
    "k8s",
    "helm",
    "argocd",
    "argo cd",
    "terraform",
    "opentofu",
    "tofu",
    "ansible",
    "kubectl",
    "kustomize",
    "kubespray",
    "gradle",
    "maven",
    "npm",
    "pip",
    "poetry",
    "uv",
    "go module",
    "golang",
    "rust",
    "cargo",
    "python",
    "node",
)
_ROUTE_VERSION_TYPES = {"package_versions", "current_versions", "version_resolution", "compatibility_matrix"}
_ROUTE_DOC_TYPES = {"official_docs", "api_examples", "documentation", "current_docs"}
_ROUTE_CHANGELOG_TYPES = {"release_notes", "changelog", "migration_notes", "breaking_changes"}


class FreshnessGate:
    """Deterministic control-plane gate for stale-model risk.

    The route LLM may still propose research, but this gate is the authority for
    freshness-sensitive work. Its decision is persisted and later stages receive
    retrieval artifacts as the grounding layer instead of relying on model memory.
    """

    def decide(self, task: Task, classification: TaskClassification | None = None, route: RoutingDecision | None = None) -> FreshnessDecision:
        text = _normalize_text("\n".join(filter(None, [task.title or "", task.description])))
        route_evidence = {str(item).strip().lower() for item in (route.required_evidence_types if route else [])}
        route_targets = [str(item).strip() for item in (route.research_targets if route else []) if str(item).strip()]

        triggered: list[str] = []
        docs_required = _contains_any(text, _DOC_TERMS) or bool(route_evidence & _ROUTE_DOC_TYPES)
        versions_required = _contains_any(text, _VERSION_TERMS) or bool(route_evidence & _ROUTE_VERSION_TYPES)
        changelog_required = _contains_any(text, _CHANGELOG_TERMS) or bool(route_evidence & _ROUTE_CHANGELOG_TYPES)
        tooling_sensitive = _contains_any(text, _TOOL_TERMS)
        web_required = _contains_any(text, _WEB_TERMS) or bool(route and route.needs_fresh_external_research)

        if docs_required:
            triggered.append("documentation_lookup")
        if versions_required:
            triggered.append("version_resolution")
        if changelog_required:
            triggered.append("changelog_resolution")
        if tooling_sensitive:
            triggered.append("tooling_current_syntax")
            docs_required = True
            versions_required = True
        if route and route.needs_fresh_external_research:
            triggered.append("route_requested_fresh_external_research")

        freshness_required = bool(docs_required or versions_required or changelog_required or web_required)
        mode = self._select_mode(docs_required=docs_required, versions_required=versions_required, changelog_required=changelog_required, web_required=web_required)
        targets = _dedupe([*route_targets, *_extract_targets(text), task.title or ""])
        if not targets and freshness_required:
            targets = [task.description[:160].strip()]
        stage_preference = self._stage_preference(
            freshness_required=freshness_required,
            route=route,
            versions_required=versions_required,
            docs_required=docs_required,
            changelog_required=changelog_required,
        )
        reason = self._reason(triggered, mode, targets, stage_preference=stage_preference)

        return FreshnessDecision(
            freshness_required=freshness_required,
            retrieval_mode=mode,
            retrieval_reason=reason,
            preferred_sources=self.source_preferences_for(mode),
            version_resolution_required=versions_required,
            docs_resolution_required=docs_required,
            changelog_resolution_required=changelog_required,
            web_resolution_required=web_required and mode == RetrievalMode.WEB,
            stage_preference=stage_preference,
            targets=targets,
            triggered_by=_dedupe(triggered),
        )

    @staticmethod
    def _select_mode(*, docs_required: bool, versions_required: bool, changelog_required: bool, web_required: bool) -> RetrievalMode:
        if docs_required and versions_required:
            return RetrievalMode.DOCS_PLUS_VERSIONS
        if changelog_required:
            return RetrievalMode.CHANGELOG
        if versions_required:
            return RetrievalMode.VERSIONS
        if docs_required:
            return RetrievalMode.DOCS
        if web_required:
            return RetrievalMode.WEB
        return RetrievalMode.NONE


    @staticmethod
    def _stage_preference(
        *,
        freshness_required: bool,
        route: RoutingDecision | None,
        versions_required: bool,
        docs_required: bool,
        changelog_required: bool,
    ) -> FreshnessStagePreference:
        if not freshness_required:
            return FreshnessStagePreference.PACKET_SCOPED
        if route is not None and (route.needs_repository_observation or route.needs_world_observation):
            return FreshnessStagePreference.AFTER_OBSERVE
        if versions_required or docs_required or changelog_required:
            return FreshnessStagePreference.IMMEDIATE
        return FreshnessStagePreference.PACKET_SCOPED

    @staticmethod
    def source_preferences_for(mode: RetrievalMode) -> list[SourcePreference]:
        source_order: list[tuple[RetrievalSourceKind, str]] = []
        if mode in {RetrievalMode.DOCS, RetrievalMode.DOCS_PLUS_VERSIONS, RetrievalMode.WEB}:
            source_order.append((RetrievalSourceKind.OFFICIAL_DOCUMENTATION, "Official docs are authoritative for current APIs, CLI flags, and examples."))
        if mode in {RetrievalMode.CHANGELOG, RetrievalMode.DOCS_PLUS_VERSIONS, RetrievalMode.VERSIONS}:
            source_order.append((RetrievalSourceKind.OFFICIAL_RELEASE_NOTES, "Release notes/changelogs are authoritative for breaking changes and migration notes."))
        if mode in {RetrievalMode.VERSIONS, RetrievalMode.DOCS_PLUS_VERSIONS, RetrievalMode.CHANGELOG}:
            source_order.append((RetrievalSourceKind.OFFICIAL_GITHUB_RELEASES, "Official GitHub releases/tags are authoritative when the project publishes versions there."))
            source_order.append((RetrievalSourceKind.OFFICIAL_PACKAGE_REGISTRY, "Official package registries are authoritative for published package versions."))
        source_order.append((RetrievalSourceKind.ISSUE_TRACKER_OR_DISCUSSION, "Use issues/discussions only for compatibility gaps not covered by official docs."))
        source_order.append((RetrievalSourceKind.THIRD_PARTY_ARTICLE, "Third-party articles are fallback only and must not override official sources."))
        out: list[SourcePreference] = []
        seen: set[RetrievalSourceKind] = set()
        for kind, reason in source_order:
            if kind in seen:
                continue
            seen.add(kind)
            out.append(SourcePreference(source_kind=kind, rank=len(out) + 1, reason=reason))
        return out

    @staticmethod
    def _reason(triggered: list[str], mode: RetrievalMode, targets: list[str], *, stage_preference: FreshnessStagePreference) -> str:
        if not triggered:
            return "No freshness-sensitive indicators were detected; local/model knowledge is sufficient for routing."
        target_text = ", ".join(targets[:5]) if targets else "the task"
        stage_hint = f" stage_preference={stage_preference.value};"
        return f"Freshness lookup required by control-plane gate ({', '.join(_dedupe(triggered))}); mode={mode.value};{stage_hint} targets={target_text}."


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)


def _extract_targets(text: str) -> list[str]:
    targets: list[str] = []
    for term in _TOOL_TERMS:
        if term in text:
            targets.append(term)
    # package/action-like tokens: actions/foo, org/tool, @scope/pkg, foo-bar>=1.2
    for match in re.findall(r"(?:[a-z0-9_.-]+/[a-z0-9_.-]+|@[a-z0-9_.-]+/[a-z0-9_.-]+|[a-z][a-z0-9_.-]{2,})(?=\s|$|[,;:])", text):
        if match in {"latest", "current", "version", "versions", "docs", "documentation", "recommended", "supported"}:
            continue
        if any(ch.isdigit() for ch in match) and "." in match:
            continue
        if match in targets:
            continue
        # Keep this conservative to avoid filling prompts with every word.
        if "/" in match or match in _TOOL_TERMS:
            targets.append(match)
    return targets[:12]


def _dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        out.append(text)
    return out

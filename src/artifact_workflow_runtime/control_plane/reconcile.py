from __future__ import annotations

import re

from artifact_workflow_runtime.freshness import FreshnessDecision, FreshnessStagePreference
from artifact_workflow_runtime.models import CommandRole, ObservationResult, RoutingDecision, Task, TaskClassification, TestLevel, WorkspaceReconciliation

_PATHISH_RE = re.compile(r"(?<!https:)(?<!http:)(?<!git@)\b(?:\.?/?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+)\b")
_IGNORED_SEGMENTS = {"http", "https", "github.com", "gitlab.com", "docs", "api"}


class WorkspaceReconciler:
    """Derive a typed continuation/adoption view from observed workspace facts."""

    def reconcile(
        self,
        *,
        task: Task,
        classification: TaskClassification,
        route: RoutingDecision,
        observation: ObservationResult | None,
        freshness_decision: FreshnessDecision | None = None,
    ) -> WorkspaceReconciliation:
        intent_floor = _effective_task_intent(classification)
        target_surfaces = _target_surfaces(task.description)
        existing_target_surfaces = [surface for surface in target_surfaces if _surface_exists(surface, observation)]
        passed_obligations = _passed_obligations(observation)
        unresolved_obligations = _unresolved_obligations(classification=classification, route=route, passed=passed_obligations)
        adopt_existing = intent_floor in {"implement", "modify"} and bool(existing_target_surfaces)
        delivery_mode = _delivery_mode(intent_floor=intent_floor, adopt_existing=adopt_existing, passed=passed_obligations)
        freshness_scope = _freshness_scope(freshness_decision, route=route, adopt_existing=adopt_existing)
        reasoning = _reasoning(
            intent_floor=intent_floor,
            target_surfaces=target_surfaces,
            existing_target_surfaces=existing_target_surfaces,
            passed_obligations=passed_obligations,
            unresolved_obligations=unresolved_obligations,
            delivery_mode=delivery_mode,
            freshness_scope=freshness_scope,
        )
        return WorkspaceReconciliation(
            task_id=task.id,
            task_intent_floor=intent_floor,
            delivery_mode=delivery_mode,
            target_surfaces=target_surfaces,
            existing_target_surfaces=existing_target_surfaces,
            adopt_existing_work=adopt_existing,
            passed_obligations=passed_obligations,
            unresolved_obligations=unresolved_obligations,
            freshness_scope=freshness_scope,
            reasoning=reasoning,
        )


def _target_surfaces(text: str) -> list[str]:
    surfaces: list[str] = []
    for match in _PATHISH_RE.findall(text or ""):
        candidate = match.strip().lstrip("./")
        if not candidate or candidate.startswith("workspace/"):
            continue
        head = candidate.split("/", 1)[0].lower()
        if head in _IGNORED_SEGMENTS or "/" not in candidate:
            continue
        if candidate.lower() not in {item.lower() for item in surfaces}:
            surfaces.append(candidate)
    return surfaces[:12]


def _surface_exists(surface: str, observation: ObservationResult | None) -> bool:
    if observation is None:
        return False
    surface_l = surface.lower().rstrip("/")
    for item in [*observation.structured_evidence.files_observed, *observation.structured_evidence.files_changed]:
        path = (item.path or "").replace("\\", "/").lower().rstrip("/")
        if not path:
            continue
        if path == surface_l or path.startswith(surface_l + "/") or surface_l.startswith(path + "/"):
            return True
        if f"/{surface_l}/" in f"/{path}/" or path.endswith("/" + surface_l):
            return True
    return False


def _passed_obligations(observation: ObservationResult | None) -> list[str]:
    if observation is None:
        return []
    passed: list[str] = []
    for test in observation.structured_evidence.tests:
        if not _evidence_passed(test.status, test.passed):
            continue
        if test.level == TestLevel.BUILD:
            _append_unique(passed, "build")
        elif test.level == TestLevel.UNIT:
            _append_unique(passed, "unit")
        elif test.level == TestLevel.INTEGRATION:
            _append_unique(passed, "integration")
        elif test.level == TestLevel.SMOKE:
            _append_unique(passed, "smoke")
    for command in observation.structured_evidence.commands_run:
        if command.exit_code not in {0, None}:
            continue
        if command.role == CommandRole.BUILD:
            _append_unique(passed, "build")
        elif command.role == CommandRole.UNIT_TEST:
            _append_unique(passed, "unit")
        elif command.role == CommandRole.INTEGRATION_TEST:
            _append_unique(passed, "integration")
        elif command.role == CommandRole.SMOKE_TEST:
            _append_unique(passed, "smoke")
    return passed


def _evidence_passed(status: str, passed: bool | None) -> bool:
    if passed is True:
        return True
    return str(status or "").lower() in {"passed", "success", "succeeded", "ok"}


def _unresolved_obligations(*, classification: TaskClassification, route: RoutingDecision, passed: list[str]) -> list[str]:
    unresolved: list[str] = []
    if classification.task_intent in {"implement", "modify"} and "build" not in passed:
        _append_unique(unresolved, "build")
    if classification.task_intent in {"implement", "modify"} and "unit" not in passed:
        _append_unique(unresolved, "unit")
    focus_values = {item.lower() for item in [*classification.observation_focus, *route.observation_focus, *route.required_evidence_types]}
    if focus_values & {"integration", "integration_harness", "runtime", "runtime_proof", "smoke", "smoke_tests"}:
        if "integration" not in passed:
            _append_unique(unresolved, "integration")
        if "smoke" not in passed:
            _append_unique(unresolved, "smoke")
    return unresolved


def _delivery_mode(*, intent_floor: str, adopt_existing: bool, passed: list[str]) -> str:
    if intent_floor not in {"implement", "modify"}:
        return "analysis_or_verification"
    if adopt_existing:
        if any(item in passed for item in ("build", "unit", "integration", "smoke")):
            return "continue_existing_candidate"
        return "complete_existing_candidate"
    return "new_implementation"


def _freshness_scope(decision: FreshnessDecision | None, *, route: RoutingDecision, adopt_existing: bool) -> str:
    if decision is None or not decision.freshness_required:
        return "none"
    if decision.stage_preference == FreshnessStagePreference.AFTER_OBSERVE:
        return "targeted_post_observe" if adopt_existing else "post_observe"
    if decision.stage_preference == FreshnessStagePreference.PACKET_SCOPED:
        return "packet_scoped"
    return "immediate"


def _reasoning(
    *,
    intent_floor: str,
    target_surfaces: list[str],
    existing_target_surfaces: list[str],
    passed_obligations: list[str],
    unresolved_obligations: list[str],
    delivery_mode: str,
    freshness_scope: str,
) -> str:
    target_text = ", ".join(target_surfaces) if target_surfaces else "no explicit target surfaces extracted"
    existing_text = ", ".join(existing_target_surfaces) if existing_target_surfaces else "none"
    passed_text = ", ".join(passed_obligations) if passed_obligations else "none"
    unresolved_text = ", ".join(unresolved_obligations) if unresolved_obligations else "none"
    return (
        f"Intent floor is {intent_floor}. "
        f"Target surfaces: {target_text}. Existing matching surfaces: {existing_text}. "
        f"Delivery mode: {delivery_mode}. Passed obligations observed so far: {passed_text}. "
        f"Unresolved obligations: {unresolved_text}. Freshness scope: {freshness_scope}."
    )


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def _effective_task_intent(classification: TaskClassification) -> str:
    intent = (classification.task_intent or "").strip().lower()
    return intent if intent in {"implement", "modify", "investigate", "document", "verify"} else "investigate"

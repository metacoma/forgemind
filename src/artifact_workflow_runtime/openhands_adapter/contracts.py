from __future__ import annotations

from artifact_workflow_runtime.policy import PolicyEnforcementPoint, RuntimeResource, RuntimeSubject
from artifact_workflow_runtime.policy.request_permissions import RequestPermissionCatalog
from artifact_workflow_runtime.policy.action_policy import ActionPolicyEnforcer

from artifact_workflow_runtime.models import (
    BackendKind,
    ExecutionRequest,
    ObservationRequest,
    PublishRequest,
    RepairRequest,
    VerificationMode,
    VerificationRequest,
    WorkPacketKind,
)

MUTATING_OBSERVE_ACTIONS = {"edit_files", "write_files", "commit", "push", "git push", "apply_cluster_changes", "change_host_config", "run_write_commands"}
DIRECT_VERIFY_FORBIDDEN_ACTIONS = {"filesystem", "shell", "git", "hosts", "kubernetes", "network_runtime_state"}
READ_ONLY_CAPABILITY_VALUES = {"document_read", "repo_read", "shell_read", "git_read", "k8s_read", "network_diagnostics"}
NON_PUBLISH_GIT_GUARDS = {"commit", "push", "git push", "git push --force", "git tag", "git merge", "git rebase", "create_pr", "open_pull_request", "publish", "release"}
DESTRUCTIVE_PUBLISH_GUARDS = {"git push --force", "git tag", "git merge", "git rebase", "release", "fix_ci_after_publish", "reimplement_feature", "apply_feature_changes", "edit_source_files", "repair"}


class OpenHandsStageContractGate:
    action_policy = ActionPolicyEnforcer()
    """Pre-flight contract gate for bounded OpenHands packets.

    This is intentionally outside ``adapter.py`` so the adapter can stay focused
    on execution, materialization, and evidence bundling. The gate validates the
    typed packet before any OpenHands conversation is started.
    """

    @staticmethod
    def forbidden_set(request: object) -> set[str]:
        values = [*getattr(request, "forbidden_actions", []), *getattr(request, "forbidden_inputs", [])]
        return {str(item).strip().lower() for item in values}

    @staticmethod
    def allowed_set(request: object) -> set[str]:
        values = [*getattr(request, "allowed_actions", []), *getattr(request, "allowed_inputs", [])]
        return {str(item).strip().lower() for item in values}

    @classmethod
    def require_forbidden_actions(cls, request: object, required: set[str], label: str) -> None:
        forbidden = cls.forbidden_set(request)
        missing = required - forbidden
        if missing:
            raise ValueError(f"{label} packets must explicitly forbid: {sorted(missing)}")

    @classmethod
    def require_allowed_actions_authorized(cls, request: object, *, stage: str, label: str) -> None:
        pep = PolicyEnforcementPoint()
        subject = RuntimeSubject(kind="agent", name="openhands", stage=stage)
        resource = RuntimeResource(kind="workflow_stage", name=stage, attributes={"request_id": getattr(request, "id", None)})
        denied: list[str] = []
        for token in sorted(cls.allowed_set(request)):
            try:
                spec = RequestPermissionCatalog.require_stage_permission(token, stage=stage)
                pep.require(subject=subject, action=spec.runtime_action, resource=resource, context={"stage": stage, "label": label})
            except (PermissionError, ValueError) as exc:
                denied.append(f"{token}: {exc}")
        if denied:
            raise ValueError(f"{label} packets allow actions outside the stage ACL: {denied}")

    @staticmethod
    def validate_compiled_prompt_contains_contract(request: object, *, label: str) -> None:
        prompt = request.compiled_prompt()
        required_markers = (
            "## Non-negotiable control-plane boundary",
            "## Allowed actions",
            "## Forbidden actions",
            "## Stop conditions",
            "## Required outputs",
            "Do not choose the next workflow step",
            "if an action is not explicitly allowed, treat it as forbidden",
            "First OpenHands pass must return a concise human-readable operational report only",
            "the controller will request the canonical JSON handoff in a separate follow-up",
        )
        missing = [marker for marker in required_markers if marker not in prompt]
        if missing:
            raise ValueError(f"{label} compiled prompt is missing stage-contract sections: {missing}")

    @classmethod
    def validate_observation(cls, request: ObservationRequest) -> None:
        if request.work_packet_kind not in {WorkPacketKind.OBSERVE, WorkPacketKind.RESEARCH}:
            raise ValueError(f"OpenHands observe() only accepts observe/research packets, got {request.work_packet_kind}")
        allowed = cls.allowed_set(request)
        if allowed & MUTATING_OBSERVE_ACTIONS:
            raise ValueError(f"Observation packets cannot allow mutating actions: {sorted(allowed & MUTATING_OBSERVE_ACTIONS)}")
        mutating_capabilities = {cap.value for cap in request.capabilities if cap.value not in READ_ONLY_CAPABILITY_VALUES}
        if mutating_capabilities:
            raise ValueError(f"Observation packets cannot carry mutating capabilities: {sorted(mutating_capabilities)}")
        cls.require_forbidden_actions(request, {"edit_files", "write_files", "commit", "push", "git push", "create_pr", "open_pull_request", "publish"}, "Observation")
        if request.evidence_requirements.require_structured is not True:
            raise ValueError("Observation packets must require structured evidence as the operational output")
        stage = "research" if request.work_packet_kind == WorkPacketKind.RESEARCH else "observe"
        cls.require_allowed_actions_authorized(request, stage=stage, label="Observation")
        cls.action_policy.validate_request(request, stage=stage, label="Observation")
        cls.validate_compiled_prompt_contains_contract(request, label="Observation")

    @classmethod
    def validate_execution(cls, request: ExecutionRequest) -> None:
        if request.work_packet_kind != WorkPacketKind.EXECUTE:
            raise ValueError(f"OpenHands execute() only accepts execute packets; publish packets must use publish() (not execute/publish packets), got {request.work_packet_kind}")
        if not request.expected_outputs:
            raise ValueError("Execution packets must declare expected_outputs")
        forbidden = cls.forbidden_set(request)
        if "change_workflow_decision" not in forbidden:
            raise ValueError("Execution packets must forbid changing workflow decisions")
        cls.require_forbidden_actions(request, NON_PUBLISH_GIT_GUARDS, "Execution")
        if request.evidence_requirements.require_structured is not True:
            raise ValueError("Execution packets must require structured evidence as the operational output")
        cls.require_allowed_actions_authorized(request, stage="execute", label="Execution")
        cls.action_policy.validate_request(request, stage="execute", label="Execution")
        cls.validate_compiled_prompt_contains_contract(request, label="Execution")

    @classmethod
    def validate_publish(cls, request: PublishRequest) -> None:
        if request.work_packet_kind != WorkPacketKind.PUBLISH:
            raise ValueError(f"OpenHands publish() only accepts publish packets, got {request.work_packet_kind}")
        if not request.expected_outputs:
            raise ValueError("Publish packets must declare expected_outputs")
        forbidden = cls.forbidden_set(request)
        if "change_workflow_decision" not in forbidden:
            raise ValueError("Publish packets must forbid changing workflow decisions")
        if "reimplement_feature" not in forbidden or "expand_task_scope" not in forbidden:
            raise ValueError("Publish packets must forbid reimplementation and scope expansion")
        cls.require_forbidden_actions(request, DESTRUCTIVE_PUBLISH_GUARDS, "Publish")
        if request.evidence_requirements.require_structured is not True:
            raise ValueError("Publish packets must require structured evidence as the operational output")
        cls.require_allowed_actions_authorized(request, stage="publish", label="Publish")
        cls.action_policy.validate_request(request, stage="publish", label="Publish")
        cls.validate_compiled_prompt_contains_contract(request, label="Publish")

    @classmethod
    def validate_repair(cls, request: RepairRequest) -> None:
        if request.work_packet_kind != WorkPacketKind.REPAIR:
            raise ValueError(f"OpenHands repair() only accepts repair packets, got {request.work_packet_kind}")
        cls.require_forbidden_actions(request, NON_PUBLISH_GIT_GUARDS, "Repair")
        if request.attempt < 1 or request.attempt > request.max_attempts:
            raise ValueError("Repair packet attempt is outside the configured repair budget")
        if request.evidence_requirements.require_structured is not True:
            raise ValueError("Repair packets must require structured evidence as the operational output")
        cls.require_allowed_actions_authorized(request, stage="repair", label="Repair")
        cls.action_policy.validate_request(request, stage="repair", label="Repair")
        cls.validate_compiled_prompt_contains_contract(request, label="Repair")

    @classmethod
    def validate_world_verification(cls, request: VerificationRequest) -> None:
        if request.work_packet_kind != WorkPacketKind.VERIFY:
            raise ValueError(f"OpenHands verify() only accepts verify packets, got {request.work_packet_kind}")
        if request.backend != BackendKind.OPENHANDS or request.mode != VerificationMode.WORLD_CHECK:
            raise ValueError("OpenHands verify() requires backend=openhands and mode=world_check")
        forbidden = {item.strip().lower() for item in request.forbidden_inputs}
        # World checks may inspect filesystem/shell/git, so they must not inherit the Direct LLM forbidden-input contract.
        if forbidden & DIRECT_VERIFY_FORBIDDEN_ACTIONS == DIRECT_VERIFY_FORBIDDEN_ACTIONS:
            raise ValueError("World verification packets must declare world-check inputs/actions explicitly, not Direct LLM forbidden inputs")
        cls.require_forbidden_actions(request, {"edit_files", "write_files", "fix_code", "repair", "commit", "push", "git push", "create_pr", "open_pull_request", "publish"}, "World verification")
        if request.evidence_requirements.require_structured is not True:
            raise ValueError("World verification packets must require structured evidence as the operational output")
        cls.require_allowed_actions_authorized(request, stage="verify", label="World verification")
        cls.action_policy.validate_request(request, stage="verify", label="World verification")
        cls.validate_compiled_prompt_contains_contract(request, label="World verification")

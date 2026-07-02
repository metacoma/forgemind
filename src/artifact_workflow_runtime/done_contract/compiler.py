from __future__ import annotations

from artifact_workflow_runtime.done_contract.models import DoneContract, EnvironmentRequirement, RuntimeProofPolicy
from artifact_workflow_runtime.models import ContextPacket, DiscoveredImpactKind, ObligationAnalysis, Task, TaskClassification

_RUNTIME_TEST_LEVELS = {"integration", "smoke", "e2e", "end-to-end", "runtime_proof", "runtime"}
_BUILDISH_LEVELS = {"build", "compile", "unit", "lint"}


class DoneContractCompiler:
    def compile(
        self,
        *,
        task: Task,
        classification: TaskClassification,
        obligations: ObligationAnalysis | None,
        context_packet: ContextPacket | None,
    ) -> DoneContract:
        obligations = obligations or ObligationAnalysis(reasoning_summary="No obligation analysis available.")
        change_class = self._change_class(classification, obligations)
        deliverables: list[str] = ["implementation"]
        required_evidence: list[str] = ["implementation_evidence"]
        docs_examples_requirements = [*obligations.required_documentation_updates, *obligations.required_examples_updates]
        ci_requirements = list(obligations.required_ci_updates)
        env_reqs: list[EnvironmentRequirement] = []
        runtime_policy = RuntimeProofPolicy(required=False, allow_debt=True, preferred_level="existing_harness")

        required_levels = {str(level).strip().lower() for level in obligations.required_test_levels if str(level).strip()}
        runtime_levels = sorted(level for level in required_levels if level in _RUNTIME_TEST_LEVELS)
        buildish_levels = sorted(level for level in required_levels if level in _BUILDISH_LEVELS)

        if change_class in {"new_client_integration", "integration_sensitive_change"} or runtime_levels:
            deliverables.append("runtime_proof")
            required_evidence.append("runtime_proof_success")
            runtime_policy = RuntimeProofPolicy(required=True, allow_debt=False, preferred_level="existing_harness")
            deliverables.append("integration_test_or_equivalent")
            required_evidence.append("integration_or_equivalent_success")
            if "ci_update_if_tests_added" not in deliverables:
                deliverables.append("ci_update_if_tests_added")

        if obligations.required_ci_updates and "ci_update_if_tests_added" not in deliverables:
            deliverables.append("ci_update_if_tests_added")
            required_evidence.append("ci_workflow_diff")

        if obligations.required_documentation_updates:
            deliverables.append("documentation_update")
            required_evidence.append("documentation_update_evidence")
        if obligations.required_examples_updates:
            deliverables.append("example_update")
            required_evidence.append("example_update_evidence")

        if runtime_policy.required:
            env_reqs.append(
                EnvironmentRequirement(
                    name="runtime_under_test",
                    mode="bootstrap_if_needed",
                    source="controller",
                    dependency_kind="runtime",
                    applicable_packet_types=["setup", "integration", "test", "verification"],
                    required_verification_levels=runtime_levels,
                )
            )

        for condition in obligations.required_environment_conditions:
            value = str(condition).strip()
            if not value:
                continue
            env_reqs.append(
                EnvironmentRequirement(
                    name=value,
                    mode="required",
                    source="obligation_discovery",
                    dependency_kind="environment",
                    applicable_packet_types=["setup", "integration", "test", "verification"],
                    required_verification_levels=runtime_levels,
                )
            )

        publish_required = bool(obligations.required_publish_actions)
        notes: list[str] = []
        if runtime_policy.required:
            notes.append("Integration-sensitive changes require runtime proof and integration coverage before acceptance.")
        if env_reqs:
            notes.append("Environment requirements are typed dependency nodes and must be materialized separately from implementation work.")

        return DoneContract(
            task_id=task.id,
            primary_goal=classification.normalized_task or task.description,
            change_class=change_class,
            deliverables=_unique(deliverables),
            required_evidence=_unique(required_evidence),
            verification_policy=runtime_policy,
            environment_requirements=_dedupe_env(env_reqs),
            ci_requirements=_unique(ci_requirements),
            docs_examples_requirements=_unique(docs_examples_requirements),
            publish_required=publish_required,
            notes=notes,
        )

    def _change_class(self, classification: TaskClassification, obligations: ObligationAnalysis) -> str:
        required_levels = {str(level).strip().lower() for level in obligations.required_test_levels if str(level).strip()}
        impact_kinds = {impact.kind for impact in obligations.discovered_impacts}
        runtime_sensitive = bool(required_levels & _RUNTIME_TEST_LEVELS) or bool(impact_kinds & {DiscoveredImpactKind.INTEGRATION, DiscoveredImpactKind.SETUP})
        grpc_surface = any("grpc/" in str(path).replace('\\', '/') for path in obligations.affected_surfaces)
        if classification.execution_family.value == "repository_change" and grpc_surface and classification.task_intent in {"implement", "modify"}:
            return "new_client_integration" if runtime_sensitive else "repository_change"
        if runtime_sensitive:
            return "integration_sensitive_change"
        if classification.task_intent in {"implement", "modify"}:
            return "repository_change"
        return "generic_change"


def _unique(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _dedupe_env(items: list[EnvironmentRequirement]) -> list[EnvironmentRequirement]:
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    out: list[EnvironmentRequirement] = []
    for item in items:
        key = (item.name, item.dependency_kind, tuple(item.applicable_packet_types))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out

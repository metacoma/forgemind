from __future__ import annotations

from artifact_workflow_runtime.done_contract.models import DoneContract, EnvironmentRequirement, RuntimeProofPolicy
from artifact_workflow_runtime.models import ContextPacket, ObligationAnalysis, Task, TaskClassification


class DoneContractCompiler:
    def compile(
        self,
        *,
        task: Task,
        classification: TaskClassification,
        obligations: ObligationAnalysis | None = None,
        context_packet: ContextPacket | None,
    ) -> DoneContract:
        obligations = obligations or ObligationAnalysis(reasoning_summary="No obligation analysis available yet.")
        text = "\n".join(
            [
                task.description,
                context_packet.text if context_packet is not None else "",
                " ".join(obligations.affected_surfaces),
                " ".join(item.summary for item in obligations.discovered_impacts),
            ]
        ).lower()
        change_class = self._change_class(classification, obligations, text)
        deliverables: list[str] = ["implementation"]
        required_evidence: list[str] = ["implementation_evidence"]
        docs_examples_requirements = [*obligations.required_documentation_updates, *obligations.required_examples_updates]
        ci_requirements = list(obligations.required_ci_updates)
        env_reqs: list[EnvironmentRequirement] = []
        runtime_policy = RuntimeProofPolicy(required=False, allow_debt=True, preferred_level="existing_harness")

        if change_class in {"new_client_integration", "integration_sensitive_change"}:
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

        env_names = set(obligations.required_environment_conditions)
        setup_text = " ".join(obligations.required_setup_steps).lower()
        if "freeplane" in text or "freeplane" in setup_text:
            env_names.add("freeplane_runtime")
        for name in sorted(env_names):
            mode = "bootstrap_if_needed" if name == "freeplane_runtime" or "script" in text else "required"
            source = "repo_supported" if mode == "bootstrap_if_needed" else "task"
            env_reqs.append(EnvironmentRequirement(name=name, mode=mode, source=source))

        publish_required = bool(obligations.required_publish_actions)
        notes: list[str] = []
        if change_class in {"new_client_integration", "integration_sensitive_change"}:
            notes.append("Integration-sensitive changes require runtime proof and integration coverage before acceptance.")
        if env_reqs:
            notes.append("Environment requirements must be attempted through bootstrap when repository-supported paths exist.")

        return DoneContract(
            task_id=task.id,
            primary_goal=classification.normalized_task or task.description,
            change_class=change_class,
            deliverables=_unique(deliverables),
            required_evidence=_unique(required_evidence),
            verification_policy=runtime_policy,
            environment_requirements=env_reqs,
            ci_requirements=_unique(ci_requirements),
            docs_examples_requirements=_unique(docs_examples_requirements),
            publish_required=publish_required,
            notes=notes,
        )

    def _change_class(self, classification: TaskClassification, obligations: ObligationAnalysis, text: str) -> str:
        if any(marker in text for marker in ("grpc client", "new client", "binding", "sdk", "protocol consumer", "kotlin", "cpp client", "client binding")):
            return "new_client_integration"
        if any(level in {"integration", "smoke", "e2e"} for level in obligations.required_test_levels):
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

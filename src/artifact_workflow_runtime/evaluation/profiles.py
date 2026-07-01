from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from artifact_workflow_runtime.models import Capability, ExecutionFamily


@dataclass(frozen=True)
class ScenarioRuntimeProfile:
    profile_id: str
    direct_llm_scripts: dict[str, list[dict[str, Any]]]
    openhands_scripts: dict[str, list[str]]
    auto_approve: bool = True


def _classification(*, task: str, family: ExecutionFamily, intent: str, capabilities: list[Capability], focus: list[str], risk: str = "medium", needs_world: bool = True) -> dict[str, Any]:
    return {
        "normalized_task": task,
        "needs_world_facts": needs_world,
        "execution_family": family.value,
        "task_intent": intent,
        "capabilities": [item.value for item in capabilities],
        "observation_focus": focus,
        "reasoning": f"Scenario profile for {task}.",
        "risk_level": risk,
    }


def _route(*, repo_observe: bool = True, world_observe: bool = False, can_plan: bool = False, focus: list[str] | None = None) -> dict[str, Any]:
    return {
        "needs_repository_observation": repo_observe,
        "needs_world_observation": world_observe,
        "needs_fresh_external_research": False,
        "can_plan_immediately": can_plan,
        "required_evidence_types": ["repo_structure"] if repo_observe else [],
        "research_targets": [],
        "observation_focus": focus or [],
        "reasoning": "Scenario route profile.",
    }


def _obligations(*, tests: list[str] | None = None, setup: list[str] | None = None, env: list[str] | None = None, docs: list[str] | None = None, examples: list[str] | None = None, ci: list[str] | None = None, codegen: list[str] | None = None, completion: list[str] | None = None, blockers: list[str] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "required_test_levels": tests or [],
        "required_setup_steps": setup or [],
        "required_environment_conditions": env or [],
        "required_documentation_updates": docs or [],
        "required_examples_updates": examples or [],
        "required_ci_updates": ci or [],
        "required_codegen_or_build_updates": codegen or [],
        "completion_requirements": completion or [],
        "blocker_conditions": blockers or [],
        "reasoning_summary": "Scenario obligation profile.",
    }
    return payload


def _plan(*, summary: str, family: ExecutionFamily, intent: str, capabilities: list[Capability], steps: list[str], success: list[str], checks: list[str], repo_changes: list[str] | None = None, tests: list[str] | None = None, setup: list[str] | None = None, require_commit: bool = False, require_push: bool = False, env: str = "docker_container") -> dict[str, Any]:
    return {
        "summary": summary,
        "execution_family": family.value,
        "task_intent": intent,
        "deliverable_kind": "repository_changes" if family == ExecutionFamily.REPOSITORY_CHANGE else "world_changes",
        "capabilities": [item.value for item in capabilities],
        "steps": steps,
        "success_criteria": success,
        "verification_checks": checks,
        "requires_mutation": any(item in {Capability.REPO_WRITE, Capability.SHELL_WRITE, Capability.HOST_ACCESS, Capability.K8S_WRITE, Capability.GIT_WRITE} for item in capabilities),
        "must_change_world": family != ExecutionFamily.REPOSITORY_CHANGE or any(item in {Capability.GIT_WRITE, Capability.REPO_WRITE} for item in capabilities),
        "expected_repo_changes": repo_changes or [],
        "required_test_levels": tests or [],
        "required_setup_steps": setup or [],
        "require_commit": require_commit,
        "require_push": require_push,
        "execution_environment": env,
        "reasoning": "Scenario planning profile.",
    }


def _verification(*, passed: bool, summary: str, checks_passed: list[str], checks_failed: list[str] | None = None, missing_obligations: list[str] | None = None, performed_test_levels: list[str] | None = None, completion_status: str = "completed", pr_passed: list[str] | None = None) -> dict[str, Any]:
    return {
        "passed": passed,
        "summary": summary,
        "checks_passed": checks_passed,
        "checks_failed": checks_failed or [],
        "missing_evidence": [],
        "confidence": "high" if passed else "medium",
        "reasoning": "Scenario verification profile.",
        "performed_test_levels": performed_test_levels or [],
        "missing_obligations": missing_obligations or [],
        "completion_status": completion_status,
        "pr_detected": bool(pr_passed),
        "pr_checks_waited": bool(pr_passed),
        "pr_checks_passed": pr_passed or [],
        "pr_checks_failed": [],
        "pr_checks_pending": [],
    }


def _structured_text(*, summary: str, commands: list[tuple[str, int, str]] | None = None, files_changed: list[tuple[str, str, str]] | None = None, tests: list[tuple[str, str, str, bool]] | None = None, blockers: list[tuple[str, str]] | None = None, postcheck_summary: str | None = None) -> str:
    import json

    payload = {
        "summary": summary,
        "structured_evidence": {
            "commands_run": [
                {"command": command, "exit_code": exit_code, "output_excerpt": output_excerpt}
                for command, exit_code, output_excerpt in (commands or [])
            ],
            "files_changed": [
                {"path": path, "action": action, "summary": file_summary}
                for path, action, file_summary in (files_changed or [])
            ],
            "tests": [
                {"name": name, "command": command, "status": status, "passed": passed}
                for name, command, status, passed in (tests or [])
            ],
            "blockers": [
                {"kind": kind, "summary": blocker_summary}
                for kind, blocker_summary in (blockers or [])
            ],
            "mutation_summary": {
                "changed": bool(files_changed),
                "summary": summary,
                "files_changed": [path for path, _, _ in (files_changed or [])],
            },
            "postcheck_summary": {
                "attempted": bool(postcheck_summary),
                "summary": postcheck_summary or "",
            },
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def _profile_repo_feature_simple() -> ScenarioRuntimeProfile:
    return ScenarioRuntimeProfile(
        profile_id="repo_feature_simple",
        direct_llm_scripts={
            "classification": [_classification(task="Inspect repo and fix failing tests", family=ExecutionFamily.REPOSITORY_CHANGE, intent="modify", capabilities=[Capability.REPO_READ, Capability.REPO_WRITE, Capability.GIT_WRITE], focus=["find failing test commands", "identify changed files"])],
            "route_analysis": [_route(focus=["find failing test commands", "identify changed files"])],
            "obligation_analysis": [_obligations(tests=["unit"], env=["docker_container"], completion=["run pytest target"])],
            "planning": [_plan(summary="Edit failing code path and validate", family=ExecutionFamily.REPOSITORY_CHANGE, intent="modify", capabilities=[Capability.REPO_WRITE, Capability.GIT_WRITE], steps=["inspect failing path", "edit code", "run tests"], success=["target tests pass"], checks=["run pytest target"], repo_changes=["src/app.py updated"], tests=["unit"])],
            "verification": [_verification(passed=True, summary="Evidence shows the target pytest command passed after the code change.", checks_passed=["run pytest target"], performed_test_levels=["unit"])],
        },
        openhands_scripts={
            "observe": ["Observed failing test: pytest tests/test_feature.py -k scenario. Relevant file: src/app.py"],
            "execute": [_structured_text(summary="Applied fix in src/app.py and ran pytest tests/test_feature.py -k scenario successfully.", commands=[("pytest tests/test_feature.py -k scenario", 0, "1 passed")], files_changed=[("src/app.py", "changed", "Applied bounded fix")], tests=[("pytest tests/test_feature.py -k scenario", "pytest tests/test_feature.py -k scenario", "passed", True)])],
        },
    )


def _profile_repo_feature_with_docs() -> ScenarioRuntimeProfile:
    return ScenarioRuntimeProfile(
        profile_id="repo_feature_with_docs",
        direct_llm_scripts={
            "classification": [_classification(task="Add public feature with docs", family=ExecutionFamily.REPOSITORY_CHANGE, intent="implement", capabilities=[Capability.REPO_READ, Capability.REPO_WRITE], focus=["repo structure", "docs layout"], needs_world=False)],
            "route_analysis": [_route(focus=["repo structure", "docs layout"])],
            "obligation_analysis": [_obligations(tests=["unit"], docs=["README public usage section"], completion=["feature works", "README public usage section updated"])],
            "planning": [_plan(summary="Implement feature and docs", family=ExecutionFamily.REPOSITORY_CHANGE, intent="implement", capabilities=[Capability.REPO_WRITE], steps=["edit src/app.py", "update README public usage section", "run unit tests"], success=["feature works", "README public usage section updated"], checks=["unit tests pass", "README public usage section updated"], repo_changes=["src/app.py", "README.md"], tests=["unit"])],
            "verification": [_verification(passed=True, summary="Feature and README public usage section are present with unit tests.", checks_passed=["unit tests pass", "README public usage section updated"], performed_test_levels=["unit"])],
        },
        openhands_scripts={
            "observe": ["Observed repo structure with src/app.py, README.md, and unit tests."],
            "execute": [
                _structured_text(summary="Implemented the bounded feature in src/app.py.", commands=[("python -m pytest tests/unit", 0, "unit passed")], files_changed=[("src/app.py", "changed", "Implemented feature")], tests=[("python -m pytest tests/unit", "python -m pytest tests/unit", "passed", True)]),
                _structured_text(summary="Verified tests for the bounded feature.", commands=[("python -m pytest tests/unit", 0, "unit passed")], tests=[("python -m pytest tests/unit", "python -m pytest tests/unit", "passed", True)]),
                _structured_text(summary="Updated README public usage section for the feature.", commands=[("python -m pytest tests/unit", 0, "unit passed")], files_changed=[("README.md", "changed", "Updated public usage section")], tests=[("python -m pytest tests/unit", "python -m pytest tests/unit", "passed", True)]),
                _structured_text(summary="Recorded final verification checkpoint for the feature change.", commands=[("python -m pytest tests/unit", 0, "unit passed")], tests=[("python -m pytest tests/unit", "python -m pytest tests/unit", "passed", True)]),
            ],
        },
    )


def _profile_repo_reentry_docs_gap() -> ScenarioRuntimeProfile:
    return ScenarioRuntimeProfile(
        profile_id="repo_reentry_docs_gap",
        direct_llm_scripts={
            "classification": [_classification(task="Add feature with public docs impact", family=ExecutionFamily.REPOSITORY_CHANGE, intent="implement", capabilities=[Capability.REPO_READ, Capability.REPO_WRITE], focus=[], needs_world=False)],
            "route_analysis": [_route(focus=["repository structure", "test/docs layout"], can_plan=True)],
            "obligation_analysis": [
                _obligations(tests=["unit"], completion=["feature works"]),
                _obligations(tests=["unit"], docs=["README public usage section"], completion=["feature works", "README public usage section updated"]),
            ],
            "planning": [
                _plan(summary="Implement feature", family=ExecutionFamily.REPOSITORY_CHANGE, intent="implement", capabilities=[Capability.REPO_WRITE], steps=["edit src/app.py", "run unit tests"], success=["feature works"], checks=["unit tests pass"], repo_changes=["src/app.py"], tests=["unit"]),
                _plan(summary="Implement feature and docs", family=ExecutionFamily.REPOSITORY_CHANGE, intent="implement", capabilities=[Capability.REPO_WRITE], steps=["edit src/app.py", "update README public usage section", "run unit tests"], success=["feature works", "README public usage section updated"], checks=["unit tests pass", "README public usage section updated"], repo_changes=["src/app.py", "README public usage section"], tests=["unit"]),
            ],
            "verification": [
                _verification(passed=False, summary="Feature works, but documentation impact discovered: README public usage section is missing.", checks_passed=["unit tests pass"], checks_failed=["README public usage section updated"], missing_obligations=["documentation impact discovered: README public usage section required"], performed_test_levels=["unit"], completion_status="partially_completed"),
                _verification(passed=True, summary="Feature and README public usage section are present with unit tests.", checks_passed=["unit tests pass", "README public usage section updated"], performed_test_levels=["unit"]),
            ],
        },
        openhands_scripts={
            "observe": ["Observed repo structure with src/app.py, README.md, and unit tests."],
            "execute": [
                _structured_text(summary="Changed src/app.py. Ran unit tests passed.", commands=[("pytest", 0, "unit tests passed")], files_changed=[("src/app.py", "changed", "Implemented feature")], tests=[("pytest", "pytest", "passed", True)]),
                _structured_text(summary="Changed src/app.py and README public usage section. Ran unit tests passed.", commands=[("pytest", 0, "unit tests passed")], files_changed=[("src/app.py", "changed", "Implemented feature"), ("README.md", "changed", "Updated public usage section")], tests=[("pytest", "pytest", "passed", True)]),
            ],
        },
    )


def _profile_repo_integration_blocked() -> ScenarioRuntimeProfile:
    return ScenarioRuntimeProfile(
        profile_id="repo_integration_required",
        direct_llm_scripts={
            "classification": [_classification(task="Add C++ gRPC client", family=ExecutionFamily.REPOSITORY_CHANGE, intent="implement", capabilities=[Capability.REPO_READ, Capability.REPO_WRITE], focus=["inspect existing clients", "inspect integration harness"])],
            "route_analysis": [_route(focus=["inspect existing clients", "inspect integration harness"])],
            "obligation_analysis": [_obligations(tests=["build", "integration"], env=["freeplane_runtime"], completion=["build succeeds", "integration tests pass"], blockers=["Freeplane runtime must be installed for integration verification"])],
            "planning": [_plan(summary="Add C++ gRPC client and validate integration", family=ExecutionFamily.REPOSITORY_CHANGE, intent="implement", capabilities=[Capability.REPO_WRITE], steps=["add client", "run build", "run integration tests"], success=["build succeeds", "integration tests pass"], checks=["build succeeds", "integration tests pass"], repo_changes=["src/cpp/client.cc", "CMakeLists.txt"], tests=["build", "integration"], setup=["Freeplane must be available for integration tests"])],
            "verification": [_verification(passed=True, summary="The implementation and build evidence look useful, but integration could not actually run.", checks_passed=["build succeeds"], performed_test_levels=["build"], completion_status="completed")],
        },
        openhands_scripts={
            "observe": ["Observed existing clients and a Freeplane-backed integration harness."],
            "execute": [_structured_text(summary="Added src/cpp/client.cc and updated CMakeLists.txt. cmake build succeeded. Integration tests blocked by missing Freeplane runtime.", commands=[("cmake --build build", 0, "build succeeded")], files_changed=[("src/cpp/client.cc", "changed", "Added client"), ("CMakeLists.txt", "changed", "Updated build")], blockers=[("integration_environment_unavailable", "Freeplane runtime is not installed / not found in the Docker environment")])],
        },
    )


def _profile_repo_publish_repair_success() -> ScenarioRuntimeProfile:
    return ScenarioRuntimeProfile(
        profile_id="repo_publish_repair_success",
        direct_llm_scripts={
            "classification": [_classification(task="Implement change and publish PR", family=ExecutionFamily.REPOSITORY_CHANGE, intent="implement", capabilities=[Capability.REPO_READ, Capability.REPO_WRITE, Capability.REPO_CREATE_PR], focus=["inspect repo"])],
            "route_analysis": [_route(focus=["inspect repo"])],
            "planning": [_plan(summary="Implement the change and publish a PR", family=ExecutionFamily.REPOSITORY_CHANGE, intent="implement", capabilities=[Capability.REPO_WRITE, Capability.REPO_CREATE_PR], steps=["edit code", "run unit tests", "publish PR"], success=["unit tests pass", "PR checks green"], checks=["unit tests pass", "PR checks green"], repo_changes=["src/app.py"], tests=["unit"])],
            "verification": [_verification(passed=True, summary="Final publish evidence shows PR checks passed after repair.", checks_passed=["unit tests pass", "PR checks green"], performed_test_levels=["unit"], pr_passed=["ci/test"])],
        },
        openhands_scripts={
            "observe": ["Repository observed."],
            "execute": [_structured_text(summary="Changed src/app.py and ran pytest tests/test_app.py passed.", commands=[("pytest tests/test_app.py", 0, "1 passed")], files_changed=[("src/app.py", "changed", "Implemented change")], tests=[("pytest tests/test_app.py", "pytest tests/test_app.py", "passed", True)])],
            "publish": [
                "Created PR #7 and waited for PR checks. PR checks failed: ci/test failed.",
                "Updated PR #7 and waited for PR checks. PR checks passed: ci/test passed.",
            ],
            "repair": [_structured_text(summary="Applied fix in src/app.py and ran pytest tests/test_app.py passed.", commands=[("pytest tests/test_app.py", 0, "1 passed")], files_changed=[("src/app.py", "changed", "Applied repair")], tests=[("pytest tests/test_app.py", "pytest tests/test_app.py", "passed", True)])],
        },
    )


def _profile_repo_ci_fix() -> ScenarioRuntimeProfile:
    return ScenarioRuntimeProfile(
        profile_id="repo_ci_fix",
        direct_llm_scripts={
            "classification": [_classification(task="Fix CI workflow for repository", family=ExecutionFamily.REPOSITORY_CHANGE, intent="modify", capabilities=[Capability.REPO_READ, Capability.REPO_WRITE], focus=["inspect workflows", "inspect failing command"])],
            "route_analysis": [_route(focus=["inspect workflows", "inspect failing command"])],
            "obligation_analysis": [_obligations(ci=["github actions workflow updated"], completion=["CI workflow fixed", "workflow file updated"], tests=["unit"])],
            "planning": [_plan(summary="Fix CI workflow and validate config", family=ExecutionFamily.REPOSITORY_CHANGE, intent="modify", capabilities=[Capability.REPO_WRITE], steps=["edit workflow", "run unit tests"], success=["CI workflow fixed", "workflow file updated"], checks=["unit tests pass", "github actions workflow updated"], repo_changes=[".github/workflows/ci.yml"], tests=["unit"])],
            "verification": [_verification(passed=True, summary="Workflow update and test evidence are present.", checks_passed=["unit tests pass", "github actions workflow updated"], performed_test_levels=["unit"])],
        },
        openhands_scripts={
            "observe": ["Observed GitHub Actions workflow and failing pytest command."],
            "execute": [
                _structured_text(summary="Updated the CI workflow file.", commands=[("pytest", 0, "unit tests passed")], files_changed=[(".github/workflows/ci.yml", "changed", "Updated CI workflow")], tests=[("pytest", "pytest", "passed", True)]),
                _structured_text(summary="Verified tests for the CI fix.", commands=[("pytest", 0, "unit tests passed")], tests=[("pytest", "pytest", "passed", True)]),
                _structured_text(summary="Validated CI/build surface update.", commands=[("python -m compileall .", 0, "ok")], files_changed=[(".github/workflows/ci.yml", "changed", "Verified workflow update")]),
                _structured_text(summary="Recorded verification checkpoint after CI fix.", commands=[("pytest", 0, "unit tests passed")], tests=[("pytest", "pytest", "passed", True)]),
            ],
        },
    )


def _profile_repo_client_library_addition() -> ScenarioRuntimeProfile:
    return ScenarioRuntimeProfile(
        profile_id="repo_client_library_addition",
        direct_llm_scripts={
            "classification": [_classification(task="Add Python client library", family=ExecutionFamily.REPOSITORY_CHANGE, intent="implement", capabilities=[Capability.REPO_READ, Capability.REPO_WRITE], focus=["inspect API bindings", "inspect integration tests"])],
            "route_analysis": [_route(focus=["inspect API bindings", "inspect integration tests"])],
            "obligation_analysis": [_obligations(tests=["unit", "integration"], completion=["client library added", "integration tests pass"], docs=["README client usage snippet"], examples=["client example snippet"], codegen=["client packaging config updated"])],
            "planning": [_plan(summary="Add client library plus docs and integration checks", family=ExecutionFamily.REPOSITORY_CHANGE, intent="implement", capabilities=[Capability.REPO_WRITE], steps=["add client package", "update packaging", "update docs", "run tests"], success=["client library added", "integration tests pass", "README client usage snippet"], checks=["unit tests pass", "integration tests pass", "README client usage snippet"], repo_changes=["src/client/__init__.py", "pyproject.toml", "README.md"], tests=["unit", "integration"])],
            "verification": [_verification(passed=True, summary="Client library, integration evidence, and docs are present.", checks_passed=["unit tests pass", "integration tests pass", "README client usage snippet"], performed_test_levels=["unit", "integration"])],
        },
        openhands_scripts={
            "observe": ["Observed API bindings, packaging metadata, and integration tests."],
            "execute": [
                _structured_text(summary="Added the client library package.", commands=[("pytest tests/unit", 0, "unit passed")], files_changed=[("src/client/__init__.py", "changed", "Added client library")], tests=[("pytest tests/unit", "pytest tests/unit", "passed", True)]),
                _structured_text(summary="Verified tests for the client library.", commands=[("pytest tests/integration", 0, "integration passed")], tests=[("pytest tests/integration", "pytest tests/integration", "passed", True)]),
                _structured_text(summary="Updated docs and examples for the client library.", commands=[("pytest tests/unit", 0, "unit passed")], files_changed=[("README.md", "changed", "Added docs and examples")], tests=[("pytest tests/unit", "pytest tests/unit", "passed", True)]),
                _structured_text(summary="Updated packaging/build surfaces for the client library.", commands=[("python -m build", 0, "build ok")], files_changed=[("pyproject.toml", "changed", "Updated packaging")]),
                _structured_text(summary="Recorded verification checkpoint for the client library.", commands=[("pytest tests/unit", 0, "unit passed"), ("pytest tests/integration", 0, "integration passed")], tests=[("pytest tests/unit", "pytest tests/unit", "passed", True), ("pytest tests/integration", "pytest tests/integration", "passed", True)]),
            ],
        },
    )


def _profile_compose_deploy_simple() -> ScenarioRuntimeProfile:
    return ScenarioRuntimeProfile(
        profile_id="compose_deploy_simple",
        direct_llm_scripts={
            "classification": [_classification(task="Update docker compose service", family=ExecutionFamily.HOST_OPERATION, intent="modify", capabilities=[Capability.HOST_ACCESS, Capability.SHELL_WRITE], focus=["inspect compose file", "inspect healthcheck"], needs_world=True)],
            "route_analysis": [_route(focus=["inspect compose file", "inspect healthcheck"])],
            "obligation_analysis": [_obligations(setup=["docker compose available"], completion=["compose update applied", "smoke check passed"], tests=["smoke"], docs=["compose note updated"])],
            "planning": [_plan(summary="Update docker compose and verify service health", family=ExecutionFamily.HOST_OPERATION, intent="modify", capabilities=[Capability.HOST_ACCESS, Capability.SHELL_WRITE], steps=["edit compose", "deploy", "run smoke"], success=["compose update applied", "smoke check passed"], checks=["smoke check passed", "compose update applied"], repo_changes=[], tests=["smoke"], env="host")],
            "verification": [_verification(passed=True, summary="Compose deployment and smoke evidence are present.", checks_passed=["compose update applied", "smoke check passed"], performed_test_levels=["smoke"])],
        },
        openhands_scripts={
            "observe": ["Observed docker-compose.yml and current service health endpoint."],
            "execute": [
                _structured_text(summary="Updated docker-compose.yml.", commands=[("docker compose config", 0, "ok")], files_changed=[("docker-compose.yml", "changed", "Updated compose service")]),
                _structured_text(summary="Executed smoke validation after compose update.", commands=[("curl http://localhost/health", 0, "200 OK")], tests=[("curl http://localhost/health", "curl http://localhost/health", "passed", True)]),
                _structured_text(summary="Updated deployment documentation note.", commands=[("printf deploy", 0, "note updated")], files_changed=[("README.md", "changed", "Updated deploy note")]),
                _structured_text(summary="Recorded compose verification checkpoint.", commands=[("curl http://localhost/health", 0, "200 OK")], postcheck_summary="curl /health returned 200 OK"),
            ],
        },
    )


def _profile_compose_deploy_postcheck() -> ScenarioRuntimeProfile:
    return ScenarioRuntimeProfile(
        profile_id="compose_deploy_postcheck",
        direct_llm_scripts={
            "classification": [_classification(task="Deploy compose change with postcheck", family=ExecutionFamily.HOST_OPERATION, intent="modify", capabilities=[Capability.HOST_ACCESS, Capability.SHELL_WRITE], focus=["inspect compose", "inspect postcheck path"], needs_world=True)],
            "route_analysis": [_route(focus=["inspect compose", "inspect postcheck path"])],
            "obligation_analysis": [_obligations(setup=["docker compose available"], completion=["compose update applied", "postcheck summary recorded"], tests=["smoke"], docs=["deploy note updated"])],
            "planning": [_plan(summary="Update compose service and run postcheck", family=ExecutionFamily.HOST_OPERATION, intent="modify", capabilities=[Capability.HOST_ACCESS, Capability.SHELL_WRITE], steps=["edit compose", "deploy", "run postcheck"], success=["compose update applied", "postcheck summary recorded"], checks=["postcheck summary recorded"], tests=["smoke"], env="host")],
            "verification": [_verification(passed=True, summary="Compose deployment and postcheck summary are present.", checks_passed=["compose update applied", "postcheck summary recorded"], performed_test_levels=["smoke"])],
        },
        openhands_scripts={
            "observe": ["Observed compose deployment path and postcheck endpoint."],
            "execute": [
                _structured_text(summary="Updated docker-compose.yml.", commands=[("docker compose config", 0, "ok")], files_changed=[("docker-compose.yml", "changed", "Updated compose service")]),
                _structured_text(summary="Executed post-deploy smoke check.", commands=[("curl http://localhost/health", 0, "200 OK")], tests=[("curl http://localhost/health", "curl http://localhost/health", "passed", True)]),
                _structured_text(summary="Updated deployment note for the compose service.", commands=[("printf deploy", 0, "note updated")], files_changed=[("README.md", "changed", "Updated deploy note")]),
                _structured_text(summary="Recorded postcheck summary for the compose deployment.", commands=[("curl http://localhost/health", 0, "200 OK")], postcheck_summary="curl /health returned 200 and background worker is alive"),
            ],
        },
    )


def _profile_host_blocked_missing_prerequisite() -> ScenarioRuntimeProfile:
    base = _profile_repo_integration_blocked()
    return ScenarioRuntimeProfile(
        profile_id="host_blocked_missing_prerequisite",
        direct_llm_scripts=base.direct_llm_scripts,
        openhands_scripts=base.openhands_scripts,
        auto_approve=True,
    )


def _profile_host_requires_approval() -> ScenarioRuntimeProfile:
    profile = _profile_compose_deploy_simple()
    return ScenarioRuntimeProfile(
        profile_id="host_change_requires_approval",
        direct_llm_scripts=profile.direct_llm_scripts,
        openhands_scripts=profile.openhands_scripts,
        auto_approve=False,
    )


PROFILES = {
    profile.profile_id: profile
    for profile in [
        _profile_repo_feature_simple(),
        _profile_repo_feature_with_docs(),
        _profile_repo_reentry_docs_gap(),
        _profile_repo_integration_blocked(),
        _profile_repo_publish_repair_success(),
        _profile_repo_ci_fix(),
        _profile_repo_client_library_addition(),
        _profile_compose_deploy_simple(),
        _profile_compose_deploy_postcheck(),
        _profile_host_blocked_missing_prerequisite(),
        _profile_host_requires_approval(),
    ]
}


def build_runtime_profile(profile_id: str) -> ScenarioRuntimeProfile:
    try:
        return PROFILES[profile_id]
    except KeyError as exc:
        raise KeyError(f"Unknown scenario runtime profile: {profile_id}") from exc

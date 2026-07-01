from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.controller import WorkflowController
from artifact_workflow_runtime.llm_backend import ScriptedLLMBackend
from artifact_workflow_runtime.model_routing import DEFAULT_CANONICAL_MODEL, load_model_routing_config
from artifact_workflow_runtime.models import FinalReport, Task, utc_now
from artifact_workflow_runtime.openhands_adapter import FakeOpenHandsAdapter
from artifact_workflow_runtime.policy import StaticApprovalProvider
from artifact_workflow_runtime.runtime_factory import build_controller

from .loader import load_scenarios
from .models import EvaluationRunReport, ScenarioRunRequest, ScenarioRunResult, ScenarioSpec
from .profiles import build_runtime_profile
from .reporting import render_markdown_report
from .scoring import score_scenario_run, summarize_pack


_LIVE_PUBLISH_TAGS = {"publish", "pr", "github", "deploy_shared"}


def _read_runtime_config(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(config_path)
    text = config_path.read_text(encoding="utf-8")
    if config_path.suffix.lower() in {".yaml", ".yml"}:
        import yaml

        data = yaml.safe_load(text) or {}
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"Runtime config {config_path} did not contain a mapping")
    return data


def _apply_runtime_config(request: ScenarioRunRequest) -> ScenarioRunRequest:
    data = _read_runtime_config(request.runtime_config_path)
    if not data:
        return request
    direct = data.get("direct_llm") if isinstance(data.get("direct_llm"), dict) else {}
    openhands = data.get("openhands") if isinstance(data.get("openhands"), dict) else {}
    update: dict[str, Any] = {}

    def set_if_missing(field: str, *keys: str, source: dict[str, Any] | None = None) -> None:
        if getattr(request, field) not in (None, "", False):
            return
        lookup = source if source is not None else data
        for key in keys:
            value = lookup.get(key) if isinstance(lookup, dict) else None
            if value not in (None, ""):
                update[field] = value
                return

    set_if_missing("direct_llm_endpoint", "direct_llm_endpoint", "endpoint", source=direct)
    set_if_missing("direct_llm_model", "direct_llm_model", "model", source=direct)
    set_if_missing("direct_llm_api_key", "direct_llm_api_key", "api_key", source=direct)
    set_if_missing("openhands_endpoint", "openhands_endpoint", "endpoint", source=openhands)
    set_if_missing("openhands_model", "openhands_model", "model", source=openhands)
    set_if_missing("openhands_api_key", "openhands_api_key", "api_key", source=openhands)
    for field in ("sandbox_id", "conversation_id", "reuse_mode", "strategy_selection_mode", "model_routing_config_path"):
        set_if_missing(field, field)
    for field in ("approve_live", "allow_live_network", "allow_live_host", "allow_live_publish", "auto_approve"):
        if getattr(request, field) is False and field in data:
            update[field] = bool(data[field])
    return request.model_copy(update=update) if update else request


def _collect_evidence_names_from_artifacts(store: ArtifactStore) -> list[str]:
    names: set[str] = set()
    for artifact in store.list():
        if artifact.media_type != "application/json":
            continue
        try:
            payload = store.read_json(artifact.id)
        except Exception:
            continue
        stack = [payload]
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                structured = current.get("structured_evidence") or current.get("structured")
                if isinstance(structured, dict):
                    if structured.get("commands_run"):
                        names.add("commands_run")
                    if structured.get("files_changed"):
                        names.add("files_changed")
                    if structured.get("files_observed"):
                        names.add("files_observed")
                    if structured.get("tests"):
                        names.add("tests")
                    if structured.get("blockers"):
                        names.add("blockers")
                    postcheck = structured.get("postcheck_summary")
                    if isinstance(postcheck, dict) and (postcheck.get("attempted") or postcheck.get("summary")):
                        names.add("postcheck_summary")
                if current.get("pr_checks_passed") or current.get("pr_checks_failed") or current.get("pr_checks_pending"):
                    names.add("pr_checks")
                stack.extend(current.values())
            elif isinstance(current, list):
                stack.extend(current)
    return sorted(names)


def _collect_blockers_from_artifacts(store: ArtifactStore) -> list[str]:
    blockers: list[str] = []

    def add(value: object) -> None:
        text = str(value or "").strip()
        if text and text not in blockers:
            blockers.append(text)

    for artifact in store.list():
        if artifact.media_type != "application/json":
            continue
        try:
            payload = store.read_json(artifact.id)
        except Exception:
            continue
        stack: list[Any] = [payload]
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                if "blocker_kind" in current:
                    add(current.get("blocker_kind"))
                if "kind" in current and "reason" in current and ("blocker" in str(current.get("kind")).lower() or "environment" in str(current.get("kind")).lower()):
                    add(f"{current.get('kind')}: {current.get('reason')}")
                structured = current.get("structured_evidence") or current.get("structured")
                if isinstance(structured, dict):
                    for blocker in structured.get("blockers") or []:
                        if isinstance(blocker, dict):
                            add(blocker.get("kind") or blocker.get("blocker_kind") or blocker.get("reason"))
                        else:
                            add(blocker)
                stack.extend(current.values())
            elif isinstance(current, list):
                stack.extend(current)
    return blockers


class LiveScenarioGate:
    """Live-safety boundary for evaluation runs.

    This is deliberately small and explicit: live evaluation is allowed only when
    the scenario declares its safety posture and the request grants the required
    approvals/capabilities. The actual execution still goes through the normal
    runtime controller.
    """

    def evaluate(self, spec: ScenarioSpec, request: ScenarioRunRequest) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if request.mode != "live":
            return True, reasons
        if spec.unsafe_for_live:
            reasons.append("scenario is marked unsafe_for_live")
        if spec.dry_run_only:
            reasons.append("scenario is marked dry_run_only")
        if spec.requires_approval_for_live and not request.approve_live:
            reasons.append("scenario requires explicit live approval")
        if not spec.safe_for_live and not spec.requires_approval_for_live:
            reasons.append("scenario has no explicit live-safety classification")
        if spec.requires_live_host and not request.allow_live_host:
            reasons.append("scenario requires live host access but --allow-live-host was not set")
        if spec.requires_live_network and not request.allow_live_network:
            reasons.append("scenario requires live network access but --allow-live-network was not set")
        if spec.needs_isolated_repo and request.reuse_mode != "isolated":
            reasons.append("scenario requires an isolated repository workspace/sandbox")
        if spec.requires_live_openhands and not request.openhands_endpoint:
            reasons.append("scenario requires live OpenHands endpoint")
        if any(tag in _LIVE_PUBLISH_TAGS for tag in spec.tags) and not request.allow_live_publish:
            reasons.append("publish/deploy scenario requires --allow-live-publish")
        if not request.direct_llm_endpoint:
            reasons.append("live mode requires a direct LLM endpoint")
        if not request.openhands_endpoint:
            reasons.append("live mode requires an OpenHands endpoint")
        return not reasons, reasons


class LiveScenarioRunner:
    def __init__(self, *, gate: LiveScenarioGate | None = None) -> None:
        self.gate = gate or LiveScenarioGate()

    async def run_scenario(self, spec: ScenarioSpec, request: ScenarioRunRequest) -> ScenarioRunResult:
        request = _apply_runtime_config(request)
        allowed, reasons = self.gate.evaluate(spec, request)
        run_dir = Path(request.artifact_dir) / spec.scenario_id
        run_dir.mkdir(parents=True, exist_ok=True)
        artifact_dir = run_dir / "artifacts"
        started = ScenarioRunResult(
            scenario_id=spec.scenario_id,
            terminal_status="started",
            runtime_status="started",
            execution_mode="live",
            artifact_dir=str(run_dir),
            live_artifact_dir=str(artifact_dir),
            live_metadata={
                "safety": spec.live_safety(),
                "environment_profile": spec.live_environment_profile or spec.environment_profile,
                "requires_live_repo": spec.requires_live_repo,
                "requires_live_host": spec.requires_live_host,
                "requires_live_openhands": spec.requires_live_openhands,
                "requires_live_network": spec.requires_live_network,
                "needs_isolated_repo": spec.needs_isolated_repo,
                "needs_isolated_host": spec.needs_isolated_host,
                "live_notes": spec.live_notes,
            },
        )
        if not allowed:
            result = started.model_copy(update={
                "finished_at": utc_now(),
                "terminal_status": "live_gated",
                "runtime_status": "live_gated",
                "fail_reasons": reasons,
                "final_report": {
                    "task_id": request.scenario_id,
                    "status": "live_gated",
                    "summary": "; ".join(reasons),
                    "artifact_ids": [],
                },
            })
            result.scorecard = score_scenario_run(spec, result)
            return result

        controller = build_controller(
            artifact_dir=str(artifact_dir),
            direct_llm_endpoint=str(request.direct_llm_endpoint),
            direct_llm_model=request.direct_llm_model or DEFAULT_CANONICAL_MODEL,
            direct_llm_api_key=request.direct_llm_api_key,
            openhands_endpoint=str(request.openhands_endpoint),
            openhands_model=request.openhands_model or DEFAULT_CANONICAL_MODEL,
            openhands_api_key=request.openhands_api_key,
            reuse=request.reuse_mode != "isolated",
            sandbox_id=request.sandbox_id,
            conversation_id=request.conversation_id,
            auto_approve=request.auto_approve and request.approve_live,
            config_path=request.model_routing_config_path,
            strategy_selection_mode=request.strategy_selection_mode,
        )
        task = Task(
            title=spec.title,
            description=spec.task_text_for_mode("live"),
            metadata={
                "scenario_id": spec.scenario_id,
                "evaluation_mode": "live",
                "tags": spec.tags,
                "environment_profile": spec.live_environment_profile or spec.environment_profile,
                "environment_overrides": request.environment_overrides,
            },
        )
        try:
            report = await asyncio.wait_for(controller.run(task), timeout=spec.timeout_for_mode(request.timeout_seconds, "live"))
        except Exception as exc:  # noqa: BLE001
            store = controller.artifact_store
            result = started.model_copy(update={
                "finished_at": utc_now(),
                "terminal_status": "runner_error",
                "runtime_status": "runner_error",
                "fail_reasons": [str(exc)],
                "artifacts": [artifact.id for artifact in store.list()],
                "final_report": {"task_id": task.id, "status": "runner_error", "summary": str(exc)},
                "required_evidence_found": _collect_evidence_names_from_artifacts(store),
                "blockers": _collect_blockers_from_artifacts(store),
                "live_run_id": task.id,
            })
            result.scorecard = score_scenario_run(spec, result)
            return result

        result = ScenarioRunner._build_result_static(
            spec,
            started,
            report,
            controller.artifact_store,
            mode="live",
            artifact_dir=run_dir,
            live_run_id=task.id,
        )
        result.scorecard = score_scenario_run(spec, result)
        return result


class ScenarioRunner:
    def __init__(self, *, live_runner: LiveScenarioRunner | None = None) -> None:
        self.live_runner = live_runner or LiveScenarioRunner()

    async def run_scenario(self, spec: ScenarioSpec, request: ScenarioRunRequest) -> ScenarioRunResult:
        if request.mode == "live":
            result = await self.live_runner.run_scenario(spec, request)
            self._write_run_outputs(spec, result, request)
            return result
        return await self._run_scripted_scenario(spec, request)

    async def _run_scripted_scenario(self, spec: ScenarioSpec, request: ScenarioRunRequest) -> ScenarioRunResult:
        started = ScenarioRunResult(
            scenario_id=spec.scenario_id,
            terminal_status="started",
            runtime_status="started",
            execution_mode="scripted",
            artifact_dir=str(Path(request.artifact_dir) / spec.scenario_id),
        )
        run_dir = Path(request.artifact_dir) / spec.scenario_id
        run_dir.mkdir(parents=True, exist_ok=True)
        artifact_store = ArtifactStore(run_dir / "artifacts")
        profile = build_runtime_profile(spec.target_runtime_profile)
        llm = ScriptedLLMBackend(profile.direct_llm_scripts)
        openhands = FakeOpenHandsAdapter(artifact_store, scripts=profile.openhands_scripts)
        controller = WorkflowController(
            llm_backend=llm,
            openhands_adapter=openhands,
            artifact_root=artifact_store.root,
            approval_provider=StaticApprovalProvider(approve=request.auto_approve and profile.auto_approve, reviewer="eval"),
            model_routing=load_model_routing_config(request.model_routing_config_path) if request.model_routing_config_path else None,
        )
        task = Task(title=spec.title, description=spec.task_text, metadata={"scenario_id": spec.scenario_id, "tags": spec.tags, "evaluation_mode": "scripted"})
        try:
            report = await asyncio.wait_for(controller.run(task), timeout=request.timeout_seconds)
        except Exception as exc:  # noqa: BLE001
            finished = started.model_copy(update={
                "finished_at": utc_now(),
                "terminal_status": "runner_error",
                "runtime_status": "runner_error",
                "fail_reasons": [str(exc)],
                "artifacts": [artifact.id for artifact in artifact_store.list()],
                "final_report": {"task_id": task.id, "status": "runner_error", "summary": str(exc)},
                "required_evidence_found": _collect_evidence_names_from_artifacts(artifact_store),
                "blockers": _collect_blockers_from_artifacts(artifact_store),
            })
            finished.scorecard = score_scenario_run(spec, finished)
            self._write_run_outputs(spec, finished, request)
            return finished

        result = self._build_result(spec, started, report, artifact_store, mode="scripted", artifact_dir=run_dir)
        result.scorecard = score_scenario_run(spec, result)
        self._write_run_outputs(spec, result, request)
        return result

    async def run_pack(self, path: str | Path, *, request_factory=None, artifact_dir: str = "eval_runs", mode: str = "scripted") -> EvaluationRunReport:
        scenarios = load_scenarios(path)
        results: list[ScenarioRunResult] = []
        pack_id = Path(path).stem or Path(path).name
        for spec in scenarios:
            request = request_factory(spec) if request_factory is not None else ScenarioRunRequest(scenario_id=spec.scenario_id, artifact_dir=artifact_dir, mode=mode)  # type: ignore[arg-type]
            results.append(await self.run_scenario(spec, request))
        mode_counts: dict[str, int] = {}
        for item in results:
            mode_counts[item.execution_mode] = mode_counts.get(item.execution_mode, 0) + 1
        report = EvaluationRunReport(
            pack_id=pack_id,
            scenario_results=results,
            summary=summarize_pack(pack_id, results),
            execution_mode=next(iter(mode_counts), mode) if len(mode_counts) <= 1 else "mixed",
            model_routing_config_path=None,
            runtime_config_path=None,
            notes=[f"mode_counts={mode_counts}"],
        )
        output_dir = Path(artifact_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / f"{pack_id}.json").write_text(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / f"{pack_id}.md").write_text(render_markdown_report(report), encoding="utf-8")
        return report

    def _write_run_outputs(self, spec: ScenarioSpec, result: ScenarioRunResult, request: ScenarioRunRequest) -> None:
        run_dir = Path(request.artifact_dir) / spec.scenario_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "scenario_result.json").write_text(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")

    def _build_result(self, spec: ScenarioSpec, started: ScenarioRunResult, report: FinalReport, artifact_store: ArtifactStore, *, mode: str, artifact_dir: Path, live_run_id: str | None = None) -> ScenarioRunResult:
        return self._build_result_static(spec, started, report, artifact_store, mode=mode, artifact_dir=artifact_dir, live_run_id=live_run_id)

    @staticmethod
    def _build_result_static(spec: ScenarioSpec, started: ScenarioRunResult, report: FinalReport, artifact_store: ArtifactStore, *, mode: str, artifact_dir: Path, live_run_id: str | None = None) -> ScenarioRunResult:
        artifacts = artifact_store.list()
        checkpoints = [item for item in artifacts if item.kind == "workflow_checkpoint"]
        stage_sequence = [str(item.metadata.get("stage") or "") for item in checkpoints]
        packet_selection_artifacts = [item for item in artifacts if item.kind == "packet_selection"]
        selected_packets: list[str] = []
        for item in packet_selection_artifacts:
            try:
                payload = artifact_store.read_json(item.id)
            except Exception:
                continue
            packet_id = payload.get("selected_packet_id") if isinstance(payload, dict) else None
            if packet_id and packet_id not in selected_packets:
                selected_packets.append(str(packet_id))
        decomposition_artifacts = [item for item in artifacts if item.kind == "decomposition_plan"]
        packet_types: list[str] = []
        if decomposition_artifacts:
            try:
                payload = artifact_store.read_json(decomposition_artifacts[-1].id)
                if isinstance(payload, dict):
                    for packet in payload.get("packets") or []:
                        if isinstance(packet, dict):
                            packet_type = packet.get("packet_type")
                            if packet_type and packet_type not in packet_types:
                                packet_types.append(str(packet_type))
            except Exception:
                pass
        state_snapshots = [item for item in artifacts if item.kind == "workflow_state_snapshot"]
        reentry_count = 0
        repair_count = len(report.repair_results)
        if state_snapshots:
            try:
                snapshot = artifact_store.read_json(state_snapshots[-1].id)
                reentry_count = len(snapshot.get("pipeline_loop_decisions") or []) if isinstance(snapshot, dict) else 0
            except Exception:
                pass
        terminal_status = report.status
        acceptance_status = report.acceptance_decision.status.value if report.acceptance_decision is not None else (report.verification.acceptance_status.value if report.verification and report.verification.acceptance_status is not None else None)
        return started.model_copy(update={
            "finished_at": utc_now(),
            "terminal_status": terminal_status,
            "runtime_status": terminal_status,
            "execution_mode": mode,
            "acceptance_status": acceptance_status,
            "packet_count": len(selected_packets) or len(packet_types),
            "transition_count": len(stage_sequence),
            "reentry_count": reentry_count,
            "repair_count": repair_count,
            "artifacts": [item.id for item in artifacts],
            "artifact_dir": str(artifact_dir),
            "live_artifact_dir": str(artifact_store.root) if mode == "live" else None,
            "live_run_id": live_run_id if mode == "live" else None,
            "final_report": report.model_dump(mode="json"),
            "stage_sequence": stage_sequence,
            "packet_types": packet_types,
            "fail_reasons": [],
            "required_evidence_found": _collect_evidence_names_from_artifacts(artifact_store),
            "blockers": _collect_blockers_from_artifacts(artifact_store),
        })

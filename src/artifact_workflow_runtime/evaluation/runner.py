from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.controller import WorkflowController
from artifact_workflow_runtime.llm_backend import ScriptedLLMBackend
from artifact_workflow_runtime.model_routing import load_model_routing_config
from artifact_workflow_runtime.models import FinalReport, Task, utc_now
from artifact_workflow_runtime.openhands_adapter import FakeOpenHandsAdapter
from artifact_workflow_runtime.policy import StaticApprovalProvider

from .loader import load_scenario_spec, load_scenarios
from .models import EvaluationRunReport, ScenarioRunRequest, ScenarioRunResult, ScenarioSpec
from .profiles import build_runtime_profile
from .reporting import render_markdown_report
from .scoring import score_scenario_run, summarize_pack




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


class ScenarioRunner:
    async def run_scenario(self, spec: ScenarioSpec, request: ScenarioRunRequest) -> ScenarioRunResult:
        started = ScenarioRunResult(scenario_id=spec.scenario_id, terminal_status="started", runtime_status="started")
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
        task = Task(title=spec.title, description=spec.task_text, metadata={"scenario_id": spec.scenario_id, "tags": spec.tags})
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
            })
            finished.scorecard = score_scenario_run(spec, finished)
            self._write_run_outputs(spec, finished, request)
            return finished

        result = self._build_result(spec, started, report, artifact_store)
        result.scorecard = score_scenario_run(spec, result)
        self._write_run_outputs(spec, result, request)
        return result

    async def run_pack(self, path: str | Path, *, request_factory=None, artifact_dir: str = "eval_runs") -> EvaluationRunReport:
        scenarios = load_scenarios(path)
        results: list[ScenarioRunResult] = []
        pack_id = Path(path).stem or Path(path).name
        for spec in scenarios:
            request = request_factory(spec) if request_factory is not None else ScenarioRunRequest(scenario_id=spec.scenario_id, artifact_dir=artifact_dir)
            results.append(await self.run_scenario(spec, request))
        report = EvaluationRunReport(
            pack_id=pack_id,
            scenario_results=results,
            summary=summarize_pack(pack_id, results),
            model_routing_config_path=(results and request_factory is None and None) or None,
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

    def _build_result(self, spec: ScenarioSpec, started: ScenarioRunResult, report: FinalReport, artifact_store: ArtifactStore) -> ScenarioRunResult:
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
            "acceptance_status": acceptance_status,
            "packet_count": len(selected_packets) or len(packet_types),
            "transition_count": len(stage_sequence),
            "reentry_count": reentry_count,
            "repair_count": repair_count,
            "artifacts": [item.id for item in artifacts],
            "final_report": report.model_dump(mode="json"),
            "stage_sequence": stage_sequence,
            "packet_types": packet_types,
            "fail_reasons": [],
            "required_evidence_found": _collect_evidence_names_from_artifacts(artifact_store),
        })

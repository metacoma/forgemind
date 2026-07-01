from __future__ import annotations

import json
from pathlib import Path

from artifact_workflow_runtime.runtime_events import RuntimeEvent
from artifact_workflow_runtime.tui.presentation import CockpitInputs, build_cockpit_view


class _Verification:
    def __init__(self, **payload):
        self._payload = payload

    def model_dump(self, mode: str = "json"):
        return dict(self._payload)


class _FinalReport:
    def __init__(self, *, status: str = "completed", summary: str = "done", verification=None, obligations=None, acceptance_decision=None, publish=None, plan=None):
        self.status = status
        self.summary = summary
        self.verification = verification
        self.obligations = obligations
        self.acceptance_decision = acceptance_decision
        self.publish = publish
        self.plan = plan


def _write_artifact(tmp_path: Path, kind: str, payload: dict) -> dict:
    path = tmp_path / f"{kind}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return {"id": kind, "kind": kind, "path": str(path), "preview": "", "metadata": {}}


def test_cockpit_view_surfaces_active_packet_and_decomposition_status(tmp_path: Path) -> None:
    artifacts = [
        _write_artifact(
            tmp_path,
            "decomposition_plan",
            {
                "plan_id": "plan-1",
                "task_summary": "Implement feature X",
                "strategy_id": "mvp_first",
                "complexity": "medium",
                "packets": [
                    {
                        "packet_id": "pkt-1",
                        "title": "Implement slice",
                        "goal": "Build the vertical slice",
                        "scope": "backend",
                        "packet_type": "implementation",
                        "strategy_id": "mvp_first",
                        "status": "completed",
                        "dependencies": [],
                        "allowed_files": ["src/app.py"],
                        "target_areas": ["backend"],
                        "forbidden_actions": ["publish"],
                        "success_criteria": ["slice compiles"],
                        "required_evidence": ["changed_files"],
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "updated_at": "2026-01-01T00:00:00+00:00",
                        "metadata": {},
                    },
                    {
                        "packet_id": "pkt-2",
                        "title": "Add tests",
                        "goal": "Cover the slice",
                        "scope": "tests",
                        "packet_type": "test",
                        "strategy_id": "mvp_first",
                        "status": "pending",
                        "dependencies": ["pkt-1"],
                        "allowed_files": ["tests/test_app.py"],
                        "target_areas": ["tests"],
                        "forbidden_actions": [],
                        "success_criteria": ["tests pass"],
                        "required_evidence": ["test_results"],
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "updated_at": "2026-01-01T00:00:00+00:00",
                        "metadata": {},
                    },
                ],
                "risks": [],
                "assumptions": [],
                "decomposition_reason": "Use packets",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "metadata": {},
            },
        ),
        _write_artifact(
            tmp_path,
            "packet_selection",
            {
                "selected_packet_id": "pkt-2",
                "reason": "Implementation packet already completed; tests are next.",
                "ready": True,
                "blocked_reason": None,
                "pending_dependencies": [],
            },
        ),
    ]
    events = [
        RuntimeEvent(kind="stage_started", stage="plan", message="Planning"),
        RuntimeEvent(kind="stage_started", stage="execute", message="Run packet 1"),
        RuntimeEvent(kind="stage_started", stage="review", message="Packet 1 review"),
        RuntimeEvent(kind="stage_started", stage="execute", message="Run packet 2"),
    ]
    view = build_cockpit_view(
        CockpitInputs(
            current_stage="execute",
            current_status="running packet pkt-2",
            last_error=None,
            stage_status={"plan": "done", "execute": "running", "review": "done"},
            stage_message={"plan": "Planning", "execute": "Run packet 2", "review": "Packet 1 review"},
            stage_started_at={"plan": "t1", "execute": "t4", "review": "t3"},
            stage_payloads={},
            artifact_rows=artifacts,
            transport_state={"mode": "reuse", "event_count": 4},
            event_history=events,
            final_report=None,
            configured_direct_model="qwen36-35b",
            configured_openhands_model="qwen36-27b",
        )
    )
    assert any(row.packet_id == "pkt-2" and row.selection_reason for row in view.packet_rows)
    assert "pkt-2" in view.packet_detail_text
    assert "re-entered: execute" in view.pipeline_text
    assert "selected_packet: pkt-2" in view.decision_text


def test_cockpit_view_shows_obligations_and_backend_split(tmp_path: Path) -> None:
    verification = _Verification(
        passed=False,
        missing_obligations=["docs updated", "integration test evidence"],
        missing_test_levels=["integration_tests"],
        checks_failed=["docs"],
        missing_evidence=["README diff"],
        completion_status="needs_human_review",
        primary_evidence_artifact_ids=["ev-1"],
    )
    obligations = _Verification(
        required_documentation_updates=["README.md"],
        required_test_levels=["integration_tests"],
        required_setup_steps=["docker compose up"],
        required_ci_updates=["github actions"],
        discovered_impacts=["helm chart"],
    )
    final_report = _FinalReport(status="needs_human_review", summary="missing docs", verification=verification, obligations=obligations)
    view = build_cockpit_view(
        CockpitInputs(
            current_stage="verify",
            current_status="checking evidence",
            last_error=None,
            stage_status={"verify": "running", "publish": "pending"},
            stage_message={"verify": "checking evidence", "publish": "pending"},
            stage_started_at={"verify": "t1", "publish": ""},
            stage_payloads={},
            artifact_rows=[],
            transport_state={"mode": "reuse", "conversation_id": "conv-1", "sandbox_id": "sb-1", "event_count": 3, "followups": 1, "last_status": "working"},
            event_history=[RuntimeEvent(kind="stage_started", stage="verify", message="checking evidence")],
            final_report=final_report,
            configured_direct_model="qwen36-35b",
            configured_openhands_model="qwen36-27b",
        )
    )
    assert "docs -> README.md" in view.obligations_text
    assert "missing_obligations: 2" in view.obligations_text
    assert "current_backend: Direct LLM" in view.backend_card
    assert "openhands_model: qwen36-27b" in view.backend_card
    assert "README diff" in view.blockers_text
    assert "primary_evidence_ids: 1" in view.evidence_text

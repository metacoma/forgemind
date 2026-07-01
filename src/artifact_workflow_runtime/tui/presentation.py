from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from artifact_workflow_runtime.graph.topology import PIPELINE_NODE_ORDER

from .evidence import latest_artifact_payload


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(mode="json")
            return dumped if isinstance(dumped, dict) else {}
        except Exception:
            return {}
    return {}


def _safe_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _text_list(items: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out


@dataclass(slots=True)
class PacketRow:
    packet_id: str
    title: str
    packet_type: str
    status: str
    dependencies: str
    selection_reason: str


@dataclass(slots=True)
class CockpitView:
    run_card: str
    backend_card: str
    acceptance_card: str
    pipeline_text: str
    decision_text: str
    packet_rows: list[PacketRow]
    packet_detail_text: str
    packet_details: dict[str, str]
    obligations_text: str
    blockers_text: str
    evidence_text: str
    backend_activity_text: str
    stage_rows: list[tuple[str, str, str, str]]


@dataclass(slots=True)
class CockpitInputs:
    current_stage: str
    current_status: str
    last_error: str | None
    stage_status: Mapping[str, str]
    stage_message: Mapping[str, str]
    stage_started_at: Mapping[str, str]
    stage_payloads: Mapping[str, Mapping[str, Any]]
    artifact_rows: list[dict[str, Any]]
    transport_state: Mapping[str, Any]
    event_history: list[Any]
    final_report: Any | None
    configured_direct_model: str | None = None
    configured_openhands_model: str | None = None


def build_cockpit_view(inputs: CockpitInputs) -> CockpitView:
    final_report = inputs.final_report
    decomposition_plan = _mapping(latest_artifact_payload(inputs.artifact_rows, "decomposition_plan"))
    packet_selection = _mapping(latest_artifact_payload(inputs.artifact_rows, "packet_selection"))
    packet_status_update = _mapping(latest_artifact_payload(inputs.artifact_rows, "packet_status_update"))
    acceptance_contract = _mapping(latest_artifact_payload(inputs.artifact_rows, "task_acceptance_contract"))
    controller_decision = _mapping(latest_artifact_payload(inputs.artifact_rows, "controller_decision"))
    verification = _mapping(getattr(final_report, "verification", None))
    obligations = _mapping(getattr(final_report, "obligations", None))
    acceptance = _mapping(getattr(final_report, "acceptance_decision", None))
    publish = _mapping(getattr(final_report, "publish", None))
    plan = _mapping(getattr(final_report, "plan", None))

    packet_rows, packet_detail, packet_details = _packet_views(
        decomposition_plan=decomposition_plan,
        packet_selection=packet_selection,
        packet_status_update=packet_status_update,
        current_stage=inputs.current_stage,
    )
    return CockpitView(
        run_card=_run_card(inputs, decomposition_plan, packet_rows),
        backend_card=_backend_card(inputs),
        acceptance_card=_acceptance_card(inputs, verification, acceptance, publish),
        pipeline_text=_pipeline_text(inputs),
        decision_text=_decision_text(inputs, packet_selection, controller_decision, verification, acceptance),
        packet_rows=packet_rows,
        packet_detail_text=packet_detail,
        packet_details=packet_details,
        obligations_text=_obligations_text(obligations, verification, acceptance_contract, decomposition_plan, plan),
        blockers_text=_blockers_text(inputs, verification, acceptance),
        evidence_text=_evidence_text(inputs, verification),
        backend_activity_text=_backend_activity_text(inputs),
        stage_rows=_stage_rows(inputs),
    )


def build_stage_detail(stage: str, stage_status: Mapping[str, str], stage_started_at: Mapping[str, str], stage_message: Mapping[str, str], stage_payloads: Mapping[str, Mapping[str, Any]], final_report: Any | None) -> str:
    parts = [
        f"Stage: {stage}",
        f"Status: {stage_status.get(stage, 'pending')}",
        f"Last update: {stage_started_at.get(stage, '')}",
        "",
        "Message:",
        stage_message.get(stage, ''),
    ]
    payload = stage_payloads.get(stage) or {}
    if payload:
        import json

        parts += ["", "Latest event payload:", json.dumps(payload, ensure_ascii=False, indent=2)]
    if final_report is not None:
        model = _mapping(getattr(final_report, {
            "route": "route",
            "research": "research",
            "observe": "observation",
            "obligations": "obligations",
            "plan": "plan",
            "publish": "publish",
            "verify": "verification",
        }.get(stage, ""), None))
        if model:
            import json

            parts += ["", "Final report context:", json.dumps(model, ensure_ascii=False, indent=2)]
        elif stage == "finalize":
            import json

            parts += ["", "Final report summary:", json.dumps(_mapping(final_report), ensure_ascii=False, indent=2)]
    return "\n".join(str(part) for part in parts if part is not None)


def _run_card(inputs: CockpitInputs, decomposition_plan: dict[str, Any], packet_rows: list[PacketRow]) -> str:
    lines = [
        "Run",
        "",
        f"current_stage: {inputs.current_stage}",
        f"status: {inputs.current_status}",
        f"stages_seen: {sum(1 for value in inputs.stage_status.values() if value != 'pending')}",
        f"artifacts: {len(inputs.artifact_rows)}",
    ]
    if decomposition_plan:
        lines.append(f"packets: {len(_safe_list(decomposition_plan.get('packets')))}")
    if packet_rows:
        active = next((row.packet_id for row in packet_rows if row.status in {"in_progress", "pending"}), packet_rows[0].packet_id)
        lines.append(f"active_packet: {active}")
    if inputs.last_error:
        lines.append(f"last_error: {inputs.last_error[:80]}")
    return "\n".join(lines)


def _backend_card(inputs: CockpitInputs) -> str:
    transport = inputs.transport_state
    direct_stages = _text_list(stage for stage, status in inputs.stage_status.items() if status != "pending" and stage in {"classify", "route", "obligations", "done_contract", "plan", "policy", "approval", "qa_plan", "qa_review", "acceptance", "finalize"})
    openhands_stages = _text_list(stage for stage, status in inputs.stage_status.items() if status != "pending" and stage in {"research", "observe", "workspace_prepare", "execute", "repair", "qa_execute", "publish", "post_publish_verify"})
    lines = [
        "Backend split",
        "",
        f"current_backend: {_current_backend(inputs.current_stage)}",
        f"direct_model: {inputs.configured_direct_model or 'qwen36-35b'}",
        f"openhands_model: {inputs.configured_openhands_model or 'qwen36-35b'}",
        f"conversation: {transport.get('conversation_id', '') or 'n/a'}",
        f"sandbox: {transport.get('sandbox_id', '') or 'n/a'}",
        f"direct_stages: {', '.join(direct_stages[-4:]) or 'none'}",
        f"openhands_stages: {', '.join(openhands_stages[-4:]) or 'none'}",
    ]
    return "\n".join(lines)


def _acceptance_card(inputs: CockpitInputs, verification: dict[str, Any], acceptance: dict[str, Any], publish: dict[str, Any]) -> str:
    lines = ["Acceptance / finish", ""]
    final = inputs.final_report
    if final is None:
        lines.append("final_status: pending")
    else:
        lines.append(f"final_status: {getattr(final, 'status', 'unknown')}")
        summary = str(getattr(final, 'summary', '') or '').strip()
        if summary:
            lines.append(f"summary: {summary[:80]}")
    if verification:
        lines.append(f"verification: {'PASS' if verification.get('passed') else 'FAIL'}")
        lines.append(f"missing_obligations: {len(_safe_list(verification.get('missing_obligations')))}")
        lines.append(f"missing_tests: {len(_safe_list(verification.get('missing_test_levels')))}")
    if acceptance:
        lines.append(f"acceptance: {'accepted' if acceptance.get('accepted') else acceptance.get('status', 'pending')}")
    if publish:
        lines.append(f"publish_ok: {publish.get('ok')}")
    return "\n".join(lines)


def _pipeline_text(inputs: CockpitInputs) -> str:
    starts = [event.stage for event in inputs.event_history if getattr(event, 'kind', '') == 'stage_started']
    start_counts: dict[str, int] = {}
    for stage in starts:
        start_counts[stage] = start_counts.get(stage, 0) + 1
    lines = ["Pipeline / lifecycle", ""]
    for stage in PIPELINE_NODE_ORDER:
        status = inputs.stage_status.get(stage, 'pending')
        marker = {
            'done': '✔',
            'running': '▶',
            'failed': '✖',
            'blocked': '⛔',
            'pending': '·',
        }.get(status, '·')
        suffix = f" ↺{start_counts[stage]}" if start_counts.get(stage, 0) > 1 else ""
        message = str(inputs.stage_message.get(stage, '') or '')
        trimmed = f" — {message[:70]}" if message else ""
        lines.append(f"{marker} {stage}{suffix}{trimmed}")
    reentry = _reentry_summary(inputs.event_history)
    if reentry:
        lines += ["", "Re-entry / loop", reentry]
    return "\n".join(lines)


def _decision_text(inputs: CockpitInputs, packet_selection: dict[str, Any], controller_decision: dict[str, Any], verification: dict[str, Any], acceptance: dict[str, Any]) -> str:
    lines = ["Current control-plane decision", ""]
    last_event = next((event for event in reversed(inputs.event_history) if getattr(event, 'stage', '') != 'transport'), None)
    if last_event is not None:
        lines.append(f"last_transition: {last_event.stage} / {last_event.kind}")
        lines.append(f"why_now: {last_event.message}")
    if controller_decision:
        lines += [
            f"selected_next_stage: {controller_decision.get('selected_next_stage', 'n/a')}",
            f"controller_reason: {str(controller_decision.get('reason', ''))[:160]}",
        ]
    if packet_selection:
        lines += [
            f"selected_packet: {packet_selection.get('selected_packet_id') or 'none'}",
            f"packet_reason: {str(packet_selection.get('reason', ''))[:160]}",
        ]
    if verification:
        lines.append(f"verification_status: {verification.get('completion_status') or ('pass' if verification.get('passed') else 'needs_work')}")
    if acceptance:
        lines.append(f"acceptance_gate: {acceptance.get('status') or ('accepted' if acceptance.get('accepted') else 'pending')}")
    loop_text = _reentry_summary(inputs.event_history)
    if loop_text:
        lines += ["", "loop trigger", loop_text]
    return "\n".join(lines)


def _packet_views(decomposition_plan: dict[str, Any], packet_selection: dict[str, Any], packet_status_update: dict[str, Any], current_stage: str) -> tuple[list[PacketRow], str, dict[str, str]]:
    packets = _safe_list(decomposition_plan.get('packets'))
    selection_reason = str(packet_selection.get('reason') or '')
    rows: list[PacketRow] = []
    details: dict[str, str] = {}
    active_packet_id = packet_selection.get('selected_packet_id') or packet_status_update.get('packet_id')
    for packet in packets:
        if not isinstance(packet, dict):
            continue
        rows.append(
            PacketRow(
                packet_id=str(packet.get('packet_id') or ''),
                title=str(packet.get('title') or ''),
                packet_type=str(packet.get('packet_type') or ''),
                status=str(packet.get('status') or 'pending'),
                dependencies=','.join(_text_list(packet.get('dependencies') or [])) or '-',
                selection_reason=selection_reason if packet.get('packet_id') == active_packet_id else '',
            )
        )
    detail = "No decomposition packets have been materialized yet."
    if rows:
        packet = next((item for item in packets if isinstance(item, dict) and item.get('packet_id') == active_packet_id), packets[0])
        packet_map = packet if isinstance(packet, dict) else {}
        success = _text_list(packet_map.get('success_criteria') or [])
        evidence = _text_list(packet_map.get('required_evidence') or [])
        detail_lines = [
            f"packet_id: {packet_map.get('packet_id', '')}",
            f"title: {packet_map.get('title', '')}",
            f"type: {packet_map.get('packet_type', '')}",
            f"status: {packet_map.get('status', '')}",
            f"scope: {packet_map.get('scope', '')}",
            f"goal: {packet_map.get('goal', '')}",
            f"dependencies: {', '.join(_text_list(packet_map.get('dependencies') or [])) or '-'}",
            f"target_areas: {', '.join(_text_list(packet_map.get('target_areas') or [])) or '-'}",
            f"allowed_files: {', '.join(_text_list(packet_map.get('allowed_files') or [])) or '-'}",
            f"forbidden_actions: {', '.join(_text_list(packet_map.get('forbidden_actions') or [])) or '-'}",
            "",
            "success criteria:",
            *([f"- {item}" for item in success] or ["- n/a"]),
            "",
            "required evidence:",
            *([f"- {item}" for item in evidence] or ["- n/a"]),
        ]
        if selection_reason and packet_map.get('packet_id') == active_packet_id:
            detail_lines += ["", f"selection_reason: {selection_reason}"]
        if current_stage == 'execute' and packet_map.get('packet_id') == active_packet_id:
            detail_lines += ["", "packet_state: runtime is executing or preparing this bounded packet."]
        details[str(packet_map.get('packet_id') or '')] = "\n".join(detail_lines)
        if str(packet_map.get('packet_id') or '') == str(active_packet_id or ''):
            detail = details[str(packet_map.get('packet_id') or '')]
    return rows, detail, details


def _obligations_text(obligations: dict[str, Any], verification: dict[str, Any], acceptance_contract: dict[str, Any], decomposition_plan: dict[str, Any], plan: dict[str, Any]) -> str:
    docs = _text_list(obligations.get('required_documentation_updates') or [])
    tests = _text_list(obligations.get('required_test_levels') or [])
    setup = _text_list(obligations.get('required_setup_steps') or [])
    ci = _text_list(obligations.get('required_ci_updates') or [])
    impacts = _text_list(obligations.get('discovered_impacts') or [])
    missing_obligations = _text_list(verification.get('missing_obligations') or [])
    packet_count = len(_safe_list(decomposition_plan.get('packets')))
    acceptance_count = len(_safe_list(acceptance_contract.get('obligations')))
    lines = [
        "Obligations / acceptance",
        "",
        f"acceptance_obligations: {acceptance_count}",
        f"decomposition_packets: {packet_count}",
        f"plan_checks: {len(_safe_list(plan.get('verification_checks')))}",
        f"missing_obligations: {len(missing_obligations)}",
    ]
    if tests:
        lines.append("tests -> " + ", ".join(tests[:5]))
    if docs:
        lines.append("docs -> " + ", ".join(docs[:5]))
    if setup:
        lines.append("setup -> " + ", ".join(setup[:5]))
    if ci:
        lines.append("ci/build -> " + ", ".join(ci[:5]))
    if impacts:
        lines.append("impacts -> " + ", ".join(impacts[:5]))
    if missing_obligations:
        lines += ["", "still missing", *[f"- {item}" for item in missing_obligations[:8]]]
    return "\n".join(lines)


def _blockers_text(inputs: CockpitInputs, verification: dict[str, Any], acceptance: dict[str, Any]) -> str:
    lines = ["Blockers / repair / publish", ""]
    blockers: list[str] = []
    blockers.extend(_text_list(verification.get('checks_failed') or []))
    blockers.extend(_text_list(verification.get('missing_evidence') or []))
    blockers.extend(_text_list(verification.get('missing_setup_steps') or []))
    blockers.extend(_text_list(verification.get('pr_checks_failed') or []))
    blockers.extend(_text_list(acceptance.get('blockers') or []))
    if inputs.last_error:
        blockers.append(inputs.last_error)
    if inputs.stage_status.get('repair') == 'running':
        blockers.append('repair loop active')
    if inputs.stage_status.get('publish') == 'failed':
        blockers.append('publish blocked or failed')
    if not blockers:
        blockers.append('no explicit blockers recorded')
    lines += [f"current_stage: {inputs.current_stage}", f"repair_stage: {inputs.stage_status.get('repair', 'pending')}", f"publish_stage: {inputs.stage_status.get('publish', 'pending')}", "", *[f"- {item}" for item in blockers[:10]]]
    return "\n".join(lines)


def _evidence_text(inputs: CockpitInputs, verification: dict[str, Any]) -> str:
    artifact_rows = inputs.artifact_rows
    stage_counts: dict[str, int] = {}
    for row in artifact_rows:
        stage = str(row.get('kind') or 'artifact')
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
    primary = _safe_list(verification.get('primary_evidence_artifact_ids'))
    lines = [
        "Evidence / artifacts cockpit",
        "",
        f"artifact_items: {len(artifact_rows)}",
        f"primary_evidence_ids: {len(primary)}",
        f"transport_events: {inputs.transport_state.get('event_count', 0)}",
    ]
    if primary:
        lines.append("primary -> " + ", ".join(str(item) for item in primary[:5]))
    latest = artifact_rows[-1] if artifact_rows else None
    if latest:
        lines.append(f"latest_artifact: {latest.get('kind', 'artifact')}")
    return "\n".join(lines)


def _backend_activity_text(inputs: CockpitInputs) -> str:
    transport = inputs.transport_state
    lines = [
        "Runtime backend activity",
        "",
        f"direct_llm_active: {_current_backend(inputs.current_stage) == 'Direct LLM'}",
        f"openhands_active: {_current_backend(inputs.current_stage) == 'OpenHands'}",
        f"transport_mode: {transport.get('mode', 'idle')}",
        f"last_status: {transport.get('last_status', '') or 'n/a'}",
        f"followups: {transport.get('followups', 0)}",
        f"ws_url: {transport.get('websocket_url', '') or 'n/a'}",
        f"last_transport_note: {str(transport.get('last_message', '') or '')[:120] or 'n/a'}",
    ]
    return "\n".join(lines)


def _stage_rows(inputs: CockpitInputs) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    for stage in PIPELINE_NODE_ORDER:
        rows.append((stage, inputs.stage_status.get(stage, 'pending'), inputs.stage_started_at.get(stage, ''), inputs.stage_message.get(stage, '')))
    return rows


def _reentry_summary(events: list[Any]) -> str:
    start_sequence = [event.stage for event in events if getattr(event, 'kind', '') == 'stage_started' and getattr(event, 'stage', '') != 'transport']
    counts: dict[str, int] = {}
    repeated: list[str] = []
    for stage in start_sequence:
        counts[stage] = counts.get(stage, 0) + 1
        if counts[stage] == 2:
            repeated.append(stage)
    if not repeated:
        return ""
    tail = " → ".join(start_sequence[-8:])
    return f"re-entered: {', '.join(repeated)}\nrecent path: {tail}"


def _current_backend(stage: str) -> str:
    if stage in {"research", "observe", "workspace_prepare", "execute", "repair", "qa_execute", "publish", "post_publish_verify"}:
        return "OpenHands"
    return "Direct LLM"

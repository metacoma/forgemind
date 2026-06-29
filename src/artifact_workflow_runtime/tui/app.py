from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Button, Checkbox, DataTable, Footer, Header, Input, Label, RichLog, Static, TabbedContent, TabPane, TextArea
from rich.text import Text

from artifact_workflow_runtime.models import FinalReport, Task
from artifact_workflow_runtime.runtime_events import RuntimeEvent
from artifact_workflow_runtime.runtime_factory import build_controller


DEFAULT_TASK = (
    "Работай с репозиторием metacoma/freeplane_plugin_grpc.\n"
    "Сначала собери факты через observation, затем спланируй и только потом меняй мир.\n"
    "Всегда оставляй evidence, changed files, commands и verification summary."
)
STAGES = ["intake", "classify", "observe", "build_context", "plan", "policy", "approval", "execute", "verify", "finalize"]


class RuntimeEventMessage(Message):
    def __init__(self, event: RuntimeEvent) -> None:
        self.event = event
        super().__init__()


class RunFinishedMessage(Message):
    def __init__(self, report: FinalReport) -> None:
        self.report = report
        super().__init__()


class RunFailedMessage(Message):
    def __init__(self, error_text: str) -> None:
        self.error_text = error_text
        super().__init__()


class ForgeMindTUI(App[None]):
    CSS = """
    Screen {
        layout: vertical;
    }
    #status-bar {
        height: 3;
        border: round $accent;
        padding: 0 1;
        margin: 0 1 1 1;
    }
    #workflow-tabs {
        height: 1fr;
    }
    .section-title {
        text-style: bold;
        color: $accent;
        margin: 1 0 0 0;
    }
    #task-pane {
        padding: 0 1;
    }
    #task-editor {
        height: 14;
        border: round $panel;
    }
    #task-actions {
        height: auto;
        margin: 1 0;
    }
    #config-columns {
        height: auto;
        margin: 1 0 0 0;
    }
    .config-card {
        width: 1fr;
        border: round $panel;
        padding: 1;
        margin: 0 1 0 0;
    }
    .config-card:last-child {
        margin: 0;
    }
    Input, Checkbox, Button {
        margin: 0 0 1 0;
    }
    #overview-pane, #events-pane, #transport-pane, #artifacts-pane, #report-pane {
        padding: 0 1;
    }
    #overview-top {
        height: 10;
        margin: 0 0 1 0;
    }
    .overview-card {
        width: 1fr;
        border: round $panel;
        padding: 1;
        margin: 0 1 0 0;
    }
    .overview-card:last-child {
        margin: 0;
    }
    #stage-table, #artifacts-table, #conversation-table {
        height: 1fr;
    }
    #event-log, #transport-log {
        height: 1fr;
        border: round $panel;
    }
    #transport-top {
        height: 12;
        margin: 0 0 1 0;
    }
    #transport-summary, #transport-last-event {
        border: round $panel;
        padding: 1;
        width: 1fr;
        margin: 0 1 0 0;
    }
    #transport-last-event {
        margin: 0;
    }
    #artifact-split {
        height: 1fr;
    }
    #evidence-view, #report-view {
        height: 1fr;
        border: round $panel;
    }
    """

    BINDINGS = [
        ("ctrl+r", "run_workflow", "Run"),
        ("ctrl+l", "clear_log", "Clear logs"),
        ("ctrl+g", "show_task", "Task"),
        ("ctrl+c", "quit", "Quit"),
    ]

    running = reactive(False)
    current_stage = reactive("idle")
    current_status = reactive("idle")

    def __init__(self, *, initial_task: str | None = None, initial_config: dict[str, Any] | None = None) -> None:
        super().__init__()
        self.initial_task = initial_task or DEFAULT_TASK
        self.initial_config = initial_config or {}
        self.stage_status: dict[str, str] = {stage: "pending" for stage in STAGES}
        self.stage_message: dict[str, str] = {stage: "" for stage in STAGES}
        self.stage_started_at: dict[str, str] = {stage: "" for stage in STAGES}
        self.artifact_rows: list[dict[str, Any]] = []
        self.conversation_rows: list[dict[str, Any]] = []
        self.final_report: FinalReport | None = None
        self.last_error: str | None = None
        self.transport_state: dict[str, Any] = {
            "mode": "idle",
            "conversation_id": "",
            "sandbox_id": "",
            "websocket_url": "",
            "session_api_key": "",
            "last_status": "",
            "fallback": "",
            "event_count": 0,
            "followups": 0,
            "last_message": "",
        }

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("Run: idle | Current stage: idle | Status: idle", id="status-bar")
        with TabbedContent(id="workflow-tabs", initial="task"):
            with TabPane("Task", id="task"):
                with Vertical(id="task-pane"):
                    yield Label("Task composer", classes="section-title")
                    yield TextArea(self.initial_task, id="task-editor")
                    with Horizontal(id="task-actions"):
                        yield Button("Run workflow", id="run", variant="primary")
                        yield Button("Reset view", id="reset")
                        yield Button("Open cockpit", id="open-cockpit")
                    with Horizontal(id="config-columns"):
                        with Vertical(classes="config-card"):
                            yield Label("Direct LLM", classes="section-title")
                            yield Input(value=str(self.initial_config.get("direct_llm_endpoint", "http://127.0.0.1:4000/v1")), id="direct-llm-endpoint", placeholder="Direct LLM endpoint")
                            yield Input(value=str(self.initial_config.get("direct_llm_model", "qwen36-35b")), id="direct-llm-model", placeholder="Direct LLM model")
                            yield Input(value=str(self.initial_config.get("direct_llm_api_key", "sk-local") or ""), id="direct-llm-api-key", placeholder="Direct LLM API key", password=True)
                        with Vertical(classes="config-card"):
                            yield Label("OpenHands", classes="section-title")
                            yield Input(value=str(self.initial_config.get("openhands_endpoint", "http://127.0.0.1:3000")), id="openhands-endpoint", placeholder="OpenHands endpoint")
                            yield Input(value=str(self.initial_config.get("openhands_model", "qwen36-35b")), id="openhands-model", placeholder="OpenHands model")
                            yield Input(value=str(self.initial_config.get("openhands_api_key", "") or ""), id="openhands-api-key", placeholder="OpenHands API key", password=True)
                        with Vertical(classes="config-card"):
                            yield Label("Runtime", classes="section-title")
                            yield Input(value=str(self.initial_config.get("artifact_dir", "run-artifacts")), id="artifact-dir", placeholder="Artifact directory")
                            yield Input(value=str(self.initial_config.get("sandbox_id", "") or ""), id="sandbox-id", placeholder="Pinned sandbox id")
                            yield Input(value=str(self.initial_config.get("conversation_id", "") or ""), id="conversation-id", placeholder="Pinned conversation id")
                            yield Checkbox("Reuse sandbox", value=bool(self.initial_config.get("reuse", True)), id="reuse")
                            yield Checkbox("Auto approve mutations", value=bool(self.initial_config.get("auto_approve", True)), id="auto-approve")
            with TabPane("Overview", id="overview"):
                with Vertical(id="overview-pane"):
                    with Horizontal(id="overview-top"):
                        yield Static(id="run-summary", classes="overview-card")
                        yield Static(id="transport-compact", classes="overview-card")
                        yield Static(id="final-compact", classes="overview-card")
                    yield Label("Pipeline stages", classes="section-title")
                    yield DataTable(id="stage-table")
            with TabPane("Events", id="events"):
                with Vertical(id="events-pane"):
                    yield Label("Workflow events", classes="section-title")
                    yield RichLog(id="event-log", highlight=True, markup=False, wrap=True)
            with TabPane("Transport", id="transport"):
                with Vertical(id="transport-pane"):
                    with Horizontal(id="transport-top"):
                        yield Static(id="transport-summary")
                        yield Static(id="transport-last-event")
                    yield Label("Conversations / sandboxes", classes="section-title")
                    yield DataTable(id="conversation-table")
                    yield Label("WebSocket / transport events", classes="section-title")
                    yield RichLog(id="transport-log", highlight=True, markup=False, wrap=True)
            with TabPane("Artifacts", id="artifacts"):
                with Horizontal(id="artifact-split"):
                    yield DataTable(id="artifacts-table")
                    yield TextArea("", id="evidence-view")
            with TabPane("Report", id="report"):
                with Vertical(id="report-pane"):
                    yield Label("Final report", classes="section-title")
                    yield TextArea("", id="report-view")
        yield Footer()

    def on_mount(self) -> None:
        stage_table = self.query_one("#stage-table", DataTable)
        stage_table.add_columns("Stage", "Status", "Last update", "Message")
        self._rebuild_stage_table()

        artifact_table = self.query_one("#artifacts-table", DataTable)
        artifact_table.add_columns("ID", "Kind", "Path", "Preview")

        conversation_table = self.query_one("#conversation-table", DataTable)
        conversation_table.add_columns("When", "Mode", "Conversation", "Sandbox", "WebSocket", "Status")

        self.query_one("#evidence-view", TextArea).read_only = True
        self.query_one("#report-view", TextArea).read_only = True
        self._refresh_status_bar()
        self._refresh_summary_cards()
        self._refresh_transport_panels()
        self.query_one("#task-editor", TextArea).focus()

    def action_clear_log(self) -> None:
        self.query_one("#event-log", RichLog).clear()
        self.query_one("#transport-log", RichLog).clear()

    def action_run_workflow(self) -> None:
        if not self.running:
            self._start_run()

    def action_show_task(self) -> None:
        self.query_one("#workflow-tabs", TabbedContent).active = "task"
        self.query_one("#task-editor", TextArea).focus()

    @on(Button.Pressed, "#run")
    def _on_run(self) -> None:
        if not self.running:
            self._start_run()

    @on(Button.Pressed, "#open-cockpit")
    def _on_open_cockpit(self) -> None:
        self.query_one("#workflow-tabs", TabbedContent).active = "overview"

    @on(Button.Pressed, "#reset")
    def _on_reset(self) -> None:
        self.stage_status = {stage: "pending" for stage in STAGES}
        self.stage_message = {stage: "" for stage in STAGES}
        self.stage_started_at = {stage: "" for stage in STAGES}
        self.current_stage = "idle"
        self.current_status = "idle"
        self.final_report = None
        self.last_error = None
        self.artifact_rows.clear()
        self.conversation_rows.clear()
        self.transport_state.update({
            "mode": "idle",
            "conversation_id": "",
            "sandbox_id": "",
            "websocket_url": "",
            "session_api_key": "",
            "last_status": "",
            "fallback": "",
            "event_count": 0,
            "followups": 0,
            "last_message": "",
        })
        self.query_one("#artifacts-table", DataTable).clear(columns=False)
        self.query_one("#conversation-table", DataTable).clear(columns=False)
        self.query_one("#event-log", RichLog).clear()
        self.query_one("#transport-log", RichLog).clear()
        self.query_one("#evidence-view", TextArea).text = ""
        self.query_one("#report-view", TextArea).text = ""
        self._refresh_stage_table()
        self._refresh_status_bar()
        self._refresh_summary_cards()
        self._refresh_transport_panels()
        self.query_one("#workflow-tabs", TabbedContent).active = "task"
        self.query_one("#task-editor", TextArea).focus()

    @on(DataTable.RowSelected, "#artifacts-table")
    def _on_artifact_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key is None:
            return
        row_index = int(str(event.row_key))
        if 0 <= row_index < len(self.artifact_rows):
            row = self.artifact_rows[row_index]
            path = Path(row["path"])
            if path.exists():
                self.query_one("#evidence-view", TextArea).text = path.read_text(encoding="utf-8", errors="replace")

    @on(RuntimeEventMessage)
    def _on_runtime_event(self, message: RuntimeEventMessage) -> None:
        event = message.event
        if event.stage == "transport":
            self._append_transport_event(event)
            self._refresh_transport_panels()
            self._refresh_summary_cards()
            return

        if event.kind == "stage_started":
            self.stage_status[event.stage] = "running"
            self.current_stage = event.stage
            self.current_status = event.message
        elif event.kind == "stage_completed":
            self.stage_status[event.stage] = "done"
            self.current_stage = event.stage
            self.current_status = event.message
        elif event.kind == "stage_failed":
            self.stage_status[event.stage] = "failed"
            self.current_stage = event.stage
            self.current_status = event.message
        self.stage_message[event.stage] = event.message
        self.stage_started_at[event.stage] = event.timestamp
        self._append_log(event)
        self._ingest_artifact_payload(event.payload)
        self._refresh_stage_table()
        self._refresh_status_bar()
        self._refresh_summary_cards()

    @on(RunFinishedMessage)
    def _on_finished(self, message: RunFinishedMessage) -> None:
        self.running = False
        self.final_report = message.report
        self.current_status = f"completed: {message.report.status}"
        self.current_stage = "finalize"
        self.query_one("#report-view", TextArea).text = json.dumps(message.report.model_dump(mode="json"), ensure_ascii=False, indent=2)
        self._refresh_status_bar()
        self._refresh_summary_cards()
        self._sync_artifacts_from_report(message.report)
        self.query_one("#workflow-tabs", TabbedContent).active = "overview"

    @on(RunFailedMessage)
    def _on_failed(self, message: RunFailedMessage) -> None:
        self.running = False
        self.last_error = message.error_text
        self.current_status = "failed"
        self.query_one("#event-log", RichLog).write(f"Run failed: {message.error_text}")
        self._refresh_status_bar()
        self._refresh_summary_cards()
        self.query_one("#workflow-tabs", TabbedContent).active = "events"

    def _append_log(self, event: RuntimeEvent) -> None:
        payload = json.dumps(event.payload, ensure_ascii=False, sort_keys=True) if event.payload else ""
        line = f"[{event.timestamp}] {event.stage:<13} {event.kind:<16} {event.message}"
        if payload:
            line += f" | {payload}"
        self.query_one("#event-log", RichLog).write(line)

    def _append_transport_event(self, event: RuntimeEvent) -> None:
        payload = event.payload or {}
        self.transport_state["event_count"] = int(self.transport_state.get("event_count", 0)) + 1
        for key in ("conversation_id", "sandbox_id", "websocket_url", "session_api_key", "last_status", "fallback", "mode"):
            if payload.get(key):
                self.transport_state[key] = payload[key]
        if payload.get("followup"):
            self.transport_state["followups"] = int(self.transport_state.get("followups", 0)) + 1
        if event.message:
            self.transport_state["last_message"] = event.message
        if payload.get("ws_url"):
            self.transport_state["websocket_url"] = payload["ws_url"]
        if payload.get("execution_status"):
            self.transport_state["last_status"] = payload["execution_status"]
        line = f"[{event.timestamp}] {event.kind:<22} {event.message}"
        compact = json.dumps(payload, ensure_ascii=False, sort_keys=True) if payload else ""
        if compact:
            line += f" | {compact}"
        self.query_one("#transport-log", RichLog).write(line)
        if event.kind in {"conversation_started", "conversation_followup", "websocket_connected"}:
            self._add_conversation_row(
                when=event.timestamp,
                mode=payload.get("mode") or ("followup" if event.kind == "conversation_followup" else "new"),
                conversation_id=payload.get("conversation_id", ""),
                sandbox_id=payload.get("sandbox_id", ""),
                websocket_url=payload.get("websocket_url") or payload.get("ws_url", ""),
                status=payload.get("last_status") or payload.get("status", event.kind),
            )

    def _ingest_artifact_payload(self, payload: dict[str, Any]) -> None:
        ids = payload.get("artifact_ids") or []
        single = payload.get("artifact_id")
        if single:
            ids = [*ids, single]
        if not ids:
            return
        artifact_dir = Path(self.query_one("#artifact-dir", Input).value or "run-artifacts")
        candidates = list(artifact_dir.glob("**/*"))
        for artifact_id in ids:
            if any(row["id"] == artifact_id for row in self.artifact_rows):
                continue
            matched = None
            for path in candidates:
                if artifact_id in str(path):
                    matched = path
                    break
            preview = ""
            path_text = ""
            kind = str(payload.get("artifact_kind") or "artifact")
            if matched and matched.exists():
                path_text = str(matched)
                preview = matched.read_text(encoding="utf-8", errors="replace")[:160]
            self._add_artifact_row(artifact_id, kind, path_text, preview)

    def _sync_artifacts_from_report(self, report: FinalReport) -> None:
        artifact_dir = Path(self.query_one("#artifact-dir", Input).value or "run-artifacts")
        if not artifact_dir.exists():
            return
        for path in sorted(artifact_dir.glob("*")):
            if not path.is_file():
                continue
            preview = path.read_text(encoding="utf-8", errors="replace")[:160]
            self._add_artifact_row(path.stem, path.stem.split("_")[0], str(path), preview)

    def _add_artifact_row(self, artifact_id: str, kind: str, path: str, preview: str) -> None:
        if any(row["id"] == artifact_id and row["path"] == path for row in self.artifact_rows):
            return
        row_index = len(self.artifact_rows)
        self.artifact_rows.append({"id": artifact_id, "kind": kind, "path": path, "preview": preview})
        self.query_one("#artifacts-table", DataTable).add_row(artifact_id, kind, path, preview.replace("\n", " "), key=str(row_index))

    def _add_conversation_row(self, *, when: str, mode: str, conversation_id: str, sandbox_id: str, websocket_url: str, status: str) -> None:
        if conversation_id and any(row["conversation_id"] == conversation_id and row["status"] == status for row in self.conversation_rows):
            return
        row_index = len(self.conversation_rows)
        self.conversation_rows.append(
            {
                "when": when,
                "mode": mode,
                "conversation_id": conversation_id,
                "sandbox_id": sandbox_id,
                "websocket_url": websocket_url,
                "status": status,
            }
        )
        self.query_one("#conversation-table", DataTable).add_row(
            when,
            mode,
            conversation_id,
            sandbox_id,
            websocket_url,
            status,
            key=str(row_index),
        )

    def _rebuild_stage_table(self) -> None:
        table = self.query_one("#stage-table", DataTable)
        table.clear(columns=False)
        for stage in STAGES:
            table.add_row(
                stage,
                self.stage_status.get(stage, "pending"),
                self.stage_started_at.get(stage, ""),
                self.stage_message.get(stage, ""),
                key=stage,
            )

    def _refresh_stage_table(self) -> None:
        table = self.query_one("#stage-table", DataTable)
        needs_rebuild = False
        try:
            for stage in STAGES:
                table.update_cell(stage, "Status", self.stage_status.get(stage, "pending"))
                table.update_cell(stage, "Last update", self.stage_started_at.get(stage, ""))
                table.update_cell(stage, "Message", self.stage_message.get(stage, ""))
        except Exception:
            needs_rebuild = True
        if needs_rebuild:
            self._rebuild_stage_table()

    def _set_static_text(self, selector: str, content: str) -> None:
        self.query_one(selector, Static).update(Text(str(content)))

    def _refresh_status_bar(self) -> None:
        run_state = "running" if self.running else "idle"
        self.query_one("#status-bar", Static).update(
            f"Run: {run_state} | Current stage: {self.current_stage} | Status: {self.current_status}"
        )

    def _refresh_summary_cards(self) -> None:
        run_lines = ["Workflow", "", f"current_stage: {self.current_stage}", f"status: {self.current_status}", f"artifacts_seen: {len(self.artifact_rows)}"]
        if self.last_error:
            run_lines.append(f"last_error: {self.last_error}")
        self._set_static_text("#run-summary", "\n".join(run_lines))

        transport_lines = [
            "Transport",
            "",
            f"mode: {self.transport_state.get('mode', '')}",
            f"conversation: {self.transport_state.get('conversation_id', '')}",
            f"sandbox: {self.transport_state.get('sandbox_id', '')}",
            f"followups: {self.transport_state.get('followups', 0)}",
            f"events: {self.transport_state.get('event_count', 0)}",
        ]
        self._set_static_text("#transport-compact", "\n".join(transport_lines))

        final_lines = ["Final / verification", ""]
        if self.final_report is not None:
            final_lines.append(f"final_status: {self.final_report.status}")
            if self.final_report.plan is not None:
                final_lines.append(f"intent: {self.final_report.plan.task_intent}")
                final_lines.append(f"deliverable: {self.final_report.plan.deliverable_kind}")
            if self.final_report.verification is not None:
                verdict = "PASS" if self.final_report.verification.passed else "FAIL"
                final_lines.append(f"verification: {verdict}")
                final_lines.append(f"confidence: {self.final_report.verification.confidence}")
        else:
            final_lines.append("no final report yet")
        self._set_static_text("#final-compact", "\n".join(final_lines))

    def _refresh_transport_panels(self) -> None:
        lines = [
            "Transport state",
            "",
            f"mode: {self.transport_state.get('mode', '')}",
            f"conversation_id: {self.transport_state.get('conversation_id', '')}",
            f"sandbox_id: {self.transport_state.get('sandbox_id', '')}",
            f"websocket_url: {self.transport_state.get('websocket_url', '')}",
            f"last_status: {self.transport_state.get('last_status', '')}",
            f"fallback: {self.transport_state.get('fallback', '')}",
            f"session_api_key: {'present' if self.transport_state.get('session_api_key') else 'none'}",
            f"events_seen: {self.transport_state.get('event_count', 0)}",
            f"followups: {self.transport_state.get('followups', 0)}",
        ]
        self._set_static_text("#transport-summary", "\n".join(lines))
        self._set_static_text(
            "#transport-last-event",
            "Last transport note\n\n" + str(self.transport_state.get("last_message", "")),
        )

    def _build_config(self) -> dict[str, Any]:
        return {
            "artifact_dir": self.query_one("#artifact-dir", Input).value or "run-artifacts",
            "direct_llm_endpoint": self.query_one("#direct-llm-endpoint", Input).value,
            "direct_llm_model": self.query_one("#direct-llm-model", Input).value,
            "direct_llm_api_key": self.query_one("#direct-llm-api-key", Input).value or None,
            "openhands_endpoint": self.query_one("#openhands-endpoint", Input).value,
            "openhands_model": self.query_one("#openhands-model", Input).value,
            "openhands_api_key": self.query_one("#openhands-api-key", Input).value or None,
            "reuse": self.query_one("#reuse", Checkbox).value,
            "sandbox_id": self.query_one("#sandbox-id", Input).value or None,
            "conversation_id": self.query_one("#conversation-id", Input).value or None,
            "auto_approve": self.query_one("#auto-approve", Checkbox).value,
        }

    def _start_run(self) -> None:
        self.running = True
        self.current_stage = "boot"
        self.current_status = "initializing workflow"
        self._refresh_status_bar()
        self.query_one("#event-log", RichLog).write("Starting workflow run...")
        self.query_one("#workflow-tabs", TabbedContent).active = "overview"
        self.run_worker(self._run_workflow(), exclusive=True, thread=False)

    async def _run_workflow(self) -> None:
        def event_sink(event: RuntimeEvent) -> None:
            self.post_message(RuntimeEventMessage(event))

        config = self._build_config()
        controller = build_controller(event_sink=event_sink, **config)
        task = Task(title=None, description=self.query_one("#task-editor", TextArea).text)
        try:
            report = await controller.run(task)
        except Exception as exc:  # pragma: no cover - interactive path
            self.post_message(RunFailedMessage(str(exc)))
            return
        self.post_message(RunFinishedMessage(report))



def run_tui(*, initial_task: str | None = None, initial_config: dict[str, Any] | None = None) -> None:
    ForgeMindTUI(initial_task=initial_task, initial_config=initial_config).run()

from __future__ import annotations

import json
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Button, Checkbox, DataTable, Footer, Header, Input, Label, RichLog, Static, TabbedContent, TabPane, TextArea
from rich.text import Text

from artifact_workflow_runtime.models import FinalReport, Task
from artifact_workflow_runtime.openhands_adapter.client import OpenHandsClient, extract_message_text, run_followup_message_and_collect
from artifact_workflow_runtime.runtime_events import RuntimeEvent
from artifact_workflow_runtime.runtime_factory import build_controller


DEFAULT_TASK = (
    "Работай с репозиторием metacoma/freeplane_plugin_grpc.\n"
    "Сначала собери факты через observation, затем спланируй и только потом меняй мир.\n"
    "Всегда оставляй evidence, changed files, commands и verification summary."
)
STAGES = ["intake", "classify", "route", "research", "observe", "build_context", "plan", "policy", "approval", "execute", "publish", "verify", "finalize"]


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


class InternalErrorMessage(Message):
    def __init__(self, context: str, error_text: str) -> None:
        self.context = context
        self.error_text = error_text
        super().__init__()


class ConversationListLoadedMessage(Message):
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        super().__init__()


class ConversationLoadedMessage(Message):
    def __init__(self, conversation_id: str, details: str, transcript: str) -> None:
        self.conversation_id = conversation_id
        self.details = details
        self.transcript = transcript
        super().__init__()


class ConversationSentMessage(Message):
    def __init__(self, conversation_id: str, transcript: str, response_text: str) -> None:
        self.conversation_id = conversation_id
        self.transcript = transcript
        self.response_text = response_text
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
    #overview-pane, #events-pane, #transport-pane, #artifacts-pane, #conversations-pane, #report-pane, #errors-pane {
        padding: 0 1;
    }
    #overview-top {
        height: 10;
        margin: 0 0 1 0;
    }
    #workflow-path-view {
        height: 12;
        border: round $panel;
        padding: 1;
        margin: 0 0 1 0;
    }
    #overview-split {
        height: 1fr;
    }
    #stage-detail-view {
        height: 1fr;
        border: round $panel;
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
    #conversation-split {
        height: 1fr;
    }
    #conversation-detail-pane {
        width: 2fr;
    }
    #conversation-meta, #conversation-chat-view {
        border: round $panel;
        padding: 1;
        margin: 0 0 1 0;
    }
    #conversation-chat-view {
        height: 1fr;
    }
    #conversation-input {
        height: 8;
        border: round $panel;
    }
    #conversation-actions {
        height: auto;
        margin: 1 0 0 0;
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
        self.browser_conversation_rows: list[dict[str, Any]] = []
        self.selected_conversation_id: str | None = None
        self.final_report: FinalReport | None = None
        self.last_error: str | None = None
        self.error_history: list[str] = []
        self.log_file_path: Path | None = None
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
                    yield Static(id="workflow-path-view")
                    yield Label("Pipeline stages", classes="section-title")
                    with Horizontal(id="overview-split"):
                        yield DataTable(id="stage-table")
                        yield TextArea("", id="stage-detail-view")
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
            with TabPane("Conversations", id="conversations"):
                with Vertical(id="conversations-pane"):
                    with Horizontal(id="conversation-actions"):
                        yield Button("Refresh conversations", id="refresh-conversations")
                        yield Button("Load selected", id="load-conversation")
                        yield Button("Use current run conversation", id="use-current-conversation")
                        yield Button("Send to conversation", id="send-conversation-message", variant="primary")
                    with Horizontal(id="conversation-split"):
                        yield DataTable(id="conversation-browser-table")
                        with Vertical(id="conversation-detail-pane"):
                            yield Static(id="conversation-meta")
                            yield TextArea("", id="conversation-chat-view")
                            yield TextArea("", id="conversation-input")
            with TabPane("Report", id="report"):
                with Vertical(id="report-pane"):
                    yield Label("Final report", classes="section-title")
                    yield TextArea("", id="report-view")
            with TabPane("Errors", id="errors"):
                with Vertical(id="errors-pane"):
                    yield Label("Errors / tracebacks", classes="section-title")
                    yield TextArea("", id="error-view")
        yield Footer()

    def on_mount(self) -> None:
        stage_table = self.query_one("#stage-table", DataTable)
        stage_table.add_columns("Stage", "Status", "Last update", "Message")
        self._rebuild_stage_table()

        artifact_table = self.query_one("#artifacts-table", DataTable)
        artifact_table.add_columns("ID", "Kind", "Path", "Preview")

        conversation_table = self.query_one("#conversation-table", DataTable)
        conversation_table.add_columns("When", "Mode", "Conversation", "Sandbox", "WebSocket", "Status")
        conversation_browser_table = self.query_one("#conversation-browser-table", DataTable)
        conversation_browser_table.add_columns("Updated", "Title", "Conversation", "Sandbox", "Status")

        self.query_one("#evidence-view", TextArea).read_only = True
        self.query_one("#stage-detail-view", TextArea).read_only = True
        self.query_one("#conversation-chat-view", TextArea).read_only = True
        self.query_one("#report-view", TextArea).read_only = True
        self.query_one("#error-view", TextArea).read_only = True
        self._refresh_log_path()
        self._refresh_status_bar()
        self._refresh_summary_cards()
        self._refresh_transport_panels()
        self._set_static_text("#workflow-path-view", self._workflow_path_text())
        self._set_static_text("#conversation-meta", "No conversation selected")
        self._show_stage_by_name("intake")
        self.query_one("#task-editor", TextArea).focus()
        self.run_worker(self._load_conversation_browser(), exclusive=False, thread=False)

    def action_clear_log(self) -> None:
        self.query_one("#event-log", RichLog).clear()
        self.query_one("#transport-log", RichLog).clear()

    def action_run_workflow(self) -> None:
        if not self.running:
            self._start_run()

    def action_show_task(self) -> None:
        self.query_one("#workflow-tabs", TabbedContent).active = "task"
        self.query_one("#task-editor", TextArea).focus()
        self.run_worker(self._load_conversation_browser(), exclusive=False, thread=False)

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
        self.error_history.clear()
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
        self.browser_conversation_rows.clear()
        self.selected_conversation_id = None
        self.query_one("#conversation-browser-table", DataTable).clear(columns=False)
        self.query_one("#event-log", RichLog).clear()
        self.query_one("#transport-log", RichLog).clear()
        self.query_one("#evidence-view", TextArea).text = ""
        self.query_one("#conversation-meta", Static).update(Text("No conversation selected"))
        self.query_one("#conversation-chat-view", TextArea).text = ""
        self.query_one("#conversation-input", TextArea).text = ""
        self.query_one("#report-view", TextArea).text = ""
        self.query_one("#error-view", TextArea).text = ""
        self.query_one("#run", Button).disabled = False
        self.query_one("#open-cockpit", Button).disabled = False
        self._refresh_log_path()
        self._refresh_stage_table()
        self._refresh_status_bar()
        self._refresh_summary_cards()
        self._refresh_transport_panels()
        self.query_one("#workflow-tabs", TabbedContent).active = "task"
        self.query_one("#task-editor", TextArea).focus()
        self.run_worker(self._load_conversation_browser(), exclusive=False, thread=False)

    def _client_for_conversations(self) -> OpenHandsClient:
        return OpenHandsClient(
            self.query_one("#openhands-endpoint", Input).value,
            api_key=self.query_one("#openhands-api-key", Input).value or None,
        )

    def _format_conversation_messages(self, messages: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for item in messages:
            role = str(item.get("role") or item.get("source") or item.get("type") or "message")
            text = extract_message_text(item).strip()
            if not text:
                llm_message = item.get("llm_message")
                if isinstance(llm_message, dict):
                    role = str(llm_message.get("role") or role)
                text = json.dumps(item, ensure_ascii=False, indent=2)
            lines.append(f"[{role}]\n{text}")
        return "\n\n".join(lines) if lines else "No messages available for this conversation."

    def _build_conversation_details(self, row: dict[str, Any]) -> str:
        details = {
            "conversation_id": row.get("conversation_id", ""),
            "sandbox_id": row.get("sandbox_id", ""),
            "status": row.get("status", ""),
            "title": row.get("title", ""),
            "updated_at": row.get("updated_at", ""),
            "mode": row.get("mode", ""),
            "websocket_url": row.get("websocket_url", ""),
        }
        return json.dumps(details, ensure_ascii=False, indent=2)

    def _select_browser_conversation(self, row_index: int) -> None:
        if 0 <= row_index < len(self.browser_conversation_rows):
            row = self.browser_conversation_rows[row_index]
            self.selected_conversation_id = str(row.get("conversation_id") or "") or None
            self._set_static_text("#conversation-meta", self._build_conversation_details(row))
            conversation_id = self.selected_conversation_id
            if conversation_id:
                self.run_worker(self._load_conversation_detail(conversation_id), exclusive=False, thread=False)

    def _show_artifact_by_index(self, row_index: int) -> None:
        if 0 <= row_index < len(self.artifact_rows):
            row = self.artifact_rows[row_index]
            raw_path = str(row.get("path") or "").strip()
            preview = str(row.get("preview") or "")
            header = (
                f"Artifact ID: {row.get('id', '')}\n"
                f"Kind: {row.get('kind', '')}\n"
                f"Path: {raw_path or '<none>'}\n\n"
            )
            if raw_path:
                path = Path(raw_path)
                if path.exists() and path.is_file():
                    self.query_one("#evidence-view", TextArea).text = header + path.read_text(
                        encoding="utf-8", errors="replace"
                    )
                    return
                if path.exists() and path.is_dir():
                    self.query_one("#evidence-view", TextArea).text = (
                        header + "Artifact path points to a directory, not a file.\n\n" + preview
                    )
                    return
                self.query_one("#evidence-view", TextArea).text = header + f"Artifact file not found:\n{path}"
                return
            self.query_one("#evidence-view", TextArea).text = (
                header + "No artifact file path was resolved for this item.\n\n" + preview
            )

    def _stage_detail_text(self, stage: str) -> str:
        parts = [
            f"Stage: {stage}",
            f"Status: {self.stage_status.get(stage, 'pending')}",
            f"Last update: {self.stage_started_at.get(stage, '')}",
            "",
            "Message:",
            self.stage_message.get(stage, ''),
        ]
        if self.final_report is not None:
            if stage == "route" and self.final_report.route is not None:
                parts += [
                    "",
                    "Route decision:",
                    json.dumps(self.final_report.route.model_dump(mode="json"), ensure_ascii=False, indent=2),
                ]
            elif stage == "research" and self.final_report.research is not None:
                parts += [
                    "",
                    "Research result:",
                    json.dumps(self.final_report.research.model_dump(mode="json"), ensure_ascii=False, indent=2),
                ]
            elif stage == "plan" and self.final_report.plan is not None:
                parts += [
                    "",
                    "Execution plan:",
                    json.dumps(self.final_report.plan.model_dump(mode="json"), ensure_ascii=False, indent=2),
                ]
            elif stage == "verify" and self.final_report.verification is not None:
                parts += [
                    "",
                    "Verification:",
                    json.dumps(self.final_report.verification.model_dump(mode="json"), ensure_ascii=False, indent=2),
                ]
        return "\n".join(str(part) for part in parts if part is not None)

    def _show_stage_by_name(self, stage: str) -> None:
        if stage in STAGES:
            self.query_one("#stage-detail-view", TextArea).text = self._stage_detail_text(stage)

    @on(DataTable.RowSelected, "#artifacts-table")
    def _on_artifact_selected(self, event: DataTable.RowSelected) -> None:
        try:
            row_index = int(str(getattr(event.row_key, "value", event.row_key)))
        except Exception:
            return
        self._show_artifact_by_index(row_index)

    @on(DataTable.CellSelected, "#artifacts-table")
    def _on_artifact_cell_selected(self, event: DataTable.CellSelected) -> None:
        self._show_artifact_by_index(int(event.coordinate.row))

    @on(DataTable.RowSelected, "#stage-table")
    def _on_stage_selected(self, event: DataTable.RowSelected) -> None:
        stage = str(getattr(event.row_key, "value", event.row_key))
        self._show_stage_by_name(stage)

    @on(DataTable.CellSelected, "#stage-table")
    def _on_stage_cell_selected(self, event: DataTable.CellSelected) -> None:
        try:
            row = self.query_one("#stage-table", DataTable).get_row_at(event.coordinate.row)
            stage = str(row[0])
        except Exception:
            stage = STAGES[int(event.coordinate.row)] if int(event.coordinate.row) < len(STAGES) else ""
        self._show_stage_by_name(stage)

    @on(DataTable.RowSelected, "#conversation-browser-table")
    def _on_browser_conversation_selected(self, event: DataTable.RowSelected) -> None:
        try:
            row_index = int(str(getattr(event.row_key, "value", event.row_key)))
        except Exception:
            return
        self._select_browser_conversation(row_index)

    @on(DataTable.CellSelected, "#conversation-browser-table")
    def _on_browser_conversation_cell_selected(self, event: DataTable.CellSelected) -> None:
        self._select_browser_conversation(int(event.coordinate.row))

    @on(Button.Pressed, "#refresh-conversations")
    def _on_refresh_conversations(self) -> None:
        self.run_worker(self._load_conversation_browser(), exclusive=False, thread=False)

    @on(Button.Pressed, "#load-conversation")
    def _on_load_conversation(self) -> None:
        if self.selected_conversation_id:
            self.run_worker(self._load_conversation_detail(self.selected_conversation_id), exclusive=False, thread=False)

    @on(Button.Pressed, "#use-current-conversation")
    def _on_use_current_conversation(self) -> None:
        current = str(self.transport_state.get("conversation_id") or "")
        if not current:
            return
        self.selected_conversation_id = current
        self.run_worker(self._load_conversation_detail(current), exclusive=False, thread=False)
        self.query_one("#workflow-tabs", TabbedContent).active = "conversations"

    @on(Button.Pressed, "#send-conversation-message")
    def _on_send_conversation_message(self) -> None:
        conversation_id = self.selected_conversation_id
        text_value = self.query_one("#conversation-input", TextArea).text.strip()
        if not conversation_id or not text_value:
            return
        self.run_worker(self._send_conversation_message(conversation_id, text_value), exclusive=False, thread=False)

    @on(ConversationListLoadedMessage)
    def _on_conversation_list_loaded(self, message: ConversationListLoadedMessage) -> None:
        table = self.query_one("#conversation-browser-table", DataTable)
        table.clear(columns=False)
        self.browser_conversation_rows = list(message.rows)
        for idx, row in enumerate(self.browser_conversation_rows):
            table.add_row(
                row.get("updated_at", ""),
                row.get("title", ""),
                row.get("conversation_id", ""),
                row.get("sandbox_id", ""),
                row.get("status", ""),
                key=str(idx),
            )

    @on(ConversationLoadedMessage)
    def _on_conversation_loaded(self, message: ConversationLoadedMessage) -> None:
        self.selected_conversation_id = message.conversation_id
        self._set_static_text("#conversation-meta", message.details)
        self.query_one("#conversation-chat-view", TextArea).text = message.transcript

    @on(ConversationSentMessage)
    def _on_conversation_sent(self, message: ConversationSentMessage) -> None:
        self.selected_conversation_id = message.conversation_id
        self.query_one("#conversation-input", TextArea).text = ""
        self.query_one("#conversation-chat-view", TextArea).text = message.transcript
        self.query_one("#workflow-tabs", TabbedContent).active = "conversations"
        self.query_one("#transport-log", RichLog).write(f"[manual_chat] sent to {message.conversation_id}: {message.response_text[:160]}")

    @on(RuntimeEventMessage)
    def _on_runtime_event(self, message: RuntimeEventMessage) -> None:
        try:
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
            if self.running and self.query_one("#workflow-tabs", TabbedContent).active == "task":
                self.query_one("#workflow-tabs", TabbedContent).active = "overview"
            self._show_stage_by_name(event.stage)
            self._refresh_status_bar()
            self._refresh_summary_cards()
        except Exception as exc:  # pragma: no cover - interactive path
            self.post_message(InternalErrorMessage("_on_runtime_event", self._format_exception(exc)))

    @on(RunFinishedMessage)
    def _on_finished(self, message: RunFinishedMessage) -> None:
        self.running = False
        self.query_one("#run", Button).disabled = False
        self.query_one("#open-cockpit", Button).disabled = False
        self.final_report = message.report
        self.current_status = f"completed: {message.report.status}"
        self.current_stage = "finalize"
        self.query_one("#report-view", TextArea).text = json.dumps(message.report.model_dump(mode="json"), ensure_ascii=False, indent=2)
        self._refresh_status_bar()
        self._refresh_summary_cards()
        self._sync_artifacts_from_report(message.report)
        self.query_one("#workflow-tabs", TabbedContent).active = "overview"
        self._show_stage_by_name("finalize")

    @on(RunFailedMessage)
    def _on_failed(self, message: RunFailedMessage) -> None:
        self.running = False
        self.query_one("#run", Button).disabled = False
        self.query_one("#open-cockpit", Button).disabled = False
        self.last_error = message.error_text
        self.current_status = "failed"
        self.query_one("#event-log", RichLog).write(f"Run failed: {message.error_text}")
        self._record_error("workflow_run", message.error_text)
        self._refresh_status_bar()
        self._refresh_summary_cards()
        self.query_one("#workflow-tabs", TabbedContent).active = "errors"

    @on(InternalErrorMessage)
    def _on_internal_error(self, message: InternalErrorMessage) -> None:
        self._record_error(message.context, message.error_text)
        self.query_one("#workflow-tabs", TabbedContent).active = "errors"

    async def _load_conversation_browser(self) -> None:
        try:
            client = self._client_for_conversations()
            data = await client.search_app_conversations(limit=100, include_sub_conversations=True)
            items = data.get("items") if isinstance(data, dict) else data
            rows: list[dict[str, Any]] = []
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    rows.append({
                        "conversation_id": str(item.get("id") or item.get("conversation_id") or ""),
                        "sandbox_id": str(item.get("sandbox_id") or ""),
                        "status": str(item.get("status") or ""),
                        "title": str(item.get("title") or item.get("name") or ""),
                        "updated_at": str(item.get("updated_at") or item.get("created_at") or ""),
                        "mode": str(item.get("mode") or "search"),
                        "websocket_url": "",
                    })
            rows = [row for row in rows if row.get("conversation_id")]
            rows.sort(key=lambda row: row.get("updated_at", ""), reverse=True)
            self.post_message(ConversationListLoadedMessage(rows))
        except Exception as exc:  # pragma: no cover - interactive path
            self.post_message(InternalErrorMessage("conversation_list", self._format_exception(exc)))

    async def _load_conversation_detail(self, conversation_id: str) -> None:
        try:
            client = self._client_for_conversations()
            start = await client.get_existing_conversation_start(conversation_id)
            messages = await client.get_conversation_messages(conversation_id)
            row = next((r for r in self.browser_conversation_rows if r.get("conversation_id") == conversation_id), None)
            payload = row or {}
            payload = {**payload, "conversation_id": conversation_id, "sandbox_id": start.sandbox_id or payload.get("sandbox_id", ""), "websocket_url": start.conversation_url or start.agent_server_url or payload.get("websocket_url", "")}
            details = self._build_conversation_details(payload)
            transcript = self._format_conversation_messages(messages)
            self.post_message(ConversationLoadedMessage(conversation_id, details, transcript))
        except Exception as exc:  # pragma: no cover - interactive path
            self.post_message(InternalErrorMessage("conversation_detail", self._format_exception(exc)))

    async def _send_conversation_message(self, conversation_id: str, text_value: str) -> None:
        try:
            client = self._client_for_conversations()
            start = await client.get_existing_conversation_start(conversation_id)
            result = await run_followup_message_and_collect(
                endpoint=self.query_one("#openhands-endpoint", Input).value,
                conversation=start,
                prompt=text_value,
                api_key=self.query_one("#openhands-api-key", Input).value or None,
                event_sink=lambda event: self.post_message(RuntimeEventMessage(event)),
            )
            messages = await client.get_conversation_messages(conversation_id)
            transcript = self._format_conversation_messages(messages)
            self.post_message(ConversationSentMessage(conversation_id, transcript, result.text))
        except Exception as exc:  # pragma: no cover - interactive path
            self.post_message(InternalErrorMessage("conversation_send", self._format_exception(exc)))

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

    def _workflow_path_text(self) -> str:
        lines = ["Global path", ""]
        for stage in STAGES:
            status = self.stage_status.get(stage, "pending")
            message = self.stage_message.get(stage, "")
            marker = {
                "done": "✓",
                "running": "→",
                "failed": "✗",
                "pending": "·",
            }.get(status, "·")
            line = f"{marker} {stage}: {status}"
            if message:
                line += f" — {message}"
            lines.append(line)

        if self.final_report is not None:
            lines += ["", "Decision summary:"]
            route = getattr(self.final_report, "route", None)
            if route is not None:
                lines.append(
                    "route -> research={research}, repo_obs={repo}, world_obs={world}, can_plan={plan}".format(
                        research=getattr(route, "needs_fresh_external_research", None),
                        repo=getattr(route, "needs_repository_observation", None),
                        world=getattr(route, "needs_world_observation", None),
                        plan=getattr(route, "can_plan_immediately", None),
                    )
                )
            plan = getattr(self.final_report, "plan", None)
            if plan is not None:
                lines.append(
                    "plan -> intent={intent}, deliverable={deliverable}, commit={commit}, push={push}".format(
                        intent=getattr(plan, "task_intent", ""),
                        deliverable=getattr(plan, "deliverable_kind", ""),
                        commit=getattr(plan, "require_commit", None),
                        push=getattr(plan, "require_push", None),
                    )
                )
                required_tests = getattr(plan, "required_test_levels", None) or []
                if required_tests:
                    lines.append("required tests -> " + ", ".join(required_tests))
            verification = getattr(self.final_report, "verification", None)
            if verification is not None:
                lines.append(
                    "verify -> passed={passed}, completion={completion}".format(
                        passed=getattr(verification, "passed", None),
                        completion=getattr(verification, "completion_status", ""),
                    )
                )
                performed = getattr(verification, "performed_test_levels", None) or []
                missing = getattr(verification, "missing_test_levels", None) or []
                if performed:
                    lines.append("performed tests -> " + ", ".join(performed))
                if missing:
                    lines.append("missing tests -> " + ", ".join(missing))
                obligations = getattr(verification, "missing_obligations", None) or []
                if obligations:
                    lines.append("missing obligations -> " + "; ".join(str(x) for x in obligations))
            lines.append(f"final -> status={self.final_report.status}")
        return "\n".join(lines)

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
            if self.final_report.route is not None:
                final_lines.append(f"needs_research: {self.final_report.route.needs_fresh_external_research}")
                final_lines.append(f"needs_repo_obs: {self.final_report.route.needs_repository_observation}")
                final_lines.append(f"needs_world_obs: {self.final_report.route.needs_world_observation}")
            if self.final_report.plan is not None:
                final_lines.append(f"intent: {self.final_report.plan.task_intent}")
                final_lines.append(f"deliverable: {self.final_report.plan.deliverable_kind}")
            if self.final_report.verification is not None:
                verdict = "PASS" if self.final_report.verification.passed else "FAIL"
                final_lines.append(f"verification: {verdict}")
                final_lines.append(f"confidence: {self.final_report.verification.confidence}")
                if getattr(self.final_report.verification, "completion_status", None):
                    final_lines.append(f"completion: {self.final_report.verification.completion_status}")
                performed = getattr(self.final_report.verification, "performed_test_levels", None) or []
                missing = getattr(self.final_report.verification, "missing_test_levels", None) or []
                if performed:
                    final_lines.append(f"tests_done: {', '.join(performed)}")
                if missing:
                    final_lines.append(f"tests_missing: {', '.join(missing)}")
                missing_obligations = getattr(self.final_report.verification, "missing_obligations", None) or []
                if missing_obligations:
                    final_lines.append(f"obligations_missing: {len(missing_obligations)}")
        else:
            final_lines.append("no final report yet")
        self._set_static_text("#final-compact", "\n".join(final_lines))
        self._set_static_text("#workflow-path-view", self._workflow_path_text())

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

    def _format_exception(self, exc: BaseException) -> str:
        return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))

    def _refresh_log_path(self) -> None:
        artifact_dir = Path(self.query_one("#artifact-dir", Input).value or "run-artifacts")
        artifact_dir.mkdir(parents=True, exist_ok=True)
        self.log_file_path = artifact_dir / "tui-errors.log"

    def _record_error(self, context: str, error_text: str) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        separator = "=" * 100
        entry = f"[{timestamp}] {context}\n\n{error_text.strip()}\n\n{separator}\n"
        self.error_history.append(entry)
        first_line = error_text.splitlines()[0] if error_text.splitlines() else error_text
        self.last_error = f"{context}: {first_line}"
        self.query_one("#error-view", TextArea).text = "\n".join(self.error_history)
        self.query_one("#event-log", RichLog).write(f"ERROR[{context}] {self.last_error}")
        try:
            if self.log_file_path is None:
                self._refresh_log_path()
            assert self.log_file_path is not None
            with self.log_file_path.open("a", encoding="utf-8") as fh:
                fh.write(entry)
        except Exception:
            pass

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
        self.query_one("#run", Button).disabled = True
        self.query_one("#open-cockpit", Button).disabled = True
        self._refresh_status_bar()
        self.query_one("#event-log", RichLog).write("Starting workflow run...")
        self.query_one("#workflow-tabs", TabbedContent).active = "overview"
        self.call_after_refresh(lambda: self.query_one("#stage-table", DataTable).focus())
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
            self.post_message(RunFailedMessage(self._format_exception(exc)))
            return
        self.post_message(RunFinishedMessage(report))



def run_tui(*, initial_task: str | None = None, initial_config: dict[str, Any] | None = None) -> None:
    ForgeMindTUI(initial_task=initial_task, initial_config=initial_config).run()

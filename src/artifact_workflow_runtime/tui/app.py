from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Button, Checkbox, DataTable, Footer, Header, Input, Label, LoadingIndicator, RichLog, Static, TabbedContent, TabPane, TextArea

from artifact_workflow_runtime.models import FinalReport, Task
from artifact_workflow_runtime.runtime_events import RuntimeEvent
from artifact_workflow_runtime.runtime_factory import build_controller


DEFAULT_TASK = "Работай с репозиторием metacoma/freeplane_plugin_grpc.\nПроанализируй текущее состояние и выполни задачу по evidence-backed pipeline."
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
    #body {
        layout: horizontal;
        height: 1fr;
    }
    #sidebar {
        width: 39;
        min-width: 34;
        border: round $accent;
        padding: 1;
    }
    #main {
        width: 1fr;
        padding: 0 1;
    }
    .section-title {
        text-style: bold;
        color: $accent;
        margin: 1 0 0 0;
    }
    Input, TextArea, Checkbox, Button {
        margin: 0 0 1 0;
    }
    #task-editor {
        height: 12;
    }
    #event-log, #evidence-view, #report-view {
        height: 1fr;
    }
    #stage-list {
        height: auto;
        border: round $panel;
        padding: 0 1;
        margin: 0 0 1 0;
    }
    .stage-row {
        height: auto;
    }
    #status-bar {
        height: 3;
        border: round $panel;
        padding: 0 1;
        margin: 0 0 1 0;
    }
    #artifacts-table {
        height: 1fr;
    }
    #right-summary {
        height: 8;
        border: round $panel;
        padding: 0 1;
        margin: 0 0 1 0;
    }
    """

    BINDINGS = [("ctrl+r", "run_workflow", "Run"), ("ctrl+l", "clear_log", "Clear log"), ("ctrl+c", "quit", "Quit")]

    running = reactive(False)
    current_stage = reactive("idle")
    current_status = reactive("idle")

    def __init__(self) -> None:
        super().__init__()
        self.stage_status: dict[str, str] = {stage: "pending" for stage in STAGES}
        self.artifact_rows: list[dict[str, Any]] = []
        self.final_report: FinalReport | None = None
        self.last_error: str | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Label("Run configuration", classes="section-title")
                yield Input(value="http://127.0.0.1:4000/v1", id="direct-llm-endpoint", placeholder="Direct LLM endpoint")
                yield Input(value="qwen36-35b", id="direct-llm-model", placeholder="Direct LLM model")
                yield Input(value="sk-local", id="direct-llm-api-key", placeholder="Direct LLM API key", password=True)
                yield Input(value="http://127.0.0.1:3000", id="openhands-endpoint", placeholder="OpenHands endpoint")
                yield Input(value="qwen36-35b", id="openhands-model", placeholder="OpenHands model")
                yield Input(value="", id="openhands-api-key", placeholder="OpenHands API key", password=True)
                yield Input(value="run-artifacts", id="artifact-dir", placeholder="Artifact directory")
                yield Input(value="", id="sandbox-id", placeholder="Pinned sandbox id")
                yield Input(value="", id="conversation-id", placeholder="Pinned conversation id")
                yield Checkbox("Reuse sandbox", value=True, id="reuse")
                yield Checkbox("Auto approve mutations", value=True, id="auto-approve")
                yield Label("Task", classes="section-title")
                yield TextArea(DEFAULT_TASK, id="task-editor")
                with Horizontal():
                    yield Button("Run workflow", id="run", variant="primary")
                    yield Button("Reset view", id="reset")
            with Vertical(id="main"):
                yield Static("Status: idle", id="status-bar")
                with TabbedContent(initial="overview"):
                    with TabPane("Overview", id="overview"):
                        yield Static(id="right-summary")
                        yield Static(id="stage-list")
                    with TabPane("Events", id="events"):
                        yield RichLog(id="event-log", highlight=True, markup=False, wrap=True)
                    with TabPane("Artifacts", id="artifacts"):
                        yield DataTable(id="artifacts-table")
                    with TabPane("Evidence", id="evidence"):
                        yield TextArea("", id="evidence-view")
                    with TabPane("Report", id="report"):
                        yield TextArea("", id="report-view")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#artifacts-table", DataTable)
        table.add_columns("ID", "Kind", "Path", "Preview")
        self._refresh_stage_list()
        self._refresh_status_bar()
        self._refresh_summary()
        self.query_one("#evidence-view", TextArea).read_only = True
        self.query_one("#report-view", TextArea).read_only = True

    def action_clear_log(self) -> None:
        self.query_one("#event-log", RichLog).clear()

    def action_run_workflow(self) -> None:
        if not self.running:
            self._start_run()

    @on(Button.Pressed, "#run")
    def _on_run(self) -> None:
        if not self.running:
            self._start_run()

    @on(Button.Pressed, "#reset")
    def _on_reset(self) -> None:
        self.stage_status = {stage: "pending" for stage in STAGES}
        self.current_stage = "idle"
        self.current_status = "idle"
        self.final_report = None
        self.last_error = None
        self.artifact_rows.clear()
        self.query_one("#artifacts-table", DataTable).clear(columns=False)
        self.query_one("#event-log", RichLog).clear()
        self.query_one("#evidence-view", TextArea).text = ""
        self.query_one("#report-view", TextArea).text = ""
        self._refresh_stage_list()
        self._refresh_status_bar()
        self._refresh_summary()

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
        self._append_log(event)
        self._ingest_artifact_payload(event.payload)
        self._refresh_stage_list()
        self._refresh_status_bar()
        self._refresh_summary()

    @on(RunFinishedMessage)
    def _on_finished(self, message: RunFinishedMessage) -> None:
        self.running = False
        self.final_report = message.report
        self.current_status = f"completed: {message.report.status}"
        self.current_stage = "finalize"
        self.query_one("#report-view", TextArea).text = json.dumps(message.report.model_dump(mode="json"), ensure_ascii=False, indent=2)
        self._refresh_status_bar()
        self._refresh_summary()
        self._sync_artifacts_from_report(message.report)

    @on(RunFailedMessage)
    def _on_failed(self, message: RunFailedMessage) -> None:
        self.running = False
        self.last_error = message.error_text
        self.current_status = "failed"
        self.query_one("#event-log", RichLog).write(f"[red]Run failed[/red]: {message.error_text}")
        self._refresh_status_bar()
        self._refresh_summary()

    def _append_log(self, event: RuntimeEvent) -> None:
        payload = json.dumps(event.payload, ensure_ascii=False, sort_keys=True) if event.payload else ""
        line = f"[{event.timestamp}] {event.stage:<13} {event.kind:<16} {event.message}"
        if payload:
            line += f" | {payload}"
        self.query_one("#event-log", RichLog).write(line)

    def _ingest_artifact_payload(self, payload: dict[str, Any]) -> None:
        ids = payload.get("artifact_ids") or []
        single = payload.get("artifact_id")
        if single:
            ids = [*ids, single]
        if not ids:
            return
        artifact_dir = Path(self.query_one("#artifact-dir", Input).value or "run-artifacts")
        candidates = list(artifact_dir.glob("**/*"))
        by_name = {path.name: path for path in candidates if path.is_file()}
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
            kind = "artifact"
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
        self.query_one("#artifacts-table", DataTable).add_row(artifact_id, kind, path, preview.replace("
", " "), key=str(row_index))

    def _refresh_stage_list(self) -> None:
        lines = []
        for stage in STAGES:
            status = self.stage_status.get(stage, "pending")
            icon = {"pending": "·", "running": "▶", "done": "✓", "failed": "✗"}.get(status, "·")
            lines.append(f"{icon} {stage:<13} {status}")
        self.query_one("#stage-list", Static).update("\n".join(lines))

    def _refresh_status_bar(self) -> None:
        run_state = "running" if self.running else "idle"
        self.query_one("#status-bar", Static).update(
            f"Run: {run_state} | Current stage: {self.current_stage} | Status: {self.current_status}"
        )

    def _refresh_summary(self) -> None:
        lines = ["Evidence-backed workflow cockpit", ""]
        if self.final_report is not None:
            lines.append(f"Final status: {self.final_report.status}")
            lines.append(f"Artifacts: {len(self.final_report.artifact_ids)}")
            if self.final_report.plan is not None:
                lines.append(f"Plan intent: {self.final_report.plan.task_intent}")
                lines.append(f"Deliverable: {self.final_report.plan.deliverable_kind}")
            if self.final_report.verification is not None:
                lines.append(f"Verification: {'PASS' if self.final_report.verification.passed else 'FAIL'} ({self.final_report.verification.confidence})")
        else:
            lines.append(f"Current stage: {self.current_stage}")
            lines.append(f"Artifacts seen: {len(self.artifact_rows)}")
            if self.last_error:
                lines.append(f"Last error: {self.last_error}")
        self.query_one("#right-summary", Static).update("\n".join(lines))

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
        self.query_one("#event-log", RichLog).write("[bold]Starting workflow run...[/bold]")
        self.run_worker(self._run_workflow(), exclusive=True, thread=False)

    @work(exclusive=True)
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


def run_tui() -> None:
    ForgeMindTUI().run()

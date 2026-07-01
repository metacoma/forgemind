from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from artifact_workflow_runtime.models import RuntimeModel, new_id, utc_now

_ALLOWED_DIFFICULTIES = {"tiny", "small", "medium", "large", "unknown"}
EvaluationMode = Literal["scripted", "live"]
LiveSafety = Literal["safe_for_live", "requires_approval_for_live", "unsafe_for_live", "dry_run_only"]


class ScenarioSpec(RuntimeModel):
    scenario_id: str
    title: str
    description: str = ""
    task_text: str
    tags: list[str] = Field(default_factory=list)
    execution_family: str = "repository_change"
    difficulty: str = "small"
    target_runtime_profile: str = "repo_feature_simple"
    environment_profile: str = "default"
    expected_obligations: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    required_stage_patterns: list[str] = Field(default_factory=list)
    allowed_terminal_statuses: list[str] = Field(default_factory=lambda: ["completed"])
    forbidden_terminal_statuses: list[str] = Field(default_factory=list)
    expected_reentry_behavior: list[str] = Field(default_factory=list)
    expected_packet_patterns: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    notes: str = ""

    # Stage 5.1 live-mode extensions. These fields intentionally live on the
    # same scenario contract so scripted and live benchmark runs stay comparable.
    live_task_text_override: str | None = None
    live_timeout_seconds: int | None = None
    live_environment_profile: str | None = None
    live_allowed_terminal_statuses: list[str] | None = None
    live_required_evidence: list[str] | None = None
    requires_live_repo: bool = False
    requires_live_host: bool = False
    requires_live_openhands: bool = False
    requires_live_network: bool = False
    safe_for_live: bool = False
    requires_approval_for_live: bool = False
    unsafe_for_live: bool = False
    dry_run_only: bool = False
    needs_isolated_repo: bool = False
    needs_isolated_host: bool = False
    live_notes: str = ""

    @field_validator("difficulty")
    @classmethod
    def _validate_difficulty(cls, value: str) -> str:
        normalized = str(value or "unknown").strip().lower()
        if normalized not in _ALLOWED_DIFFICULTIES:
            raise ValueError(f"Unknown scenario difficulty: {value!r}")
        return normalized

    @model_validator(mode="after")
    def _validate_live_safety(self) -> "ScenarioSpec":
        live_flags = [self.safe_for_live, self.requires_approval_for_live, self.unsafe_for_live, self.dry_run_only]
        if sum(1 for item in live_flags if item) > 1:
            raise ValueError("Only one live-safety flag may be true")
        return self

    def task_text_for_mode(self, mode: EvaluationMode) -> str:
        if mode == "live" and self.live_task_text_override:
            return self.live_task_text_override
        return self.task_text

    def timeout_for_mode(self, request_timeout_seconds: int, mode: EvaluationMode) -> int:
        if mode == "live" and self.live_timeout_seconds is not None:
            return self.live_timeout_seconds
        return request_timeout_seconds

    def allowed_terminal_statuses_for_mode(self, mode: EvaluationMode) -> list[str]:
        if mode == "live" and self.live_allowed_terminal_statuses:
            return list(self.live_allowed_terminal_statuses)
        return list(self.allowed_terminal_statuses)

    def required_evidence_for_mode(self, mode: EvaluationMode) -> list[str]:
        if mode == "live" and self.live_required_evidence is not None:
            return list(self.live_required_evidence)
        return list(self.required_evidence)

    def live_safety(self) -> LiveSafety:
        if self.unsafe_for_live:
            return "unsafe_for_live"
        if self.dry_run_only:
            return "dry_run_only"
        if self.requires_approval_for_live:
            return "requires_approval_for_live"
        return "safe_for_live" if self.safe_for_live else "requires_approval_for_live"


class ScenarioRunRequest(RuntimeModel):
    scenario_id: str
    mode: EvaluationMode = "scripted"
    runtime_config_path: str | None = None
    model_routing_config_path: str | None = None
    artifact_dir: str = "eval_runs"
    reuse_mode: str = "isolated"
    auto_approve: bool = True
    timeout_seconds: int = 120
    environment_overrides: dict[str, Any] = Field(default_factory=dict)

    # Live runtime stack settings. The evaluation package only builds a normal
    # runtime controller from these values; it does not reimplement the runtime.
    direct_llm_endpoint: str | None = None
    direct_llm_model: str | None = None
    direct_llm_api_key: str | None = None
    openhands_endpoint: str | None = None
    openhands_model: str | None = None
    openhands_api_key: str | None = None
    sandbox_id: str | None = None
    conversation_id: str | None = None
    approve_live: bool = False
    allow_live_network: bool = False
    allow_live_host: bool = False
    allow_live_publish: bool = False
    strategy_selection_mode: str = "rule_based"


class ScoreComponent(RuntimeModel):
    name: str
    score: int
    max_score: int
    passed: bool
    reason: str = ""


class ScenarioScorecard(RuntimeModel):
    scenario_id: str
    overall_score: int
    completion_score: int
    acceptance_score: int
    evidence_score: int
    loop_score: int
    packet_score: int
    policy_score: int
    passed: bool
    hard_failures: list[str] = Field(default_factory=list)
    soft_failures: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    components: list[ScoreComponent] = Field(default_factory=list)


class ScenarioRunResult(RuntimeModel):
    scenario_id: str
    run_id: str = Field(default_factory=lambda: new_id("scenario_run"))
    started_at: str = Field(default_factory=utc_now)
    finished_at: str | None = None
    terminal_status: str
    runtime_status: str
    execution_mode: EvaluationMode = "scripted"
    acceptance_status: str | None = None
    packet_count: int = 0
    transition_count: int = 0
    reentry_count: int = 0
    repair_count: int = 0
    artifacts: list[str] = Field(default_factory=list)
    artifact_dir: str | None = None
    final_report: dict[str, Any] = Field(default_factory=dict)
    stage_sequence: list[str] = Field(default_factory=list)
    packet_types: list[str] = Field(default_factory=list)
    required_evidence_found: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    live_run_id: str | None = None
    live_artifact_dir: str | None = None
    live_metadata: dict[str, Any] = Field(default_factory=dict)
    scorecard: ScenarioScorecard | None = None
    fail_reasons: list[str] = Field(default_factory=list)


class PackSummary(RuntimeModel):
    pack_id: str
    scenario_count: int
    passed_count: int
    completion_rate: float
    acceptance_pass_rate: float
    false_success_rate: float
    average_loops: float
    average_packets: float
    average_repairs: float
    average_duration_seconds: float
    mode_counts: dict[str, int] = Field(default_factory=dict)


class EvaluationRunReport(RuntimeModel):
    report_id: str = Field(default_factory=lambda: new_id("eval_report"))
    generated_at: str = Field(default_factory=utc_now)
    pack_id: str
    scenario_results: list[ScenarioRunResult] = Field(default_factory=list)
    summary: PackSummary
    execution_mode: EvaluationMode | str = "scripted"
    model_routing_config_path: str | None = None
    runtime_config_path: str | None = None
    notes: list[str] = Field(default_factory=list)


class ScenarioComparison(RuntimeModel):
    scenario_id: str
    before_status: str
    after_status: str
    before_score: int
    after_score: int
    delta: int
    regression: bool
    improvement: bool
    before_mode: str = "scripted"
    after_mode: str = "scripted"
    notes: list[str] = Field(default_factory=list)


class PackComparison(RuntimeModel):
    pack_id: str
    before_summary: PackSummary
    after_summary: PackSummary
    regressions: list[ScenarioComparison] = Field(default_factory=list)
    improvements: list[ScenarioComparison] = Field(default_factory=list)
    overall_delta: float
    before_mode: str = "scripted"
    after_mode: str = "scripted"

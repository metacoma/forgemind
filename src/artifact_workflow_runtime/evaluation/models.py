from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from artifact_workflow_runtime.models import RuntimeModel, new_id, utc_now

_ALLOWED_DIFFICULTIES = {"tiny", "small", "medium", "large", "unknown"}


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

    @field_validator("difficulty")
    @classmethod
    def _validate_difficulty(cls, value: str) -> str:
        normalized = str(value or "unknown").strip().lower()
        if normalized not in _ALLOWED_DIFFICULTIES:
            raise ValueError(f"Unknown scenario difficulty: {value!r}")
        return normalized


class ScenarioRunRequest(RuntimeModel):
    scenario_id: str
    runtime_config_path: str | None = None
    model_routing_config_path: str | None = None
    artifact_dir: str = "eval_runs"
    reuse_mode: str = "isolated"
    auto_approve: bool = True
    timeout_seconds: int = 120
    environment_overrides: dict[str, Any] = Field(default_factory=dict)


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
    acceptance_status: str | None = None
    packet_count: int = 0
    transition_count: int = 0
    reentry_count: int = 0
    repair_count: int = 0
    artifacts: list[str] = Field(default_factory=list)
    final_report: dict[str, Any] = Field(default_factory=dict)
    stage_sequence: list[str] = Field(default_factory=list)
    packet_types: list[str] = Field(default_factory=list)
    required_evidence_found: list[str] = Field(default_factory=list)
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


class EvaluationRunReport(RuntimeModel):
    report_id: str = Field(default_factory=lambda: new_id("eval_report"))
    generated_at: str = Field(default_factory=utc_now)
    pack_id: str
    scenario_results: list[ScenarioRunResult] = Field(default_factory=list)
    summary: PackSummary
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
    notes: list[str] = Field(default_factory=list)


class PackComparison(RuntimeModel):
    pack_id: str
    before_summary: PackSummary
    after_summary: PackSummary
    regressions: list[ScenarioComparison] = Field(default_factory=list)
    improvements: list[ScenarioComparison] = Field(default_factory=list)
    overall_delta: float

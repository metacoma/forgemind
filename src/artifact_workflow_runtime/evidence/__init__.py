from __future__ import annotations

import re
from dataclasses import dataclass

from artifact_workflow_runtime.models import (
    BlockerEvidence,
    CommandEvidence,
    DiffEvidence,
    ExtractedFact,
    FileEvidence,
    MutationSummary,
    PostcheckSummary,
    StructuredEvidence,
    TestCheckEvidence,
)

_COMMAND_RE = re.compile(r"^\s*(?:[$#]\s+|(?:command|cmd|ran|run)\s*[:=]\s*)(?P<cmd>.+?)\s*$", re.IGNORECASE)
_PATH_RE = re.compile(r"(?P<path>(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+|[A-Za-z0-9_.-]+\.(?:py|go|rs|ts|js|tsx|jsx|yaml|yml|json|toml|md|sh|sql|proto))")
_TEST_RE = re.compile(r"\b(?P<name>pytest|go test|npm test|pnpm test|yarn test|cargo test|gradle|mvn test|tox|ruff|mypy|semgrep|trivy|github actions|pr checks?)\b", re.IGNORECASE)
_PASS_RE = re.compile(r"\b(pass(?:ed|es)?|success(?:ful)?|ok|green|0 failed)\b", re.IGNORECASE)
_FAIL_RE = re.compile(r"\b(fail(?:ed|ure)?|error|red|non-zero|nonzero|blocked|timeout|exception)\b", re.IGNORECASE)
_CHANGED_RE = re.compile(r"\b(changed|modified|created|updated|deleted|wrote|patched)\b", re.IGNORECASE)
_OBSERVED_RE = re.compile(r"\b(read|observed|inspected|found|located)\b", re.IGNORECASE)
_FACT_RE = re.compile(r"^\s*(?:fact|finding|found|observed)\s*[:=-]\s*(?P<fact>.+)$", re.IGNORECASE)
_BLOCKER_RE = re.compile(r"\b(blocker|blocked|cannot|can't|unable|permission denied|not found|missing|failed|error|timeout)\b", re.IGNORECASE)
_DIFF_RE = re.compile(r"\b(diff|patch|hunk|@@|git diff)\b", re.IGNORECASE)


@dataclass(slots=True)
class EvidenceExtractor:
    """Best-effort bridge from agent text to machine-usable evidence.

    OpenHands can only be required to return bounded evidence in text today. This
    extractor makes that evidence useful to the runtime without letting the agent
    own workflow decisions. It is intentionally conservative: uncertain findings
    become low-confidence facts or blockers, not controller decisions.
    """

    max_items: int = 50
    max_excerpt_chars: int = 500

    def from_text(self, text: str, *, artifact_id: str | None = None, changed_default: bool = False) -> StructuredEvidence:
        lines = [line.rstrip() for line in text.splitlines()]
        commands: list[CommandEvidence] = []
        files_changed: list[FileEvidence] = []
        files_observed: list[FileEvidence] = []
        facts: list[ExtractedFact] = []
        diffs: list[DiffEvidence] = []
        tests: list[TestCheckEvidence] = []
        blockers: list[BlockerEvidence] = []
        seen_commands: set[str] = set()
        seen_changed: set[str] = set()
        seen_observed: set[str] = set()
        seen_tests: set[str] = set()

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            artifact_ids = [artifact_id] if artifact_id else []
            command_match = _COMMAND_RE.match(stripped)
            if command_match:
                command = command_match.group("cmd").strip().strip("`")
                if command and command not in seen_commands:
                    commands.append(CommandEvidence(command=command, output_excerpt=self._clip(stripped), output_artifact_ids=artifact_ids))
                    seen_commands.add(command)
            fact_match = _FACT_RE.match(stripped)
            if fact_match:
                facts.append(ExtractedFact(subject="agent_observation", fact=self._clip(fact_match.group("fact")), source=artifact_id, confidence="medium", artifact_ids=artifact_ids))
            if _TEST_RE.search(stripped):
                name = _TEST_RE.search(stripped).group("name")  # type: ignore[union-attr]
                key = f"{name}:{stripped[:120]}"
                if key not in seen_tests:
                    passed = True if _PASS_RE.search(stripped) else (False if _FAIL_RE.search(stripped) else None)
                    status = "passed" if passed is True else ("failed" if passed is False else "unknown")
                    tests.append(TestCheckEvidence(name=name, passed=passed, status=status, output_excerpt=self._clip(stripped), artifact_ids=artifact_ids))
                    seen_tests.add(key)
            if _DIFF_RE.search(stripped):
                diffs.append(DiffEvidence(summary=self._clip(stripped), diff_artifact_ids=artifact_ids))
            for path in _PATH_RE.findall(stripped):
                if _CHANGED_RE.search(stripped) or changed_default:
                    if path not in seen_changed:
                        files_changed.append(FileEvidence(path=path, action="changed", summary=self._clip(stripped), artifact_ids=artifact_ids))
                        seen_changed.add(path)
                elif _OBSERVED_RE.search(stripped):
                    if path not in seen_observed:
                        files_observed.append(FileEvidence(path=path, action="observed", summary=self._clip(stripped), artifact_ids=artifact_ids))
                        seen_observed.add(path)
            if _BLOCKER_RE.search(stripped):
                severity = "high" if re.search(r"\b(permission denied|failed|error|timeout|blocked)\b", stripped, re.IGNORECASE) else "medium"
                blockers.append(BlockerEvidence(summary=self._clip(stripped), severity=severity, artifact_ids=artifact_ids))

        commands = commands[: self.max_items]
        files_changed = files_changed[: self.max_items]
        files_observed = files_observed[: self.max_items]
        facts = facts[: self.max_items]
        diffs = diffs[: self.max_items]
        tests = tests[: self.max_items]
        blockers = blockers[: self.max_items]
        mutation_summary = MutationSummary(
            changed=bool(files_changed),
            summary=(f"Detected {len(files_changed)} changed file references." if files_changed else "No changed file references detected."),
            files_changed=[item.path for item in files_changed],
        )
        postcheck_summary = PostcheckSummary(
            attempted=bool(tests),
            summary=(f"Detected {len(tests)} test/check evidence items." if tests else "No test/check evidence detected."),
            checks=tests,
        )
        return StructuredEvidence(
            commands_run=commands,
            files_changed=files_changed,
            files_observed=files_observed,
            extracted_facts=facts,
            diffs=diffs,
            tests=tests,
            blockers=blockers,
            mutation_summary=mutation_summary,
            postcheck_summary=postcheck_summary,
        )

    def _clip(self, text: str) -> str:
        return text if len(text) <= self.max_excerpt_chars else text[: self.max_excerpt_chars] + "..."

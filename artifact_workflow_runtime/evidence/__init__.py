from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from artifact_workflow_runtime.models import (
    BlockerEvidence,
    BlockerKind,
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
_TEST_RE = re.compile(r"\b(?P<name>pytest|go test|npm test|pnpm test|yarn test|cargo test|cmake|make test|gradle|mvn test|tox|ruff|mypy|semgrep|trivy|integration tests?|e2e tests?|github actions|pr checks?)\b", re.IGNORECASE)
_PASS_RE = re.compile(r"\b(pass(?:ed|es)?|success(?:ful)?|ok|green|0 failed)\b", re.IGNORECASE)
_FAIL_RE = re.compile(r"\b(fail(?:ed|ure)?|error|red|non-zero|nonzero|blocked|timeout|exception)\b", re.IGNORECASE)
_NOT_RUN_RE = re.compile(r"\b(not run|not executed|did not run|was not run|were not run|skipped|not launched|not performed|no .*run evidence)\b", re.IGNORECASE)
_CHANGED_RE = re.compile(r"\b(changed|modified|created|updated|deleted|wrote|patched|implemented|added|applied fix|fixed|edited)\b", re.IGNORECASE)
_OBSERVED_RE = re.compile(r"\b(read|observed|inspected|found|located)\b", re.IGNORECASE)
_FACT_RE = re.compile(r"^\s*(?:fact|finding|found|observed)\s*[:=-]\s*(?P<fact>.+)$", re.IGNORECASE)
_BLOCKER_RE = re.compile(r"\b(blocker|blocked|cannot|can't|unable|permission denied|not found|missing|failed|error|timeout)\b", re.IGNORECASE)
_DIFF_RE = re.compile(r"\b(diff|patch|hunk|@@|git diff)\b", re.IGNORECASE)
_EVIDENCE_KEYS = {"commands_run", "files_changed", "files_observed", "extracted_facts", "diffs", "tests", "checks", "blockers", "mutation_summary", "postcheck_summary"}


class EvidenceContractError(ValueError):
    """Raised when operational output does not satisfy strict evidence mode."""


def render_structured_evidence_summary(evidence: StructuredEvidence) -> str:
    """Render the operational summary used by controller/context layers.

    This intentionally ignores raw prose unless no structured information exists.
    Raw text remains attached as an artifact; the runtime should use this summary
    and the typed evidence fields for policy, verification, and finalization.
    """

    lines: list[str] = []
    if evidence.commands_run:
        lines.append("commands_run:")
        lines.extend(f"- {item.command} exit={item.exit_code if item.exit_code is not None else 'unknown'}" for item in evidence.commands_run[:20])
    if evidence.files_changed:
        lines.append("files_changed:")
        lines.extend(f"- {item.path}: {item.summary or item.action}" for item in evidence.files_changed[:20])
    if evidence.files_observed:
        lines.append("files_observed:")
        lines.extend(f"- {item.path}: {item.summary or item.action}" for item in evidence.files_observed[:20])
    if evidence.extracted_facts:
        lines.append("facts:")
        lines.extend(f"- {item.subject}: {item.fact} [{item.confidence}]" for item in evidence.extracted_facts[:20])
    if evidence.diffs:
        lines.append("diffs:")
        lines.extend(f"- {item.path or 'unknown'}: {item.summary}" for item in evidence.diffs[:20])
    if evidence.tests:
        lines.append("checks:")
        lines.extend(f"- {item.name}: {item.status}; {item.output_excerpt or ''}".rstrip() for item in evidence.tests[:20])
    if evidence.blockers:
        lines.append("blockers:")
        lines.extend(f"- [{item.severity}] {item.summary}" for item in evidence.blockers[:20])
    lines.append(f"mutation_summary: changed={evidence.mutation_summary.changed}; files={evidence.mutation_summary.files_changed}; summary={evidence.mutation_summary.summary}")
    lines.append(f"postcheck_summary: attempted={evidence.postcheck_summary.attempted}; summary={evidence.postcheck_summary.summary}")
    return "\n".join(lines).strip()


@dataclass(slots=True)
class EvidenceExtractor:
    """Normalize OpenHands output into machine-usable evidence.

    Preferred path: OpenHands returns JSON with structured evidence sections. The
    extractor validates and normalizes that schema. Fallback path: conservative
    regex extraction from text. This keeps the extractor as a compatibility layer,
    not the main source of architectural truth.
    """

    max_items: int = 50
    max_excerpt_chars: int = 500

    def from_agent_output(
        self,
        text: str,
        *,
        artifact_id: str | None = None,
        changed_default: bool = False,
        strict: bool = False,
    ) -> StructuredEvidence:
        structured = self._from_json_contract(text, artifact_id=artifact_id)
        if structured is not None:
            return structured
        if strict:
            raise EvidenceContractError(
                "Strict evidence mode requires a JSON object with structured_evidence "
                "or top-level evidence keys; prose/regex fallback is not accepted."
            )
        return self.from_text(text, artifact_id=artifact_id, changed_default=changed_default)

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
                    if _NOT_RUN_RE.search(stripped):
                        passed = None
                        status = "not_run"
                    else:
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
                blockers.append(BlockerEvidence(summary=self._clip(stripped), severity=severity, blocker_kind=_classify_blocker_kind(stripped), artifact_ids=artifact_ids))

        commands = commands[: self.max_items]
        files_changed = files_changed[: self.max_items]
        files_observed = files_observed[: self.max_items]
        facts = facts[: self.max_items]
        diffs = diffs[: self.max_items]
        tests = tests[: self.max_items]
        blockers = blockers[: self.max_items]
        evidence = StructuredEvidence(
            commands_run=commands,
            files_changed=files_changed,
            files_observed=files_observed,
            extracted_facts=facts,
            diffs=diffs,
            tests=tests,
            blockers=blockers,
        )
        if changed_default and not evidence.files_changed and _CHANGED_RE.search(text):
            evidence.mutation_summary = MutationSummary(
                changed=True,
                summary="Agent reported mutation/change activity but did not provide file-level evidence.",
                files_changed=[],
            )
        return self._with_summaries(evidence)

    def _from_json_contract(self, text: str, *, artifact_id: str | None = None) -> StructuredEvidence | None:
        payload = _extract_json_payload(text)
        if not isinstance(payload, Mapping):
            return None
        data = payload.get("structured_evidence") if isinstance(payload.get("structured_evidence"), Mapping) else payload
        if not isinstance(data, Mapping) or not (_EVIDENCE_KEYS & set(data.keys())):
            return None
        artifact_ids = [artifact_id] if artifact_id else []
        try:
            evidence = StructuredEvidence(
                commands_run=[self._command(item, artifact_ids) for item in _list(data.get("commands_run"))],
                files_changed=[self._file(item, "changed", artifact_ids) for item in _list(data.get("files_changed"))],
                files_observed=[self._file(item, "observed", artifact_ids) for item in _list(data.get("files_observed"))],
                extracted_facts=[self._fact(item, artifact_ids) for item in _list(data.get("extracted_facts") or data.get("facts"))],
                diffs=[self._diff(item, artifact_ids) for item in _list(data.get("diffs"))],
                tests=[self._test(item, artifact_ids) for item in _list(data.get("tests") or data.get("checks"))],
                blockers=[self._blocker(item, artifact_ids) for item in _list(data.get("blockers"))],
                mutation_summary=MutationSummary.model_validate(data.get("mutation_summary")) if isinstance(data.get("mutation_summary"), Mapping) else MutationSummary(),
                postcheck_summary=PostcheckSummary.model_validate(data.get("postcheck_summary")) if isinstance(data.get("postcheck_summary"), Mapping) else PostcheckSummary(),
            )
        except Exception:
            return None
        return self._with_summaries(evidence)

    def _with_summaries(self, evidence: StructuredEvidence) -> StructuredEvidence:
        if not evidence.mutation_summary.summary:
            evidence.mutation_summary = MutationSummary(
                changed=bool(evidence.files_changed),
                summary=(f"Detected {len(evidence.files_changed)} changed file references." if evidence.files_changed else "No changed file references detected."),
                files_changed=[item.path for item in evidence.files_changed],
            )
        if not evidence.postcheck_summary.summary:
            evidence.postcheck_summary = PostcheckSummary(
                attempted=bool(evidence.tests),
                summary=(f"Detected {len(evidence.tests)} test/check evidence items." if evidence.tests else "No test/check evidence detected."),
                checks=evidence.tests,
            )
        return evidence

    def _command(self, item: object, artifact_ids: list[str]) -> CommandEvidence:
        if isinstance(item, Mapping):
            data = dict(item)
            data.setdefault("output_artifact_ids", artifact_ids)
            return CommandEvidence.model_validate(data)
        return CommandEvidence(command=str(item), output_artifact_ids=artifact_ids)

    def _file(self, item: object, action: str, artifact_ids: list[str]) -> FileEvidence:
        if isinstance(item, Mapping):
            data = dict(item)
            data.setdefault("action", action)
            data.setdefault("artifact_ids", artifact_ids)
            return FileEvidence.model_validate(data)
        return FileEvidence(path=str(item), action=action, artifact_ids=artifact_ids)

    def _fact(self, item: object, artifact_ids: list[str]) -> ExtractedFact:
        if isinstance(item, Mapping):
            data = dict(item)
            data.setdefault("artifact_ids", artifact_ids)
            data.setdefault("subject", "agent_observation")
            data.setdefault("confidence", "medium")
            return ExtractedFact.model_validate(data)
        return ExtractedFact(subject="agent_observation", fact=str(item), confidence="medium", artifact_ids=artifact_ids)

    def _diff(self, item: object, artifact_ids: list[str]) -> DiffEvidence:
        if isinstance(item, Mapping):
            data = dict(item)
            data.setdefault("diff_artifact_ids", artifact_ids)
            return DiffEvidence.model_validate(data)
        return DiffEvidence(summary=str(item), diff_artifact_ids=artifact_ids)

    def _test(self, item: object, artifact_ids: list[str]) -> TestCheckEvidence:
        if isinstance(item, Mapping):
            data = dict(item)
            data.setdefault("artifact_ids", artifact_ids)
            return TestCheckEvidence.model_validate(data)
        text = str(item)
        passed = True if _PASS_RE.search(text) else (False if _FAIL_RE.search(text) else None)
        status = "passed" if passed is True else ("failed" if passed is False else "unknown")
        return TestCheckEvidence(name=text[:80], passed=passed, status=status, output_excerpt=self._clip(text), artifact_ids=artifact_ids)

    def _blocker(self, item: object, artifact_ids: list[str]) -> BlockerEvidence:
        if isinstance(item, Mapping):
            data = dict(item)
            data.setdefault("artifact_ids", artifact_ids)
            if "blocker_kind" not in data and "kind" not in data:
                data["blocker_kind"] = _classify_blocker_kind(str(data.get("summary") or data))
            elif "kind" in data and "blocker_kind" not in data:
                data["blocker_kind"] = data.pop("kind")
            return BlockerEvidence.model_validate(data)
        text = str(item)
        return BlockerEvidence(summary=text, blocker_kind=_classify_blocker_kind(text), artifact_ids=artifact_ids)

    def _clip(self, text: str) -> str:
        return text if len(text) <= self.max_excerpt_chars else text[: self.max_excerpt_chars] + "..."


def _classify_blocker_kind(text: str) -> BlockerKind:
    lowered = text.lower()
    env_markers = ("missing", "not found", "not installed", "unavailable", "cannot find", "no such file", "dependency")
    integration_markers = ("freeplane", "x11", "display", "integration", "gui", "runtime", "service", "daemon")
    if any(marker in lowered for marker in integration_markers) and any(marker in lowered for marker in env_markers):
        return BlockerKind.INTEGRATION_ENVIRONMENT_UNAVAILABLE
    if any(marker in lowered for marker in ("missing dependency", "dependency missing", "not installed", "cannot find", "not found")):
        return BlockerKind.MISSING_ENVIRONMENT_DEPENDENCY
    if any(marker in lowered for marker in ("runtime prerequisite", "prerequisite", "environment unavailable")):
        return BlockerKind.MISSING_RUNTIME_PREREQUISITE
    if any(marker in lowered for marker in ("test failed", "tests failed", "failure", "assertion")):
        return BlockerKind.TEST_FAILURE
    if any(marker in lowered for marker in ("missing evidence", "not run", "not executed")):
        return BlockerKind.MISSING_EVIDENCE
    return BlockerKind.GENERIC


def _list(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


_FENCED_JSON_RE = re.compile(r"```(?:\s*json)?\s*(?P<body>[\s\S]*?)```", re.IGNORECASE)


def _extract_json_payload(text: str) -> object | None:
    stripped = text.strip()
    if not stripped:
        return None

    candidates: list[str] = [stripped]
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        inner = "\n".join(lines).strip()
        if inner and inner not in candidates:
            candidates.insert(0, inner)

    for match in _FENCED_JSON_RE.finditer(stripped):
        body = match.group("body").strip()
        if body and body not in candidates:
            candidates.insert(0, body)

    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            for idx, ch in enumerate(candidate):
                if ch not in "[{":
                    continue
                try:
                    obj, _ = decoder.raw_decode(candidate[idx:])
                    return obj
                except json.JSONDecodeError:
                    continue
    return None

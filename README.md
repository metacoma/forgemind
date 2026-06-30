# artifact-workflow-runtime

`artifact-workflow-runtime` is an engineering runtime/control-plane for agentic work with hard separation between orchestration, text reasoning, world execution, policy, state, and artifacts.

The current codebase is no longer organized around roles. It is organized around typed stage contracts and bounded backend responsibilities:

- **WorkflowController / RuntimeKernel** owns workflow decisions.
- **LangGraph** runs the state machine.
- **Direct LLM backend** receives text-only `LLMRequest` packets.
- **OpenHands backend** receives bounded `observe`, `research`, `execute`, `publish`, or `verify` work packets.
- **ArtifactStore + typed WorkflowStateSnapshot** are the source of truth.
- **StructuredEvidence / EvidenceBundle** converts raw agent text into machine-usable evidence.
- **ContextBuilder** converts persisted artifacts into a text-only `ContextPacket` for Direct LLM reasoning.
- **PolicyEngine / EvidenceGate / ApprovalProvider** are separate control layers.

## Architecture map

```text
src/artifact_workflow_runtime/
  controller/          # public WorkflowController entrypoint
  control_plane/       # RuntimeKernel: next-step and policy gate decisions
  graph/               # LangGraph workflow + offline compat state graph
  models/              # Pydantic typed contracts and WorkflowState
  artifacts/           # file-backed ArtifactStore and index
  evidence/            # raw OpenHands text -> structured evidence bundles
  context/             # artifacts -> ContextPacket text bridge
  llm_backend/         # text-only Direct LLM adapters and fake backend
  openhands_adapter/   # bounded OpenHands observe/execute/verify adapter
  observation/         # ObservationRequest / research request construction
  policy/              # policy, evidence gate, approvals
  reports/             # FinalReport assembly
  runtime_events.py    # stage and transport telemetry
```

## Runtime flow

1. `intake` stores the task as an artifact.
2. `classify` asks the Direct LLM for a typed `TaskClassification`.
3. `route` asks the Direct LLM what evidence is required before planning.
4. `RuntimeKernel` decides whether to run external research and/or world observation.
5. OpenHands collects facts as bounded observation/research packets.
6. `ContextBuilder` builds a text-only `ContextPacket` from artifacts.
7. Direct LLM synthesizes obligations and an `ExecutionPlan` from the `ContextPacket`.
8. `RuntimeKernel` + `EvidenceGate` + `PolicyEngine` decide whether execution is blocked, allowed, or approval-gated.
9. OpenHands executes only after policy/approval and receives an explicit bounded `ExecutionRequest`.
10. Optional publish obligations are handled as a separate bounded packet.
11. Direct LLM verifies evidence from artifacts; final status is assembled by `FinalReportBuilder`.

## Backend invariants

### Direct LLM

The Direct LLM gets only text. `LLMRequest` now carries explicit `task_text`, `instructions`, `input_artifact_ids`, `allowed_inputs`, and `forbidden_inputs` in addition to the rendered prompt string used by the transport adapter. Its forbidden inputs include filesystem, shell, git, host, Kubernetes, and network runtime state. It must not be given live world access.

### OpenHands

OpenHands is not the workflow brain. It receives bounded work packets and returns evidence/artifacts/blockers. The adapter validates observe/execute/world-verification packet kinds, rejects mutating observation contracts, and stores raw plus structured evidence artifacts. It does not decide the next graph step.

### Artifacts, evidence, and state

Every meaningful step writes typed records or evidence files to `ArtifactStore`. `WorkflowStateSnapshot` validates the LangGraph wire state as typed durable runtime state, and the controller persists a final `workflow_state_snapshot` artifact for debugging/resume work. OpenHands text evidence is also converted into `StructuredEvidence` / `EvidenceBundle` records containing commands, file observations/changes, facts, diffs, tests/checks, blockers, mutation summaries, and postcheck summaries.

## Installation

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test,langgraph]'
```

`langgraph` is the preferred runtime layer. The project also includes a tiny `graph.compat` fallback so offline tests can run when LangGraph is unavailable.

## CLI

```bash
artifact-workflow-run \
  --task "Inspect repo metacoma/freeplane_plugin_grpc and fix failing tests" \
  --direct-llm-endpoint http://127.0.0.1:4000/v1 \
  --direct-llm-model openai/reasoner \
  --openhands-endpoint http://127.0.0.1:3000 \
  --openhands-model openai/executor \
  --auto-approve
```

The CLI prints the final JSON `FinalReport` and writes artifacts to `./run-artifacts` by default.

## Per-stage model routing

Supported YAML format:

```yaml
direct_llm:
  classify: openai/qwen36-27b
  route: openai/qwen36-35b
  obligations: openai/qwen36-35b
  plan: openai/qwen36-35b
  verify: openai/qwen36-27b

openhands:
  observe: openai/qwen36-27b
  research: openai/qwen36-27b
  execute: openai/qwen36-35b
  publish: openai/qwen36-35b

verification_checks:
  # Optional: when present, each plan.verification_checks item is assessed
  # as a separate typed VerificationCheckRequest with its own model_override.
  unit_tests: openai/qwen36-27b
  integration_tests: openai/qwen36-35b
  pr_checks: openai/qwen36-35b
  security: openai/qwen36-35b
  docs: openai/qwen36-27b
  default: openai/qwen36-27b
```

`verification_checks` keys are normalized from human plan checks. For example, `run unit tests` maps to `unit_tests`, while `wait for GitHub Actions PR checks` maps to `pr_checks`. If no check-specific route matches, the runtime falls back to `direct_llm.verify`, then the Direct LLM default model.

Legacy `roles:` configs are rejected.

## Tests

```bash
python -m pytest -q
```

The current test suite covers capability normalization, typed state validation, structured evidence extraction, per-stage and per-verification-check model routing, OpenHands transport fallback, sandbox reuse, runtime events, policy gating, research/observation routing, publish obligations, and verification behavior.

## Acceptance gate hardening

Mutation workflows now have an explicit acceptance layer between verification and finalization. The controller derives a typed `TaskAcceptanceContract` from the task classification, obligations, and execution plan. Verification and finalization no longer treat useful execution evidence as sufficient completion.

The acceptance contract is evaluated into an `AcceptanceDecision` with per-obligation `VerificationObligationResult` records. If any required blocking obligation is `failed`, `blocked`, or `not_run`, the final workflow status cannot be `completed`. Missing runtime prerequisites such as a required Freeplane integration environment are classified as structured environment blockers and produce `needs_environment` rather than soft success.

The key distinction is now explicit:

- `ExecutionResult.execution_status` describes what OpenHands actually managed to do.
- `VerificationResult` describes evidence/world verification results.
- `AcceptanceDecision` decides whether the task is accepted for completion.
- `FinalReport.status` follows the acceptance decision for mutation tasks.

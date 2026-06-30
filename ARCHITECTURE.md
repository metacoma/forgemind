# Architecture

## Goal

This project is an artifact-backed engineering runtime. It approximates a modern agentic/control-plane architecture without making OpenHands the owner of workflow decisions.

```text
User task
  -> WorkflowController
  -> RuntimeKernel decisions
  -> LangGraph state machine
  -> Direct LLM text reasoning
  -> OpenHands bounded world packets
  -> ArtifactStore / WorkflowState source of truth
  -> ContextPacket text bridge
  -> Policy / Approval / Verification gates
  -> FinalReport
```

## Control-plane layers

### WorkflowController

`WorkflowController` is the public runtime entrypoint. It wires services, creates the file-backed `ArtifactStore`, installs `RuntimeKernel`, and invokes the graph with a serializable initial state.

It does not delegate global workflow ownership to OpenHands.

### RuntimeKernel

`control_plane.RuntimeKernel` owns next-step decisions:

- after route: research, observe, or build context
- after research: observe or build context
- after policy: approval, execute, or finalize
- after approval: execute or finalize
- after execution: publish or verify
- policy gate evaluation before OpenHands execution

LangGraph executes these decisions; OpenHands does not make them.

### LangGraph

`graph.workflow` defines the concrete state machine and node implementations. It is the runtime/orchestration layer, not the reasoning backend.

When the optional `langgraph` dependency is unavailable, `graph.compat` provides a minimal async state graph for tests and local development.

## Reasoning layer

### Direct LLM backend

`llm_backend.OpenAICompatibleLLMBackend` accepts `LLMRequest` and returns typed `LLMResult` plus a validated pydantic model.

`LLMRequest` is text-only and declares forbidden inputs:

- filesystem
- shell
- git
- hosts
- Kubernetes
- live network/runtime state

Direct LLM stages include classification, route analysis, obligation synthesis, planning, and evidence verification.

## Execution layer

### OpenHands adapter

`openhands_adapter.OpenHandsAdapter` exposes bounded methods:

- `observe(ObservationRequest)`
- `execute(ExecutionRequest)`
- `verify(VerificationRequest)`

The adapter rejects incompatible work packet kinds. It persists every returned evidence payload as an artifact. Transport garbage such as HTML fallback pages is classified as unusable evidence instead of being treated as successful execution.

### Work packets

Typed request contracts now carry backend/work-packet boundaries:

- `ObservationRequest.work_packet_kind = observe | research`
- `ExecutionRequest.work_packet_kind = execute | publish`
- `VerificationRequest.work_packet_kind = verify`

Requests also declare allowed actions, forbidden actions, expected outputs, capabilities, and metadata. This makes backend boundaries explicit instead of hiding them only inside prompt prose.

## State and artifacts

### ArtifactStore

`artifacts.ArtifactStore` is a file-backed source of truth. It writes:

- text evidence
- JSON model dumps
- an `index.json` registry

Graph state keeps artifact ids and typed model dumps. Later stages rebuild context from artifacts rather than relying on invisible prior prompt state.

### ContextPacket

`context.ContextBuilder` is the explicit world-facts-to-text bridge. It builds a `ContextPacket` from task and artifact contents. Direct LLM planning and verification read world facts only through this text packet.

## Policy / approval / evidence gates

### EvidenceGate

`policy.evidence.EvidenceGate` ensures required world facts exist before execution:

- repository/host/cluster/network families require observation evidence
- route decisions requiring fresh external research must have research evidence
- failed/transport-corrupt evidence blocks execution

### PolicyEngine

`policy.PolicyEngine` decides whether a typed plan is allowed and whether it requires approval. Mutating capabilities or world-changing plans require approval.

### ApprovalProvider

`ApprovalProvider` is a separate interface. The CLI uses `StaticApprovalProvider`; real deployments can replace it with a human approval system.

## Final report

`reports.FinalReportBuilder` computes final status from policy, approval, execution, publish, and verification state. It does not infer success from OpenHands text alone; verification and missing obligations influence the final status.

## Known remaining gaps

The current implementation is now structurally runnable and closer to the target control-plane model, but several deeper improvements remain:

1. Replace remaining long prompt strings in graph nodes with dedicated prompt/work-packet builders.
2. Store richer structured evidence bundles, not only text artifacts plus summaries.
3. Add repair-loop state for failed verification and bounded re-execution.
4. Add real human approval backends.
5. Add persistent workflow resume from `ArtifactStore.index.json`.
6. Split verification into rule-based evidence checks plus Direct LLM judgment more cleanly.


## Verification check routing

Verification is no longer limited to one monolithic `verify` model when check-level routing is configured. The plan keeps human-readable `verification_checks`; the runtime normalizes each check into a stable slot such as `unit_tests`, `integration_tests`, `pr_checks`, `security`, `docs`, or `default`. Each check becomes a typed `VerificationCheckRequest`, is evaluated by the Direct LLM with a check-specific `model_override`, and is persisted as `verification_check_assessment` / `verification_check_llm_raw` artifacts. The aggregate `VerificationResult` is derived from those typed check results, not from hidden prompt decisions.

OpenHands still does not own verification routing. It may provide execution/publish evidence, but check selection and model routing remain controller-side concerns.

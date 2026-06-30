# Gap analysis against target architecture

## What was already good

- The existing project already had a useful workflow shape: intake, classify, route, research/observe, build context, obligations, plan, policy, approval, execute, publish, verify, finalize.
- Direct LLM and OpenHands were conceptually separate in the graph: LLM for classification/routing/planning/verification, OpenHands for observation/execution.
- Core pydantic contracts already existed for task classification, routing, observation, planning, policy, execution, publish, verification, and final reports.
- The OpenHands transport client had practical REST/WebSocket fallback logic and sandbox reuse tests.
- Policy and approval existed as intended imports and were referenced in the graph.

## What was broken or incomplete

- Several imported modules were missing from the tarball: `artifacts`, `context`, `policy`, `graph.compat`, `llm_backend.fake`, `openhands_adapter.models`, and multiple package `__init__.py` files.
- Because of those gaps, the project failed pytest collection before any workflow test could run.
- `WorkflowController` was mostly a dependency wrapper over the graph; next-step decisions were embedded in graph-local functions.
- Evidence gates were embedded directly in the policy graph node instead of being a separate control layer.
- Direct LLM boundaries were mostly prompt-described rather than represented in typed request contracts.
- OpenHands work packet bounds were mostly prompt-described rather than validated as request kind/capability contracts.
- Artifact and context layers were referenced but not implemented, so artifacts could not be a real source of truth.

## Priority fixes implemented

1. Restored missing runtime modules and package exports so the project is runnable.
2. Added a file-backed `ArtifactStore` with `index.json` persistence.
3. Added `ContextBuilder` as the explicit artifacts-to-text bridge for Direct LLM context.
4. Added `RuntimeKernel` and moved route/policy/approval/execution next-step decisions out of graph-local ad hoc logic.
5. Added `EvidenceGate` as a separate policy-side control layer.
6. Added typed backend/work-packet boundary fields: `BackendKind`, `WorkPacketKind`, `EvidenceBundle`, bounded request fields, forbidden actions, expected outputs.
7. Strengthened OpenHands adapter boundary checks so observe/execute/verify reject wrong work packet kinds.
8. Added test doubles and compatibility runtime missing from the archive.
9. Added tests for control-plane decisions, artifact-backed context, and OpenHands packet boundary validation.
10. Updated README and ARCHITECTURE to describe the actual current control-plane runtime.

## Remaining high-value next steps

- Split long graph prompt strings into dedicated prompt/work-packet builder modules.
- Add structured evidence extraction from OpenHands output into `EvidenceBundle` records.
- Add bounded repair loops for failed verification.
- Add persistent workflow resume from `ArtifactStore.index.json`.
- Replace `StaticApprovalProvider` with a real human approval backend.


## Per-check verification model routing update

The previous update supported stage-level routing only: `direct_llm.verify` selected one model for all verification analysis. The new code adds check-level routing through `verification_checks` in the YAML config, typed `VerificationCheckRequest` / `VerificationCheckResult` contracts, artifact-backed per-check assessments, and an aggregate `VerificationResult`. This closes the gap where different verification concerns, such as local unit tests, integration tests, docs checks, security checks, and PR/CI checks, could not be assigned to different models.

## Typed state / structured evidence hardening update

This iteration closes the next set of gaps without replacing the project:

- `models.state.WorkflowStateSnapshot` now provides a pydantic durable state model over the LangGraph wire `TypedDict`. It validates typed request/result fields, status, transitions, controller decisions, artifact ids, and final reports.
- `WorkflowController.run()` now starts from `WorkflowStateSnapshot` and persists the final typed state as a `workflow_state_snapshot` artifact.
- `StructuredEvidence` and `EvidenceBundle` were expanded into machine-usable records: commands run, files changed, files observed, extracted facts, diffs, tests/checks, blockers, mutation summary, and postcheck summary.
- `evidence.EvidenceExtractor` now converts raw OpenHands text into structured evidence artifacts so downstream runtime steps are not forced to depend only on one text blob.
- `ObservationRequest`, `ExecutionRequest`, `PublishRequest`, `VerificationRequest`, and `LLMRequest` now carry more contract fields: objective, focus, scope constraints, plan steps, expected changes, verification commands, task text, input artifact ids, allowed/forbidden inputs, and expected outputs.
- `OpenHandsAdapter` validates bounded packet contracts more strictly: observation packets cannot allow mutation, execution packets must forbid changing workflow decisions, and `verify()` is reserved for `backend=openhands` + `mode=world_check`.
- `RuntimeKernel` now exposes a `verification_strategy()` decision so evidence-review verification and world-check verification are explicit controller choices rather than prompt-level ambiguity.

Remaining gaps after this pass:

1. Some long prompt strings still live in graph nodes and should be moved to dedicated work-packet builder modules.
2. Repair loops are still not first-class typed state transitions.
3. Resume/replay from `workflow_state_snapshot` + `ArtifactStore.index.json` is documented but not yet implemented.
4. The structured evidence extractor is conservative and heuristic; OpenHands should eventually be instructed/validated to emit a strict JSON evidence schema directly.

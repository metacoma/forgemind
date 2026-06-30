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

## Contract/state hardening pass

Closed in this pass:

- `context_packet` in `WorkflowStateSnapshot` is now a typed `ContextPacket`, not a `JsonDict`.
- Request contracts now include `EvidenceRequirements` and `StructuredResponseContract` and render through `compiled_prompt()`.
- Direct LLM and OpenHands backends now send compiled typed contracts instead of raw prompt strings.
- OpenHands output normalization prefers structured JSON evidence sections and uses heuristic extraction only as fallback.
- `EvidenceBundle` now separates raw text artifact ids from structured artifact ids and exposes an operational summary.
- `ContextBuilder` renders structured evidence bundles as typed summaries for Direct LLM reasoning.
- Graph nodes now record typed `StageTransition` and `ControllerDecision` items into workflow state.
- `RuntimeKernel` now exposes fact/planning/execution/verification readiness checks.

Remaining debt:

1. The execution and publish narrative bodies still live in `graph/workflow.py`; they are now compiled inside typed packets, but should later move into dedicated work-packet builder modules.
2. Repair loops still finalize after failed verification; a future pass should add typed `RepairRequest` / `RepairResult` and bounded re-execution edges.
3. Durable resume/replay from `workflow_state_snapshot` and `ArtifactStore.index.json` is still not implemented.

## Acceptance / verification finalization hardening update

Closed in this pass:

- Added typed acceptance models: `TaskAcceptanceContract`, `AcceptanceObligation`, `VerificationObligationResult`, `AcceptanceDecision`, `ExecutionStatus`, `AcceptanceStatus`, and typed environment blockers.
- Added an explicit graph `acceptance` stage between `verify` and `finalize`.
- Final reports now follow `AcceptanceDecision.final_workflow_status` when available.
- Mutation tasks cannot finalize as `completed` when required obligations are `failed`, `blocked`, or `not_run`.
- Missing Freeplane/integration/runtime prerequisites are classified as `missing_environment_dependency`, `missing_runtime_prerequisite`, or `integration_environment_unavailable` and produce `needs_environment`.
- Added regression coverage for the C++ gRPC client / Freeplane integration blocker scenario.

Remaining debt:

1. Acceptance obligation derivation is deterministic and typed, but still heuristic. Future iterations can make obligations first-class planner output validated by policy.
2. OpenHands should eventually emit strict JSON evidence with blocker kinds directly instead of relying on fallback blocker normalization.


## Lifecycle / policy engine pass

Closed in this pass:

- Added a dedicated lifecycle state-transition layer instead of continuing to encode publish/finalize decisions as ad hoc `if execution.ok` graph routing.
- Added OPA/Rego policy gates for lifecycle decisions with a strict Python fallback. This keeps the runtime deterministic even when external OPA is not installed.
- Added typed lifecycle facts/events/transition decisions and persisted them in workflow state/artifacts.
- Closed the PR capability leak: `execute` now receives execution-only capabilities and forbids commit/push/create_pr/open_pull_request/publish/wait_pr_checks.
- Split `publish` from `execute` at the adapter API. Tests now expect `calls["publish"]`, not a second `execute()` call.
- Added `execution_review` before publish. A PR created during execute is a control-plane violation, not an acceptable publish shortcut.
- Fixed verification accounting so `missing_evidence` and `not_run` no longer count as a successful run.
- Stopped optimistic mutation evidence from treating arbitrary path mentions as changed files.

Remaining debt:

1. The lifecycle fallback currently mirrors only the hard P0 invariants. More Rego rules should gradually move from Python helper logic into policy modules.
2. Durable resume/replay still needs to restore lifecycle decisions from artifacts and continue from a safe stage.
3. The optional OPA invocation should be hardened for production deployment conventions, bundle loading, and policy test fixtures.


## Repair loop pass

Closed in this pass:

- Added typed `RepairRequest` and `RepairResult` contracts.
- Added lifecycle facts/policy for `publish_review`, `can_leave_publish`, and `can_repair`.
- Added `publish_review` and `repair` graph stages.
- Publisher no longer owns CI repair. It reports failed checks/blockers; lifecycle policy decides whether a bounded repair packet is allowed.
- Repair packets forbid commit, push, PR creation/update, publishing, waiting PR checks, and workflow decisions.
- After repair the graph returns to `execution_review`, so repaired code must pass lifecycle review before verification/acceptance and any subsequent publish.
- Added regression tests for failed PR checks -> repair -> review -> second publish, repair attempt limit, and publisher-repair policy violation.

Remaining debt:

1. Repair budget is currently a fixed controller value (`max_attempts=2`); future work can move this into typed task policy/config.
2. Publish-forbidden-action detection still has conservative text/evidence fallback; strict JSON publish evidence should eventually become mandatory for publication.
3. Durable resume/replay should restore mid-repair lifecycle state safely from artifacts.

## Stage prompt / contract hardening update

Closed in this pass:

- Added a centralized OpenHands stage contract renderer instead of relying on per-node prose to communicate boundaries.
- Every OpenHands packet now renders `Allowed actions`, `Forbidden actions`, `Stop conditions`, `Required outputs`, and explicit control-plane non-goals.
- Observe/research are read-only by contract and capability-filtered to read-only capabilities.
- Execute and repair explicitly forbid commit, push, PR creation, release, publish, tag, merge, and rebase actions.
- Verify/world-check explicitly forbids mutation, repair, publishing, git publication actions, and final acceptance decisions.
- Publish is limited to publication/check evidence and explicitly forbids source edits, CI repair, feature reimplementation, force push, tags, merge/rebase, and releases.
- OpenHands adapter validation now checks that compiled prompts contain the standard bounded-packet sections before dispatch.
- Added prompt/contract regression tests for observe, execute, verify, publish, repair, and mutating capability filtering.

Remaining debt:

1. Some narrative bodies still live in `graph/workflow.py`; they are now bounded by the centralized stage contract compiler, but should eventually move to dedicated packet builder modules.
2. OPA/Rego currently covers lifecycle gates, while prompt-contract validation is still Python-side. Future work can add policy tests for prompt/action matrices.
3. Strict JSON evidence emission should become mandatory for publish and repair once OpenHands reliably emits the schema.

## Pipeline-wide re-entry pass

Closed in this pass:

- Added typed `PipelineLoopDecision`, `PipelineLoopTriggerKind`, `PipelineReentryTarget`, and `PipelineLoopBudget` models.
- Added controller-owned `RuntimeKernel.evaluate_pipeline_reentry()` for verification, acceptance, and publish-review re-entry decisions.
- Added lifecycle/policy support for `can_reenter` with Rego and deterministic Python fallback.
- Added legal re-entry targets back to research, observe, context build, obligation discovery, and planning.
- Added global, per-trigger, and per-source-stage budgets so rediscovery cannot become an infinite loop.
- Expanded obligation discovery to docs, examples, CI/build, codegen/tooling, affected surfaces, adjacent components, discovered impacts, and work-surface completion.
- Folded discovered impacts into planning, verification, and acceptance obligations.
- Added regression coverage for verify->obligations re-entry, acceptance-contract broad obligations, and budget exhaustion.

Remaining debt:

1. Re-entry trigger detection is typed at the decision layer but still partially uses conservative text normalization from verification/publish summaries; strict structured trigger output from verification would reduce heuristics further.
2. Durable resume/replay should restore `PipelineLoopDecision` history and continue from the safe re-entry target.
3. Policy fixtures should grow from hard invariants into scenario-specific bundles for repo, infra, k8s, and network task families.

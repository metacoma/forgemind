from __future__ import annotations

from .common import *


class PublishingStageMixin:
    async def publish_node(self, state: WorkflowState) -> dict[str, Any]:
            services = self.services
            readiness_gate = self.readiness_gate
            readiness_gate.require(state, "publish", "task", "plan", "execution_result")
            task = Task.model_validate(state["task"])
            plan = ExecutionPlan.model_validate(state["plan"])
            execution = ExecutionResult.model_validate(state["execution_result"])
            await _emit(services, "stage_started", "publish", "Ensuring commit/push obligations are satisfied", task_id=task.id)
            prompt = (
                "You are performing repository completion steps after implementation.\n"
                "You are running inside a Docker container.\n"
                "Use the existing workspace, credentials, git remote configuration, and GitHub token or CLI authentication if available.\n"
                "Do not re-implement or repair the feature in publish; report failing checks as structured blockers for the controller repair loop.\n"
                "Repository completion is not finished until commit/push obligations are satisfied and, if a PR exists or is created, its checks are fully assessed.\n\n"
                f"Task: {task.description}\n\n"
                f"Require commit: {plan.require_commit}\n"
                f"Require push: {plan.require_push}\n"
                f"Execution summary: {execution.summary}\n\n"
                "Do the following as needed:\n"
                "- inspect git status, current branch, remote tracking branch, and whether a PR already exists for the branch\n"
                "- create a commit if required and changes are not committed\n"
                "- push the branch/changes if required and remote credentials allow it\n"
                "- if a PR exists already or is created/updated by this push, identify the PR number/URL\n"
                "- wait for all PR checks and GitHub Actions/jobs for the current PR head SHA to finish\n"
                "- if checks fail, report exact failing jobs/log pointers and blockers; do not patch or run a CI repair loop in publish\n"
                "- report exact commands, commit hashes, branch names, PR number/URL, check names and statuses, whether checks were waited to completion, and any remaining blockers\n"
                "- if no PR exists and none is needed, state that explicitly\n"
            )
            request = PublishRequest(
                execution_result_id=execution.id,
                task_id=task.id,
                prompt=prompt,
                require_commit=plan.require_commit,
                require_push=plan.require_push,
                artifact_ids=list(state.get("artifact_ids") or []),
                metadata={"mode": "repo_completion", "execution_environment": plan.execution_environment},
            )
            run = await services.openhands_adapter.publish(request)
            artifact_ids = list(state.get("artifact_ids") or [])
            artifact_ids.extend(artifact.id for artifact in run.artifacts)
            result = run
            await _emit(
                services,
                "stage_completed",
                "publish",
                "Publish obligations attempted",
                ok=result.ok,
                conversation_id=result.conversation_id,
                require_commit=plan.require_commit,
                require_push=plan.require_push,
                artifact_ids=[artifact.id for artifact in result.artifacts],
            )
            return {
                "publish_request": request.model_dump(mode="json"),
                "publish_result": result.model_dump(mode="json"),
                "artifact_ids": artifact_ids,
                "status": "published",
                "transitions": _append_transition(state, "publish", "published", "Repository publication obligations attempted", [artifact.id for artifact in result.artifacts]),
            }

    async def publish_review_node(self, state: WorkflowState) -> dict[str, Any]:
            services = self.services
            readiness_gate = self.readiness_gate
            readiness_gate.require(state, "publish_review", "task", "plan", "execution_result", "publish_result")
            task = Task.model_validate(state["task"])
            plan = ExecutionPlan.model_validate(state["plan"])
            execution = ExecutionResult.model_validate(state["execution_result"]) if state.get("execution_result") else None
            publish = PublishResult.model_validate(state["publish_result"])
            contract = TaskAcceptanceContract.model_validate(state["acceptance_contract"]) if state.get("acceptance_contract") else None
            repair_attempt_count = len(state.get("repair_results") or [])
            kernel = services.runtime_kernel or RuntimeKernel()
            await _emit(services, "stage_started", "publish_review", "Reviewing publish/check evidence through lifecycle policy", task_id=task.id, repair_attempt_count=repair_attempt_count)
            decision = kernel.review_publish(
                plan=plan,
                execution=execution,
                publish=publish,
                acceptance_contract=contract,
                repair_attempt_count=repair_attempt_count,
                max_repair_attempts=2,
            )
            loop_decision = kernel.evaluate_pipeline_reentry(
                source_stage="publish_review",
                plan=plan,
                obligations=ObligationAnalysis.model_validate(state["obligations"]) if state.get("obligations") else None,
                publish=publish,
                loop_decisions=_pipeline_loop_decisions(state),
            )
            loop_target = _reentry_target(loop_decision)
            selected_next = loop_target or decision.graph_next
            artifact = services.artifact_store.add_json("lifecycle_transition_decision", decision.model_dump(mode="json"), metadata={"task_id": task.id, "event": decision.event.value})
            update: dict[str, Any] = {
                "publish_review_decision": decision.model_dump(mode="json"),
                "pipeline_loop_decisions": _append_pipeline_loop_decision(state, loop_decision),
                "artifact_ids": _append_artifact_id(state.get("artifact_ids"), artifact.id),
                "status": "publish_reviewed",
                "lifecycle_decisions": _append_lifecycle_decision(state, decision),
                "transitions": _append_transition(state, "publish_review", "publish_reviewed", loop_decision.reason if loop_target else decision.reason, [artifact.id]),
                "controller_decisions": _append_controller_decision(state, kernel.controller_decision(stage="publish_review", selected_next_stage=selected_next, reason=loop_decision.reason if loop_target else decision.reason)),
            }
            if loop_target is not None:
                update.update(_clear_for_reentry(loop_target))
            if not decision.allowed and decision.graph_next == "finalize" and contract is not None:
                acceptance = kernel.acceptance_from_lifecycle_violation(contract=contract, execution=execution, decision=decision)
                acceptance_artifact = services.artifact_store.add_json("acceptance_decision", acceptance.model_dump(mode="json"), metadata={"task_id": task.id, "source": "publish_lifecycle_violation"})
                update["acceptance_decision"] = acceptance.model_dump(mode="json")
                update["artifact_ids"] = _append_artifact_id(update["artifact_ids"], acceptance_artifact.id)
            await _emit(services, "stage_completed", "publish_review", "Publish lifecycle transition reviewed", allowed=decision.allowed, next_stage=decision.graph_next, violations=[item.code for item in decision.violations])
            return update

    def publish_review_next(self, state: WorkflowState) -> str:
            services = self.services
            readiness_gate = self.readiness_gate
            loop_decisions = state.get("pipeline_loop_decisions") or []
            if loop_decisions:
                loop = PipelineLoopDecision.model_validate(loop_decisions[-1])
                if loop.source_stage == "publish_review":
                    target = _reentry_target(loop)
                    if target in {"research", "observe", "build_context", "obligations", "plan", "finalize"}:
                        return target
            decision = (state.get("publish_review_decision") or {})
            next_stage = str(decision.get("graph_next") or "verify") if isinstance(decision, dict) else "verify"
            return next_stage if next_stage in {"repair", "verify", "acceptance", "finalize"} else "verify"

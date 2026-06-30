package artifact_workflow_runtime.lifecycle

# Reference OPA/Rego policy for production deployments. The Python evaluator in
# lifecycle.policy is an explicit development fallback; production wiring should
# use opa_required mode and fail closed when OPA cannot answer.

default can_leave_execute := false

can_leave_execute if {
  not input.execution_stage_failed
  not input.execute_pr_created
  not input.execute_git_push
  not input.execute_git_commit
  not input.execute_forbidden_action_detected
}

default can_publish := false

can_publish if {
  input.execution_succeeded
  not input.environment_blocked
  not input.mandatory_verification_required
}

can_publish if {
  input.execution_succeeded
  not input.environment_blocked
  input.mandatory_verification_required
  input.mandatory_verification_satisfied
}

default can_finalize_success := false

can_finalize_success if {
  input.acceptance.accepted == true
}

default can_leave_publish := false

can_leave_publish if {
  not input.publish_stage_failed
  not input.publish_forbidden_action_detected
}

default can_repair := false

can_repair if {
  not input.environment_blocked
  input.repair_attempt_count < input.max_repair_attempts
  input.publish_failed_checks
}

can_repair if {
  not input.environment_blocked
  input.repair_attempt_count < input.max_repair_attempts
  input.publish_has_blockers
}


default can_reenter := false

can_reenter if {
  input.reentry_required
  input.reentry_target_stage != "continue"
  not input.reentry_budget_exhausted
}

can_reenter if {
  not input.reentry_required
}

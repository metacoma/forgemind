package artifact_workflow_runtime.lifecycle

# Reference OPA/Rego policy for production deployments. The Python fallback in
# lifecycle.policy mirrors these hard invariants when the opa binary is not
# installed, so the control plane is never silently permissive.

default can_leave_execute := false

can_leave_execute if {
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

# Reuse plan from donor archive

## A. Переносим почти как есть

1. **OpenHands API/client layer**
   - explicit-only payload builder
   - REST polling for start tasks
   - WebSocket event collection patterns
   - conversation metadata refresh logic

2. **Typed public contracts**
   - Pydantic base model patterns
   - conversation start / run result DTO style

3. **Tests and mocks**
   - fake OpenHands server
   - payload invariants tests
   - SDK-level integration patterns

## B. Переносим, но адаптируем

1. **conversation/session handling**
   - old meaning: role conversation lifecycle
   - new meaning: OpenHands execution session lifecycle

2. **result abstraction**
   - old meaning: role result / role summary
   - new meaning: observation result / execution result / verification result

3. **graph state patterns**
   - old meaning: role pipeline state
   - new meaning: workflow state with artifacts/context/policy

4. **report shaping**
   - old meaning: role reports
   - new meaning: neutral final workflow report

## C. Не переносим

- role catalog
- role policy
- team lead logic
- team lead prompts
- scout/architect/coder/qa/reviewer/publisher routing
- fixed role pipeline
- role-specific routing heuristics
- role summaries and role contracts as architectural primitives
- any abstraction whose primary unit is a role

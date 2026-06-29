# Architecture

## Core separation of concerns

### WorkflowController

Собирает зависимости runtime, запускает graph и возвращает `FinalReport`.

### Graph

Оркеструет только переходы состояния:

- intake
- classify
- observe?
- build_context
- plan
- policy
- approval?
- execute
- verify
- finalize

### Direct LLM backend

Получает **только текст**:

- task text
- context packet text
- artifact excerpts
- schema instructions

Он не знает про filesystem, shell, repo state, kubernetes, hosts и сеть напрямую.

### OpenHands adapter

Отвечает только за действия в мире:

- observation
- execution
- verification

Он не принимает routing decisions за workflow.

### Artifacts

Каждый значимый шаг фиксируется в виде файла и metadata-record:

- task intake
- observation evidence
- context packet
- policy decision
- execution evidence
- verification evidence
- final report

### ContextPacket

Это текстовая упаковка фактов, извлечённых из artifact layer. Только через неё Direct LLM получает world facts.

## State-first model

Graph state хранит сериализуемые сущности:

- task
- classification
- observation_request/result
- context_packet
- llm_requests/results
- execution_request/result
- policy_decision
- approval_request
- verification_result
- final_report
- artifact registry

Runtime objects не сериализуются в state; они передаются через service bundle.

## Policy and approval

Политика rule-based в MVP:

- read-only families → allow
- mutating repo/host/k8s/git actions → require approval
- approval denied → workflow stops with blocked final report

## Execution families

Система построена вокруг family/capability instead of roles:

- `documentation_only`
- `repository_change`
- `host_operation`
- `cluster_operation`
- `network_investigation`

## Why this is a new project

Новый runtime не наследует старый orchestration layer. Он берет только нейтральные, рабочие инженерные куски:

- OpenHands transport/client patterns
- typed contracts
- test harness patterns

Все role-bound abstractions исключены.

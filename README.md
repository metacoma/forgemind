# artifact-workflow-runtime

Новый standalone-проект для capability-driven orchestration без ролевой модели.

Базовая формула системы:

- **WorkflowController управляет**
- **LangGraph orchestrates**
- **Direct LLM думает только по тексту**
- **OpenHands наблюдает и исполняет**
- **Artifacts фиксируют истину**
- **ContextPacket переносит факты из мира в текст**
- **Policy/Approval ограничивают действия**

## Что это не делает

Этот проект не является рефакторингом старого репозитория. Он не сохраняет team lead/scout/architect/coder/reviewer/publisher модель и не использует role theater.

## Откуда взят код

Старый архив использован как **донор**:

- explicit-only OpenHands payload builder
- OpenHands REST/WebSocket patterns
- conversation/session contracts
- Pydantic public contracts
- fake OpenHands test harness

Весь role-centric слой отброшен.

## Структура

```text
src/artifact_workflow_runtime/
  controller/
  graph/
  llm_backend/
  openhands_adapter/
  context/
  observation/
  artifacts/
  policy/
  capabilities/
  families/
  models/
  reports/
```

## MVP-flow

1. intake task
2. classify through Direct LLM
3. if world facts are needed → OpenHands observation
4. build context packet
5. Direct LLM planning
6. policy check
7. approval if needed
8. OpenHands execution
9. ingest artifacts/evidence
10. verification
11. final report

## Установка

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test,langgraph]'
```

`langgraph` — основной orchestrator. Если пакет временно недоступен, в проекте есть маленький совместимый fallback для офлайн-тестов.

## CLI

```bash
artifact-workflow-run \
  --task "Работай с репозиторием metacoma/freeplane_plugin_grpc, склонируй его и внеси нужные изменения" \
  --direct-llm-endpoint http://localhost:4000/v1 \
  --direct-llm-model openai/reasoner \
  --openhands-endpoint http://localhost:3000 \
  --openhands-model openai/executor
```

Важно: runtime больше **не принимает** `--repository/--branch/--git-provider` как пользовательские флаги.
Источник истины для целевого репозитория, ветки, хоста или кластера — **текст задачи** и/или уже существующий sandbox OpenHands.

CLI печатает финальный JSON report и сохраняет artifacts в `./run-artifacts`.

## Документация

- [ARCHITECTURE.md](ARCHITECTURE.md)
- [REUSE_PLAN.md](REUSE_PLAN.md)

## OpenHands sandbox reuse

The CLI supports `--reuse` to search for an existing OpenHands sandbox already associated with the selected OpenHands model and reuse it for the workflow run. You can also pin a specific sandbox or conversation explicitly with `--sandbox-id` and `--conversation-id`.

Example:

```bash
artifact-workflow-run \
  --task "Inspect repo and fix failing tests in metacoma/freeplane_plugin_grpc" \
  --direct-llm-endpoint http://127.0.0.1:4000/v1 \
  --direct-llm-model openai/reasoner \
  --openhands-endpoint http://127.0.0.1:3000 \
  --openhands-model openai/executor \
  --reuse \
  --auto-approve
```

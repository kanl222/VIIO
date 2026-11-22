# Общий сервис Arize Phoenix (Shared Observability Platform)

Централизованный экземпляр **Arize Phoenix** для сбора трейсов, спанов, мониторинга задержек, оценки качества (evals) и работы с датасетами во всех проектах (`VIIO`, `ConsultantBot`, `AgentAI`, `local-rag`, и др.).

---

## 1. Запуск и остановка

```powershell
# Запуск из корня VIIO:
docker compose -f docker-compose.phoenix.yml up -d

# Или запуск из папки infra/phoenix:
cd infra/phoenix
docker compose up -d

# Просмотр логов:
docker logs shared_phoenix -f

# Остановка:
docker compose -f docker-compose.phoenix.yml down
```

---

## 2. Веб-интерфейс и порты

| Назначение | Адрес | Описание |
|---|---|---|
| **Web UI** | `http://localhost:6006` | Интерфейс трейсов, спанов, сессий и датасетов |
| **OTLP HTTP Collector** | `http://localhost:6006/v1/traces` | Приём спанов по протоколу OTLP HTTP |
| **OTLP gRPC Collector** | `http://localhost:4317` | Приём спанов по протоколу OTLP gRPC |

Все данные (база `phoenix.db`, датасеты, оценки) персистентно сохраняются в общем Docker томе `shared_phoenix_data`.

---

## 3. Подключение проектов к Phoenix

### Вариант А: Локальный Python-проект (на хосте)

В любом проекте (`.env` или переменные окружения):

```env
PHOENIX_COLLECTOR_ENDPOINT=http://localhost:6006/v1/traces
PHOENIX_PROJECT_NAME=my-new-project
```

В коде (LangChain / LangGraph):

```python
from phoenix.otel import register
from openinference.instrumentation.langchain import LangChainInstrumentor

tracer_provider = register(
    project_name="my-new-project",
    endpoint="http://localhost:6006/v1/traces",
    batch=True,
    verbose=False,
)
LangChainInstrumentor().instrument(tracer_provider=tracer_provider)
```

---

### Вариант Б: Контейнеризованный проект (Docker Compose)

В `docker-compose.yml` вашего проекта добавьте внешнюю сеть `phoenix_net`:

```yaml
services:
  my_agent:
    image: my-agent:latest
    environment:
      - PHOENIX_COLLECTOR_ENDPOINT=http://phoenix:6006/v1/traces
      - PHOENIX_PROJECT_NAME=my-agent
    networks:
      - default
      - phoenix_net

networks:
  phoenix_net:
    name: phoenix_net
    external: true
```

*Примечание:* Если контейнер не подключён к `phoenix_net`, он может отправлять трейсы на хост через `http://host.docker.internal:6006/v1/traces` (с флагом `extra_hosts: ["host.docker.internal:host-gateway"]`).

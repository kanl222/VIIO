# APIAgent: LangGraph RAG Agent REST API

Масштабируемый REST API микросервис на FastAPI для диалогового AI-агента на базе LangGraph с поддержкой сессионной памяти, векторного хранилища Qdrant, локальных моделей Ollama и развертыванием в Docker.

---

## Архитектура и принципы разработки

Сервис построен в строгом соответствии с принципами **SOLID**:
- **`src/apiagent/interfaces/`** — `Protocol`-абстракции (`IVectorStore`, `IDocumentService`, `IAgentService`, `DocumentMetadata`) для инверсии зависимостей (DIP) и разделения интерфейсов (ISP).
- **`src/apiagent/services/`** — бизнес-логика:
  - `DocumentService` — валидация, нарезка текста на чанки (`RecursiveCharacterTextSplitter`) и индексация.
  - `graph_builder.py` — компиляция графа состояний LangGraph (`StateGraph`) с поддержкой инструментов и `MemorySaver` checkpointer.
  - `agent_service.py` — оркестрация сессий диалога и асинхронное взаимодействие с агентом.
- **`src/apiagent/tools/`** — инструменты LangChain (`search_knowledge_base`) для обращения к базе знаний.
- **`src/apiagent/utils/`** — инфраструктурные адаптеры (`QdrantVectorStoreAdapter`) с автосозданием коллекции (cold start).
- **`src/apiagent/router.py`** — тонкий HTTP-роутер с Pydantic DTO и обработкой ошибок.
- **`src/apiagent/__init__.py`** — Composition Root (сборка DI-графа и фабрика `create_app`).
- **`src/apiagent/runner.py`** — запуск ASGI-сервера Uvicorn.

---

## Требования

- Python `>= 3.12`
- Менеджер пакетов [uv](https://docs.astral.sh/uv/)
- Запущенный локальный экземпляр [Ollama](https://ollama.com/) с загруженными моделями:
  ```bash
  ollama pull llama3.1
  ollama pull nomic-embed-text
  ```
- Векторная база данных Qdrant (локально или через Docker)

---

## Локальный запуск

1. **Клонирование репозитория и переход в директорию проекта:**
   ```bash
   cd APIAgent
   ```

2. **Создание файла переменных окружения:**
   ```bash
   cp .env.example .env
   ```

3. **Установка зависимостей с помощью `uv`:**
   ```bash
   uv sync
   ```

4. **Запуск сервиса:**
   ```bash
   uv run apiagent
   ```
   Сервер запустится по адресу: `http://localhost:8000`. Интерактивная документация Swagger доступна по адресу `http://localhost:8000/docs`.

---

## Запуск в Docker Compose

Для запуска сервиса вместе с Qdrant выполните:

```bash
docker compose up -d --build
```

- Сервис Qdrant доступен на порту `6333`
- Сервис APIAgent доступен на порту `8000`
- Подключение к хостовой Ollama настроено через `host.docker.internal:11434`

Остановка контейнеров:
```bash
docker compose down
```

---

## Спецификация API

### 1. Проверка состояния: `GET /health`

**Запрос:**
```bash
curl -X GET http://localhost:8000/health
```

**Ответ (200 OK):**
```json
{
  "status": "healthy",
  "model": "llama3.1",
  "documents_count": 12
}
```

---

### 2. Добавление документа в базу знаний: `POST /api/v1/documents`

**Запрос:**
```bash
curl -X POST http://localhost:8000/api/v1/documents \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Регламент возврата товара",
    "content": "Возврат товара надлежащего качества возможен в течение 14 дней с момента покупки при сохранении товарного вида и чека.",
    "metadata": {
      "source": "wiki",
      "department": "support"
    }
  }'
```

**Ответ (201 Created):**
```json
{
  "document_id": "doc-a1b2c3d4",
  "title": "Регламент возврата товара",
  "chunks_count": 1,
  "status": "indexed"
}
```

---

### 3. Диалог с агентом: `POST /api/v1/chat`

**Запрос (первое сообщение в новой сессии):**
```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "В течение какого срока я могу вернуть товар?"
  }'
```

**Ответ (200 OK):**
```json
{
  "reply": "Согласно регламенту возврата, вы можете вернуть товар надлежащего качества в течение 14 дней с момента покупки.",
  "thread_id": "9f3c1b64-5820-410e-8ea2-36c927f6be32"
}
```

**Последующий запрос с сохранением контекста диалога (`thread_id`):**
```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "А что нужно сохранить для возврата?",
    "thread_id": "9f3c1b64-5820-410e-8ea2-36c927f6be32"
  }'
```

---

## Тестирование

Запуск полного набора юнит- и интеграционных тестов:

```bash
uv run pytest -v
```

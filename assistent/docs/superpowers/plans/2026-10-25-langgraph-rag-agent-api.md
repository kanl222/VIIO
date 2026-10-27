# LangGraph RAG Agent API с Qdrant и Docker: План реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Создать масштабируемый REST API микросервис на FastAPI для AI-агента на базе LangGraph с поддержкой памяти диалогов, векторного хранилища Qdrant, локальной Ollama и развертыванием в Docker.

**Architecture:** SOLID-микросервис с разделением на протоколы (`interfaces/`), бизнес-логику (`services/`), инструменты агента (`tools/`), векторный адаптер (`utils/vector_store.py`), тонкий роутер (`router.py`) и composition root в `__init__.py`.

**Tech Stack:** Python 3.12, uv, LangChain, LangGraph, FastAPI, Uvicorn, Pydantic Settings, Qdrant Client (`qdrant-client`, `langchain-qdrant`), Docker & Docker Compose.

**Spec:** `docs/superpowers/specs/2026-10-25-langgraph-rag-agent-api-design.md`

## Global Constraints

- Версия Python: `>=3.12`
- Управление зависимостями: `uv` (`uv add`, `uv sync`)
- Все методы, классы и модули должны содержать Google-style docstrings на русском языке
- Принципы SOLID: сервисы зависят от `typing.Protocol` из `interfaces/`, зависимости инжектируются через конструктор
- `__init__.py` выступает единственным местом создания экземпляров конкретных классов (composition root)

## Review Focus

1. **Недоступность сервиса Qdrant / Ollama:** Корректный fallback или информативное сообщение об ошибке (HTTP 503/500) вместо падения приложения.
2. **Сессии и память `thread_id`:** Проверка изоляции контекстов — сообщения пользователя в одной сессии не должны влиять на другую сессию.
3. **Холодный старт коллекции Qdrant:** Автоматическое создание коллекции векторов при ее отсутствии при первой индексации или поиске.
4. **Формат ответа инструмента `search_knowledge_base`:** Инструмент должен возвращать читаемый текст или сообщение "Ничего не найдено", чтобы LLM могла адекватно сформулировать ответ.
5. **Работа в Docker:** Корректная адресация сервисов в `docker-compose` (`http://qdrant:6333` и `http://host.docker.internal:11434` для доступа к хостовой Ollama).

---

### Task 1: Конфигурация и зависимости проекта

**Files:**
- Modify: `pyproject.toml`
- Create: `src/assistent/config.py`
- Create: `tests/test_config.py`

**Interfaces:**
- Produces: `Settings` класс из `src/assistent/config.py` с полями:
  - `ollama_base_url: str` (default: "http://localhost:11434")
  - `ollama_model: str` (default: "llama3.1")
  - `ollama_embedding_model: str` (default: "nomic-embed-text")
  - `qdrant_url: str` (default: "http://localhost:6333")
  - `qdrant_collection_name: str` (default: "knowledge_base")
  - `api_host: str` (default: "0.0.0.0")
  - `api_port: int` (default: 8000)

- [ ] **Step 1: Установить необходимые зависимости проекта с помощью `uv add`**
Добавить `fastapi`, `uvicorn[standard]`, `pydantic-settings`, `langchain-ollama`, `qdrant-client`, `langchain-qdrant`, `pytest`, `httpx`.

- [ ] **Step 2: Написать падающий тест для `Settings` в `tests/test_config.py`**
Проверить считывание дефолтных значений и переопределение через переменные окружения.

- [ ] **Step 3: Запустить тест и убедиться, что он падает**
Команда: `uv run pytest tests/test_config.py`

- [ ] **Step 4: Реализовать `Settings` в `src/assistent/config.py`**
Реализовать класс на базе `pydantic_settings.BaseSettings` с валидацией и docstrings на русском языке.

- [ ] **Step 5: Запустить тест и убедиться, что он проходит**
Команда: `uv run pytest tests/test_config.py`

---

### Task 2: Протоколы, Qdrant векторное хранилище и сервис документов

**Files:**
- Create: `src/assistent/interfaces/__init__.py`
- Create: `src/assistent/interfaces/_protocols.py`
- Create: `src/assistent/utils/__init__.py`
- Create: `src/assistent/utils/vector_store.py`
- Create: `src/assistent/services/__init__.py`
- Create: `src/assistent/services/document_service.py`
- Create: `tests/test_document_service.py`

**Interfaces:**
- Produces:
  - `interfaces/_protocols.py`: `IVectorStore(Protocol)`, `IDocumentService(Protocol)`, `IAgentService(Protocol)`
  - `utils/vector_store.py`: `QdrantVectorStoreAdapter` реализующий `IVectorStore` (поиск `similarity_search`, индексация `add_texts`)
  - `services/document_service.py`: `DocumentService` (метод `index_document(title: str, content: str, metadata: dict) -> DocumentMetadata`)

- [ ] **Step 1: Написать падающий тест для `DocumentService` и mock векторного хранилища в `tests/test_document_service.py`**
Проверить нарезку текста на чанки (RecursiveCharacterTextSplitter) и вызов добавления чанков в векторное хранилище.

- [ ] **Step 2: Запустить тест и убедиться, что он падает**
Команда: `uv run pytest tests/test_document_service.py`

- [ ] **Step 3: Реализовать протоколы в `interfaces/_protocols.py` и re-export в `interfaces/__init__.py`**
Описать контракты интерфейсов `IVectorStore`, `IDocumentService`, `IAgentService`.

- [ ] **Step 4: Реализовать `QdrantVectorStoreAdapter` в `src/assistent/utils/vector_store.py`**
Создание клиента Qdrant, инициализация коллекции при необходимости, методы `add_texts` и `similarity_search`.

- [ ] **Step 5: Реализовать `DocumentService` в `src/assistent/services/document_service.py`**
Инжектировать `IVectorStore` через конструктор, нарезать текст на чанки и передавать в хранилище.

- [ ] **Step 6: Запустить тест и проверить прохождение**
Команда: `uv run pytest tests/test_document_service.py`

---

### Task 3: Инструмент поиска, сборка LangGraph и AgentService

**Files:**
- Create: `src/assistent/tools/__init__.py`
- Create: `src/assistent/tools/search_tool.py`
- Create: `src/assistent/services/graph_builder.py`
- Create: `src/assistent/services/agent_service.py`
- Create: `tests/test_agent_service.py`

**Interfaces:**
- Consumes: `IVectorStore` из `interfaces/_protocols.py`
- Produces:
  - `tools/search_tool.py`: `create_knowledge_search_tool(vector_store: IVectorStore)`
  - `services/graph_builder.py`: `build_agent_graph(llm, tools, checkpointer)` -> `CompiledStateGraph`
  - `services/agent_service.py`: `AgentService(graph)` с асинхронным методом `chat(message: str, thread_id: str | None) -> tuple[str, str]`

- [ ] **Step 1: Написать падающий тест для `AgentService` в `tests/test_agent_service.py`**
Тест с использованием тестовой модели или мока графа для проверки передачи `thread_id` и возврата ответа.

- [ ] **Step 2: Запустить тест и убедиться, что он падает**
Команда: `uv run pytest tests/test_agent_service.py`

- [ ] **Step 3: Реализовать `create_knowledge_search_tool` в `src/assistent/tools/search_tool.py`**
Функция создает LangChain `@tool` с docstring для LLM, форматируя найденные релевантные чанки в строковый ответ.

- [ ] **Step 4: Реализовать `build_agent_graph` в `src/assistent/services/graph_builder.py`**
Использовать `create_react_agent` из `langgraph.prebuilt` (или `StateGraph` с `ToolNode`) с `MemorySaver` в качестве checkpointer для изоляции сессий по `thread_id`.

- [ ] **Step 5: Реализовать `AgentService` в `src/assistent/services/agent_service.py`**
Асинхронный метод `chat(message: str, thread_id: str | None) -> tuple[str, str]`, обрабатывающий входящее сообщение пользователя, генерирующий `uuid` при отсутствии `thread_id` и извлекающий финальный ответ агента.

- [ ] **Step 6: Запустить тест и проверить успешное прохождение**
Команда: `uv run pytest tests/test_agent_service.py`

---

### Task 4: FastAPI Router, Composition Root и Runner

**Files:**
- Create: `src/assistent/router.py`
- Create: `src/assistent/runner.py`
- Modify: `src/assistent/__init__.py`
- Create: `tests/test_api.py`

**Interfaces:**
- Consumes: `Settings`, `IAgentService`, `IDocumentService`, `IVectorStore`
- Produces:
  - `router.py`: `create_api_router(agent_service, document_service, settings)`
  - `__init__.py`: `create_app() -> FastAPI`, `main() -> None`
  - `runner.py`: `run_app()`

- [ ] **Step 1: Написать падающий интеграционный тест API в `tests/test_api.py`**
Тесты эндпоинтов `GET /health`, `POST /api/v1/documents`, `POST /api/v1/chat` через `TestClient(app)`.

- [ ] **Step 2: Запустить тест и убедиться, что он падает**
Команда: `uv run pytest tests/test_api.py`

- [ ] **Step 3: Реализовать `router.py` с Pydantic DTO схемами и эндпоинтами**
Эндпоинты `/health`, `/api/v1/documents`, `/api/v1/chat` с полной типизацией и обработкой исключений.

- [ ] **Step 4: Реализовать Composition Root в `src/assistent/__init__.py`**
Функция `create_app()`: инициализация `Settings`, создание `QdrantVectorStoreAdapter`, `DocumentService`, `create_knowledge_search_tool`, сборка графа `build_agent_graph`, инициализация `AgentService`, монтирование роутера в FastAPI.

- [ ] **Step 5: Реализовать `runner.py` для запуска uvicorn**

- [ ] **Step 6: Запустить тесты API и проверить успешное прохождение**
Команда: `uv run pytest tests/test_api.py`

---

### Task 5: Dockerfile, docker-compose.yml и окружение

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `.dockerignore`
- Create: `.env.example`
- Modify: `README.md`

**Interfaces:**
- Docker Compose сервисы:
  - `qdrant`: порт `6333:6333`, volume `qdrant_storage:/qdrant/storage`
  - `assistent`: сборка из `Dockerfile`, порты `8000:8000`, env vars для `QDRANT_URL=http://qdrant:6333` и `OLLAMA_BASE_URL=http://host.docker.internal:11434`

- [ ] **Step 1: Создать `.dockerignore`**
Исключить `.git`, `.venv`, `__pycache__`, `.pytest_cache`, тома данных.

- [ ] **Step 2: Создать `Dockerfile`**
Multi-stage сборка на базе `python:3.12-slim` с `uv`, копированием `pyproject.toml`, установкой зависимостей и запуском `uvicorn assistent:create_app --factory --host 0.0.0.0 --port 8000`.

- [ ] **Step 3: Создать `docker-compose.yml`**
Определить сервисы `qdrant` и `assistent` с общей docker-сетью, volumes и healthchecks.

- [ ] **Step 4: Создать `.env.example` и обновить `README.md`**
Документировать запуск через `uv run`, запуск через Docker, примеры curl-запросов к `/api/v1/chat` и `/api/v1/documents`.

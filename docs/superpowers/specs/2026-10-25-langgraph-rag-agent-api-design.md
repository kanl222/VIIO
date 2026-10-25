# Спецификация дизайна: LangGraph RAG Agent API

- **Дата:** 2026-10-25
- **Статус:** Черновик на утверждении
- **Цель:** Создать легковесный, расширяемый REST API микросервис на FastAPI для AI-агента на базе LangGraph с поддержкой локальной модели Ollama, RAG-поиска по документам и сохранения истории диалогов.

---

## 1. Контекст и цели

### 1.1. Бизнес-цель
Предоставить готовый к использованию HTTP API сервис, позволяющий:
1. Вести диалог с AI-агентом с контекстной памятью в рамках отдельных сессий (`thread_id`).
2. Добавлять локальные текстовые документы/знания в базу данных для семантического поиска.
3. Использовать возможности LangGraph (ReAct-цикл принятия решений агентом для вызова инструмента поиска знаний).
4. Работать полностью локально без внешних платных API через Ollama.

### 1.2. Нефункциональные требования
- **SOLID / Чистая архитектура:** Строгое разделение ответственности по правилам микросервисов Python (`interfaces/`, `services/`, `tools/`, `utils/`, composition root в `__init__.py`).
- **Dependency Inversion:** Зависимости передаются через конструкторы, модули бизнес-логики опираются на `typing.Protocol`.
- **Документирование:** Все модули, классы и функции документируются на русском языке с использованием Google-style docstrings.
- **Python 3.12+** с управлением зависимостями через `uv`.

---

## 2. Архитектура системы

### 2.1. Структура проекта
```
src/apiagent/
├── __init__.py             # Composition root: сборка DI-графа, фабрика FastAPI, точка входа main()
├── config.py               # Настройки (pydantic-settings: URL Ollama, имя модели, хост/порт API)
├── router.py               # HTTP-адаптер: эндпоинты FastAPI (/api/v1/chat, /api/v1/documents, /health)
├── runner.py               # Оркестратор запуска uvicorn и graceful shutdown
│
├── interfaces/             # Protocol-абстракции (ISP + DIP)
│   ├── __init__.py         # Re-export интерфейсов
│   └── _protocols.py       # IAgentService, IDocumentStore, IVectorStore
│
├── services/               # Бизнес-логика (SRP)
│   ├── __init__.py         # Re-export сервисов
│   ├── agent_service.py    # Управление вызовами LangGraph и сессиями пользователей
│   ├── document_service.py # Загрузка, чанкинг и индексация документов
│   └── graph_builder.py    # Построение и компиляция StateGraph агента с checkpointer
│
├── tools/                  # Инструменты для агента
│   ├── __init__.py
│   └── search_tool.py      # LangChain tool `search_knowledge_base`
│
└── utils/                  # Инфраструктурные помощники
    ├── __init__.py
    └── vector_store.py     # Интеграция с векторным хранилищем Qdrant (локальный/docker клиент)
```

### 2.2. Компоненты и ответственность

| Компонент | Роль | Вход | Выход |
|---|---|---|---|
| `Settings` (`config.py`) | Конфигурация из переменных окружения | `.env` / env vars | Объект конфигурации |
| `router.py` | Валидация HTTP запросов и маппинг на сервисы | Pydantic Request DTO | Pydantic Response DTO |
| `AgentService` | Оркестрация диалога с агентом через LangGraph | `message: str, thread_id: str` | `AgentResponse(reply, thread_id)` |
| `GraphBuilder` | Сборка графа состояний LangGraph | `ChatOllama`, `Tools`, `Checkpointer` | Скомпилированный `CompiledStateGraph` |
| `DocumentService` | Обработка и сохранение документов | `text: str, metadata: dict` | `DocumentMetadata` |
| `VectorStore` | Семантический поиск эмбеддингов | Текст запроса | Список релевантных фрагментов |
| `SearchTool` | Обертка инструмента поиска для модели | `query: str` | Текстовый результат для модели |

---

## 3. Граф агента (LangGraph)

### 3.1. Схема состояний (State)
Используется стандартное состояние `MessagesState`:
```python
class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
```

### 3.2. Логика графа (ReAct loop)
```
          [ Входящее сообщение ]
                    │
                    ▼
           ┌─────────────────┐
           │   agent_node    │ ◄───┐
           │ (LLM reasoning) │     │
           └────────┬────────┘     │
                    │              │
      Нужен tool?   │              │
      ┌─────────────┴────────────┐ │
  Да  │                          │ Нет (ответ сформирован)
      ▼                          ▼
┌───────────┐                 [ Конец ]
│   tools   │ ───────────────────┘
│ (search)  │
└───────────┘
```
- **Checkpointer:** `MemorySaver` из `langgraph.checkpoint.memory` сохраняет историю диалогов по `thread_id`.
- **Инструмент:** `@tool("search_knowledge_base")` выполняет семантический поиск по загруженным документам.

---

## 4. Спецификация API (FastAPI)

### 4.1. `POST /api/v1/chat`
Отправка сообщения агенту.

**Запрос:**
```json
{
  "message": "Что написано в регламенте о возвратах?",
  "thread_id": "optional-uuid-string"
}
```
*Если `thread_id` не передан, сервер генерирует новый UUID.*

**Успешный ответ (200 OK):**
```json
{
  "reply": "Согласно регламенту, возврат осуществляется в течение 14 дней...",
  "thread_id": "b3f07a1e-8e6d-4c3a-9214-e0b67bf91d34"
}
```

### 4.2. `POST /api/v1/documents`
Добавление документа в базу знаний.

**Запрос:**
```json
{
  "title": "Регламент возврата",
  "content": "Полный текст документа регламента...",
  "metadata": {
    "source": "wiki",
    "author": "support"
  }
}
```

**Успешный ответ (201 Created):**
```json
{
  "document_id": "doc-a1b2c3d4",
  "title": "Регламент возврата",
  "chunks_count": 3,
  "status": "indexed"
}
```

### 4.3. `GET /health`
Проверка состояния сервиса.

**Успешный ответ (200 OK):**
```json
{
  "status": "healthy",
  "model": "llama3.1",
  "documents_count": 5
}
```

---

## 5. Зависимости проекта

Для реализации потребуются следующие библиотеки (устанавливаются через `uv add`):
1. `fastapi` — современный асинхронный веб-фреймворк.
2. `uvicorn[standard]` — ASGI сервер.
3. `pydantic-settings` — типобезопасная конфигурация приложения.
4. `langchain-ollama` — интеграция LangChain с локальной Ollama (ChatOllama + OllamaEmbeddings).
5. `qdrant-client` — клиент для работы с векторной базой Qdrant (поддержка in-memory/on-disk или сетевого Qdrant).
6. `langchain-qdrant` — интеграция векторного хранилища Qdrant с LangChain.
7. `pytest` и `httpx` — для тестирования эндпоинтов и логики.

---

## 6. Docker и контейнеризация

### 6.1. `Dockerfile`
- Многоэтапная сборка (multi-stage) на базе `python:3.12-slim` с `uv` для легковесного образа.
- Запуск приложения через Uvicorn на порту 8000.

### 6.2. `docker-compose.yml`
- Сервис `qdrant`: официальный образ `qdrant/qdrant:latest`, проброс портов `6333:6333` и монтирование тома для персистентности векторов.
- Сервис `apiagent`: сборка из локального `Dockerfile`, подключение к `qdrant` по внутренней сети `http://qdrant:6333`, настройка переменных окружения для подключения к Ollama (например, `http://host.docker.internal:11434`).

---

## 7. План тестирования

1. **Юнит-тесты (`tests/test_services.py`):**
   - Проверка `DocumentService` (разбиение на чанки, корректное сохранение).
   - Проверка сборки графа `GraphBuilder` с фиктивной моделью (`FakeListChatModel`).
   - Проверка `AgentService` и сохранения истории по `thread_id`.
2. **Интеграционные тесты API (`tests/test_api.py`):**
   - Проверка эндпоинта `/health`.
   - Проверка эндпоинта `/api/v1/documents` (успешное добавление).
   - Проверка эндпоинта `/api/v1/chat` через FastAPI `TestClient`.

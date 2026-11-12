# Скиллы агентов (Agent Skills) в платформе VIIO

В проекте VIIO используется открытая спецификация **Agent Skills** (пакеты знаний и инженерных методологий для AI-ассистентов, таких как Google Antigravity, Claude Code, GitHub Copilot). Скиллы хранятся непосредственно в репозитории проекта и стандартизируют правила архитектуры, написания кода, оптимизации баз данных, настройки RAG и проектирования системных промптов.

---

## 1. Архитектура и хранение скиллов

- **Каталог скиллов проекта**: `.agents/skills/<skill-name>/`
- **Файл фиксации версий**: `skills-lock.json` (содержит источник GitHub, путь к манифесту `SKILL.md` и вычисленный SHA-256 хеш каждого скилла для обеспечения воспроизводимости)
- **Управление и реестр**: Skills CLI (`npx skills`) и официальный каталог [skills.sh](https://skills.sh/)
- **Формат**: каждый скилл содержит обязательный файл `SKILL.md` с YAML frontmatter (`name`, `description`), структурированными инструкциями, шаблонами и ссылками на вложенные справочники (`references/`, `assets/`, `scripts/`).

---

## 2. Реестр установленных скиллов проекта

На данный момент в проекте зафиксировано и используется **19 скиллов** (18 в `skills-lock.json` + мета-скилл `find-skills`):

| Скилл | Источник | Назначение | Применение в кодовой базе VIIO |
| --- | --- | --- | --- |
| `ai-prompt-engineering-safety-review` | `github/awesome-copilot` | Аудит безопасности промптов, защита от injection и утечек | Проверка системных промптов `Assistent` и изоляции пользовательского ввода |
| `docker-compose-patterns` | `docker/skills` | Паттерны Docker Compose: healthchecks, порядок запуска, volume и сети | Корневой `compose.yaml`, `docker-compose.yml`, профили `pdf`, `postgres`, `diagnostics`, проверки `condition: service_healthy` |
| `enhance-prompt` | `google-labs-code/stitch-skills` | Быстрое улучшение формулировок и форматирование промптов | Доработка инструкций диалога и ролевых шаблонов |
| `evaluate-rag` | `hamelsmu/evals-skills` | Оценка качества RAG (RAG Triad: context precision/recall, faithfulness) | Автоматизированная оценка качества поиска и генерации ответов |
| `fastapi` | `fastapi/fastapi` | Лучшие практики FastAPI: Pydantic v2, DI `Depends`, lifespan, SSE | `Assistent/src/assistent/router.py`, `RAG/src/viio_rag/app.py`, сервисы `Documents` и `Consultant` |
| `finalize-agent-prompt` | `github/awesome-copilot` | Финализация и полировка промптов от лица AI-агента | Оптимизация ролевых инструкций в `Assistent/src/assistent/prompts.py` |
| `find-skills` | Meta-skill | Поиск, аудит и установка скиллов из экосистемы `skills.sh` | Инструмент обнаружения и обновления скиллов разработчиками и агентом |
| `langchain-rag` | `langchain-ai/langchain-skills` | Архитектура RAG: chunking, embeddings, vector stores | `Documents/src/viio_documents/indexer.py`, `RAG/src/viio_rag/retrieval.py`, `Assistent/src/assistent/tools/knowledge_base.py` |
| `langgraph-fundamentals` | `langchain-ai/langchain-skills` | Базовые сущности LangGraph: StateGraph, схемы состояния, узлы и ребра | `Assistent/src/assistent/services/supervisor_graph.py`, `database_subgraph.py`, `capabilities.py` |
| `langgraph-human-in-the-loop` | `langchain-ai/langchain-skills` | Механизмы `interrupt()`, валидация и подтверждение действий | Защитные барьеры при работе с SQL-запросами и потенциально деструктивными операциями |
| `langgraph-persistence` | `langchain-ai/langchain-skills` | Сохранение состояния графа, треды, чекпоинтеры | `Assistent/src/assistent/utils/postgres_saver.py`, персистентность диалогов в `agent_db` через `AsyncPostgresSaver` |
| `mcp-builder` | `anthropics/skills` | Разработка и интеграция серверов Model Context Protocol | `postgres-tool` (Postgres MCP Pro), расширение агентов внешними инструментами |
| `postgres-patterns` | `affaan-m/ecc` | Оптимизация схем PostgreSQL, индексы B-Tree/GIN, безопасность | `init-db/03-optimize-indexes.sql`, оптимизация запросов ACL документов и сессий пользователей |
| `prompt-engineering-patterns` | `wshobson/agents` | XML-структурирование промптов, CoT, few-shot, защита от инъекций | Системные промпты `Assistent/src/assistent/prompts.py`, `capabilities.py`, `corrective_rag.py` |
| `prompt-optimizer` | `affaan-m/ECC` | Интеллектуальный аудит и контекстная оптимизация промптов | Автоматический рефакторинг и доработка промптов агентов под стек VIIO |
| `qdrant-search-quality` | `qdrant/skills` | Диагностика и оптимизация поиска Qdrant: hybrid prefetch, RRF | `RAG/src/viio_rag/retrieval.py` (dense + sparse BM25 payload prefetch с проверкой прав доступа) |
| `rag-blueprint` | `NVIDIA/skills` | Инженерные шаблоны RAG enterprise-уровня и траблшутинг | Архитектура и надежность контейнеров и пайплайнов поиска под нагрузкой |
| `rag-implementation` | `wshobson/agents` | Комплексные шаблоны RAG: HyDE, reranking, parent doc, фильтрация | Модернизация конвейера `RAG` и гибридного поиска с LangGraph |
| `vercel-react-best-practices` | `vercel-labs/agent-skills` | Производительность React 18 / Next.js: мемоизация, борьба с лишними ререндерами | Компоненты веб-интерфейса `WebChat/src/` (`App.tsx`, `ChatMessage.tsx`, `ChatInput.tsx`) и `Diagnostics/src/` |

---

## 3. Детальный разбор использования скиллов по подсистемам

### 3.1. Оркестрация и агентный граф (LangGraph)
- **Используемые скиллы**: `langgraph-fundamentals`, `langgraph-persistence`, `langgraph-human-in-the-loop`.
- **Фактическая реализация**:
  - Граф диалога построен в `supervisor_graph.py` на базе `StateGraph`. Агент супервизора определяет намерения пользователя и выполняет маршрутизацию между вызовом RAG, субграфом coder и базой данных.
  - Состояние диалога непрерывно сохраняется в PostgreSQL (`agent_db`) благодаря адаптеру `AsyncPostgresSaver`, реализующему паттерны из `langgraph-persistence`. Это обеспечивает изолированные треды сессий и защиту от потери контекста при перезапусках.
  - В `database_subgraph.py` применены шаблоны проверки допустимости операций (валидация `SELECT`-only, изоляция транзакций).

### 3.2. База знаний, RAG и поисковый движок
- **Используемые скиллы**: `langchain-rag`, `qdrant-search-quality`, `rag-implementation`, `evaluate-rag`, `rag-blueprint`.
- **Фактическая реализация**:
  - `Documents/src/viio_documents/indexer.py` использует рекурсивное разбиение текстов (`RecursiveCharacterTextSplitter`) с настраиваемыми размерами чанков и перекрытий (`RAG_CHUNK_SIZE`, `RAG_CHUNK_OVERLAP`).
  - В сервисе `RAG` (`viio_rag/retrieval.py`) внедрены рекомендации из `qdrant-search-quality`: используется гибридная схема поиска (dense векторный поиск + payload prefetch с текстовыми фильтрами), строгая фильтрация по ACL (`allowed_roles`, `allowed_departments`, `is_public`) на уровне ядра Qdrant до финального ранжирования.
  - Скилл `rag-implementation` задает шаблоны интеграции с многоступенчатым пайплайном (HyDE, parent document retrieval, cross-encoder reranking).
  - Скилл `evaluate-rag` обеспечивает методику независимой оценки качества извлечения (Context Precision/Recall) и верности генерации ответов без галлюцинаций.
  - Скилл `rag-blueprint` регламентирует устойчивость контейнеров RAG при масштабировании нагрузки.

### 3.3. Системные промпты и ролевые модели
- **Используемые скиллы**: `prompt-engineering-patterns`, `finalize-agent-prompt`, `prompt-optimizer`, `ai-prompt-engineering-safety-review`, `enhance-prompt`.
- **Фактическая реализация**:
  - В системных промптах в `Assistent/src/assistent/prompts.py` внедрены стандарты структурирования:
    - Использование XML-тегов: `<role>`, `<context>`, `<rules>`, `<instructions>`, `<examples>`, `<constraints>`.
    - Инструкции защиты от инъекций (`ai-prompt-engineering-safety-review`): строгий запрет на выполнение инструкций из текстов пользовательских документов.
    - Внедрение Chain-of-Thought (CoT) и few-shot шаблонов для корректной маршрутизации между консультантом, программистом и базой данных.
  - `prompt-optimizer` и `enhance-prompt` применяются для циклической полировки промптов при добавлении новых возможностей агентов.

### 3.4. Базы данных и кэширование
- **Используемые скиллы**: `postgres-patterns`.
- **Фактическая реализация**:
  - В файле `init-db/03-optimize-indexes.sql` применены рекомендации по композитным B-Tree и GIN индексам:
    - Индексация полей метаданных документов для ускорения поиска прав доступа.
    - Индексация сессий и пользователей в `user_db` для устранения узких мест при авторизации.

### 3.5. Веб-интерфейс и диагностика
- **Используемые скиллы**: `vercel-react-best-practices`.
- **Фактическая реализация**:
  - Оптимизация `WebChat/src/App.tsx`, `ChatMessage.tsx`, `ChatInput.tsx`:
    - Использование функциональных обновлений состояния для избежания устаревших замыканий.
    - Мемоизация тяжелых блоков рендеринга сообщений и блоков подсветки синтаксиса.
    - Вынесение транзиентного состояния ввода за пределы глобального цикла рендеринга диалога.
  - Оптимизация `Diagnostics/src/App.tsx`: декомпозиция на хуки и компоненты, устранение избыточных ререндеров при отслеживании шагов графа в реальном времени.

### 3.6. Инфраструктура и микросервисы
- **Используемые скиллы**: `docker-compose-patterns`, `fastapi`.
- **Фактическая реализация**:
  - Архитектура Docker Compose (`compose.yaml`): явные `depends_on` с условиями `service_healthy`, внутренний DNS `viio_network`, монтирование томов только там, где это необходимо.
  - Все Python-микросервисы построены на FastAPI с единым стилем валидации Pydantic и типизированными исключениями.

---

## 4. Регламент использования скиллов разработчиками и агентами

1. **Разведка перед внесением изменений**:
   Перед редактированием кода компонента агент обязан проверить наличие релевантного скилла в `.agents/skills/`.
2. **Обязательное ознакомление**:
   Если для задачи существует установленный скилл (например, оптимизация RAG -> `rag-implementation` и `qdrant-search-quality`; оптимизация промптов -> `prompt-optimizer` и `prompt-engineering-patterns`), агент обязан прочитать его `SKILL.md` и следовать приведенным паттернам.
3. **Безопасность и контроль изменений**:
   - Автоматическая установка новых скиллов агентом без явного подтверждения пользователя **запрещена**.
   - Перед предложением нового скилла необходимо проверить его репутацию, количество установок, репозиторий-источник и безопасность содержимого.
   - При установке нового скилла обязательно обновляется `skills-lock.json`.

---

## 5. Управление скиллами через CLI

В среде Windows (PowerShell) для поиска, обновления и установки используется команда `npx.cmd skills`:

```powershell
# Поиск скиллов по ключевому слову
npx.cmd skills find "prompt"
npx.cmd skills find "rag"

# Просмотр установленных скиллов и их обновление
npx.cmd skills update

# Установка скилла в проект (после согласования с пользователем)
npx.cmd skills add <owner/repo@skill> -y
```

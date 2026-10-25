# Архитектурная спецификация: Перевод WebChat на React, сессии и две базы данных PostgreSQL (user_db и agent_db)

**Дата:** 2026-10-25  
**Статус:** Согласовано с пользователем  

---

## 1. Введение и цели

Цель разработки — полная модернизация веб-клиента платформы VIIO и разделение зон ответственности данных между AI-агентом и пользователями:
1. **Переписать сервис `WebChat`** на современный стек: **React + TypeScript + Vite + Tailwind CSS** с поддержкой форматирования Markdown, подсветки синтаксиса кода, светлой/тёмной тем, вложений и истории диалогов.
2. **Обеспечить постоянное хранение сессий и учетных записей** в базе данных пользователей (`user_db`) с аутентификацией по связке username + пароль (хэширование bcrypt) и выдачей безопасных `HttpOnly` cookie.
3. **Разделить базы данных на две независимые схемы/БД в PostgreSQL**:
   - **`user_db`** — хранит пользователей, активные сессии, структуру диалогов и историю сообщений пользователей.
   - **`agent_db`** — хранит внутреннее состояние графа LangGraph, контрольные точки рассуждений (`checkpoints`), промежуточные шаги выполнения и вызовы инструментов агента (`APIAgent`).
4. **Обновить оркестрацию Docker Compose** для автоматического развертывания экземпляра PostgreSQL, создания двух изолированных баз данных при инициализации, контроля готовности сервисов (healthcheck) и сетевого взаимодействия.

---

## 2. Архитектура баз данных (PostgreSQL)

В качестве СУБД используется единый сервис PostgreSQL 16 в Docker Compose, внутри которого создаются две изолированные базы данных: `user_db` и `agent_db`.

### 2.1. Инициализация СУБД (`init-databases.sh`)
При первом запуске контейнера `postgres` запускается скрипт инициализации:
```bash
#!/bin/bash
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
    CREATE DATABASE user_db;
    CREATE DATABASE agent_db;
    GRANT ALL PRIVILEGES ON DATABASE user_db TO $POSTGRES_USER;
    GRANT ALL PRIVILEGES ON DATABASE agent_db TO $POSTGRES_USER;
EOSQL
```

### 2.2. Схема базы данных пользователей (`user_db`)
`user_db` инициализируется при старте сервера `WebChat` с помощью SQL-миграции/DDL:

```sql
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(64) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sessions (
    id VARCHAR(64) PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    user_agent TEXT,
    ip_address VARCHAR(45)
);

CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL DEFAULT 'Новый диалог',
    agent_thread_id VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    sender VARCHAR(16) NOT NULL CHECK (sender IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    attachments JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_conversations_user_id ON conversations(user_id);
CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id);
```

### 2.3. База данных AI-агента (`agent_db`)
Управляется официальной библиотекой LangGraph `langgraph-checkpoint-postgres`:
- Таблицы `checkpoints`, `checkpoint_blobs`, `checkpoint_writes` создаются вызовом `checkpointer.setup()` при запуске микросервиса `APIAgent`.
- Обеспечивает персистентность контекста агента при перезапуске контейнеров: если диалог продолжается с тем же `thread_id`, агент восстанавливает всю историю шагов и рассуждений.

---

## 3. Архитектура и структура сервиса `WebChat`

Сервис `WebChat` объединяет современный клиентский React SPA и легковесный Node.js сервер.

### 3.1. Структура каталогов
```
WebChat/
├── src/                          # React + TypeScript Frontend
│   ├── api/                      # Клиенты HTTP запросов
│   │   ├── client.ts             # Базовый fetch-обертка с credentials: 'include'
│   │   ├── auth.ts               # login, register, logout, getMe
│   │   ├── conversations.ts      # getList, create, getMessages, delete
│   │   └── chat.ts               # sendMessage
│   ├── components/
│   │   ├── Header.tsx            # Название чата, переключатель темы, меню профиля
│   │   ├── Sidebar.tsx           # Список диалогов, кнопка "Новый чат", удаление
│   │   ├── ChatView.tsx          # Лента сообщений, скролл вниз, индикатор печати
│   │   ├── ChatMessage.tsx       # Отображение сообщения (Markdown, Code copy)
│   │   ├── ChatInput.tsx         # Поле ввода текста с поддержкой автовысоты
│   │   └── AuthModal.tsx         # Модальное окно входа и регистрации
│   ├── hooks/
│   │   ├── useAuth.ts            # Состояние пользователя, вход/выход
│   │   ├── useChat.ts            # Отправка сообщений, реактивное обновление
│   │   └── useConversations.ts   # Список и выбор активного диалога
│   ├── types/                    # Определение интерфейсов и DTO
│   ├── App.tsx                   # Главная разметка и маршрутизация состояния
│   ├── main.tsx                  # Входная точка React
│   └── index.css                 # Стили Tailwind CSS
├── server/                       # Серверная часть Node.js (TypeScript)
│   ├── db.ts                     # Пул pg.Pool для user_db и автомиграции DDL
│   ├── auth.ts                   # Логика хэширования bcrypt и работы с сессиями
│   ├── routes/
│   │   ├── authRoutes.ts         # /api/auth/register, /login, /logout, /me
│   │   ├── conversationRoutes.ts # /api/conversations (CRUD)
│   │   └── chatRoutes.ts         # /api/chat (сохранение в БД + вызов APIAgent)
│   └── index.ts                  # Точка входа Express сервера, раздача dist/
├── package.json
├── tsconfig.json
├── vite.config.ts
├── tailwind.config.js
└── Dockerfile
```

### 3.2. Спецификация серверного API `WebChat`

| Метод | Путь | Описание | Защита сессией |
|---|---|---|---|
| `POST` | `/api/auth/register` | Регистрация (username, password), создание сессии, установка cookie | Нет |
| `POST` | `/api/auth/login` | Вход по логину и паролю, создание сессии, установка cookie | Нет |
| `POST` | `/api/auth/logout` | Удаление сессии из БД и очистка cookie | Да |
| `GET` | `/api/auth/me` | Получение информации о текущем пользователе | Да |
| `GET` | `/api/conversations` | Список диалогов текущего пользователя | Да |
| `POST` | `/api/conversations` | Создание нового диалога со случайным `agent_thread_id` | Да |
| `GET` | `/api/conversations/:id` | Загрузка истории сообщений диалога | Да |
| `DELETE` | `/api/conversations/:id` | Удаление диалога пользователя | Да |
| `POST` | `/api/chat` | Отправка сообщения агенту и получение ответа | Да |
| `GET` | `/health` | Проверка доступности сервера и `user_db` | Нет |

---

## 4. Интеграция `APIAgent` с `agent_db`

### 4.1. Изменения в зависимостях
В `APIAgent/pyproject.toml` добавляются:
- `langgraph-checkpoint-postgres>=2.0.0`
- `psycopg[binary,pool]>=3.2.0`

### 4.2. Конфигурация (`src/apiagent/config.py`)
Добавляются параметры подключения к PostgreSQL:
```python
postgres_host: str = Field(default="localhost", description="Хост PostgreSQL")
postgres_port: int = Field(default=5432, description="Порт PostgreSQL")
postgres_db: str = Field(default="agent_db", description="Имя БД агента")
postgres_user: str = Field(default="postgres", description="Пользователь БД")
postgres_password: str = Field(default="postgres", description="Пароль БД")
```

### 4.3. Точка входа (`src/apiagent/__init__.py`)
Замена `MemorySaver` на `PostgresSaver`:
```python
conn_string = (
    f"postgresql://{app_settings.postgres_user}:{app_settings.postgres_password}@"
    f"{app_settings.postgres_host}:{app_settings.postgres_port}/{app_settings.postgres_db}"
)
checkpointer = PostgresSaver.from_conn_string(conn_string)
checkpointer.setup()
```

---

## 5. Обновленная конфигурация Docker Compose

В `docker-compose.yml` в корне проекта настраивается взаимодействие 4 сервисов:
1. **`postgres`** — образ `postgres:16-alpine`, порт `5432`, монтирование тома `postgres_data` и скрипта инициализации `./init-db/init-databases.sh`. Healthcheck: `pg_isready -U postgres`.
2. **`qdrant`** — порты `6333`, `6334`, том `qdrant_storage`, healthcheck `/healthz`.
3. **`apiagent`** — порт `8000`, зависит от здоровых `postgres` и `qdrant`, переменные для подключения к `agent_db` и LM Studio/Ollama.
4. **`webchat`** — порт `3000`, зависит от здоровых `postgres` и `apiagent`, переменные для подключения к `user_db` и `AGENTAI_URL=http://apiagent:8000`.

---

## 6. План тестирования и верификации

1. **Базы данных**:
   - Проверка успешного автоматического создания баз `user_db` и `agent_db`.
   - Проверка создания таблиц `users`, `sessions`, `conversations`, `messages` в `user_db`.
   - Проверка создания таблиц чекпоинтов LangGraph в `agent_db`.
2. **Аутентификация и сессии**:
   - Регистрация пользователя, хэширование пароля через bcrypt, проверка записи в `users`.
   - Проверка выдачи `HttpOnly` cookie и создания записи сессии в таблице `sessions`.
   - Проверка запрета доступа к защищенным эндпоинтам без валидной cookie.
   - Проверка корректного выхода (удаление сессии из БД и удаление cookie).
3. **Диалоги и сквозная интеграция с агентом**:
   - Создание диалога и привязка сообщений пользователя в `messages`.
   - Проксирование запроса в `APIAgent` с сохранением ответа агента в `user_db`.
   - Перезапуск контейнеров `docker compose restart apiagent` и проверка, что агент помнит контекст диалога через `agent_db`.
4. **Пользовательский интерфейс React**:
   - Корректная сборка Vite и отображение UI в браузере.
   - Переключение диалогов, отправка сообщений, рендеринг Markdown и подсветка блоков кода.

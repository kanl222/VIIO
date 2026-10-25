# План реализации: Перевод WebChat на React, сессии и две базы данных PostgreSQL (user_db и agent_db)

> **Для исполнителей:** REQUIRED SUB-SKILL: Используйте `superpowers:subagent-driven-development` (рекомендуется) или `superpowers:executing-plans` для пошаговой реализации этого плана. Шаги используют синтаксис чекбоксов (`- [ ]`) для отслеживания.

**Цель:** Заменить vanilla JS реализацию WebChat на современный React SPA + Node.js (Express/TS) сервер с хранением сессий и пользователей в PostgreSQL (`user_db`), а также подключить постоянное хранилище контрольных точек LangGraph агента в PostgreSQL (`agent_db`), объединив сервисы в Docker Compose.

**Архитектура:** Единый контейнер PostgreSQL 16 поднимает две изолированные базы данных (`user_db` и `agent_db`). `WebChat` содержит React SPA (Vite + TypeScript + Tailwind CSS) и Node.js сервер, управляющий сессиями пользователей в `user_db` и проксирующий сообщения к `APIAgent`. `APIAgent` подключает `PostgresSaver` к `agent_db` для персистентности памяти рассуждений графа.

**Стек технологий:** React 18/19, TypeScript, Vite, Tailwind CSS, Node.js, Express, pg (node-postgres), bcrypt, cookie-parser, Python 3.12, FastAPI, LangGraph, langgraph-checkpoint-postgres, psycopg, Docker Compose.

**Спецификация:** [docs/superpowers/specs/2026-10-25-react-webchat-postgres-dbs-design.md](file:///D:/Source/VIIO/docs/superpowers/specs/2026-10-25-react-webchat-postgres-dbs-design.md)

## Глобальные ограничения

- Порты: WebChat `3000`, APIAgent `8000`, Qdrant `6333`/`6334`, PostgreSQL `5432`.
- Базы данных: `user_db` для пользователей/сессий, `agent_db` для чекпоинтов LangGraph агента.
- Сессии: безопасные `HttpOnly`, `SameSite=Strict` cookie, привязанные к записям в таблице `sessions` в `user_db`.
- Обратная совместимость: APIAgent сохраняет поддержку LM Studio (по умолчанию) и Ollama.
- Никаких внешних CDN в рантайме браузера.

## Фокус проверки (Review Focus)

1. Перезапуск контейнера `apiagent` не должен стирать контекст текущей беседы (проверяется вызовом агента с тем же `thread_id` после рестарта через `agent_db`).
2. Удаление cookie или истечение срока `expires_at` в `sessions` должно возвращать HTTP 401 и блокировать доступ к защищенным чатам.
3. Очистка или удаление диалога пользователем (`DELETE /api/conversations/:id`) должна каскадно удалять все сообщения в `messages` в `user_db`.
4. Ошибки недоступности `APIAgent` должны возвращать читаемый JSON с кодом 502/503 без падения процесса Node.js.
5. Несуществующий пользователь или неверный пароль при логине должны возвращать корректный HTTP 401 с понятным сообщением об ошибке.

---

### Task 1: Инфраструктура PostgreSQL и инициализация двух баз данных

**Файлы:**
- Создать: `init-db/init-databases.sh`
- Изменить: `docker-compose.yml`
- Изменить: `.env.example`

**Интерфейсы:**
- Производит: Docker-сервис `postgres` (образ `postgres:16-alpine`), монтирующий скрипт `./init-db/init-databases.sh` в `/docker-entrypoint-initdb.d/init-databases.sh`.
- Производит: Базы данных `user_db` и `agent_db` с полными правами пользователя `postgres`.

- [ ] **Шаг 1: Создать скрипт инициализации баз данных `init-db/init-databases.sh`**
  Создать bash-скрипт с созданием баз `user_db` и `agent_db` при первом запуске:
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

- [ ] **Шаг 2: Обновить `docker-compose.yml`**
  Добавить сервис `postgres`:
  - `image: postgres:16-alpine`
  - `container_name: viio_postgres`
  - `environment: POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB`
  - `ports: ["5432:5432"]`
  - `volumes: [postgres_data:/var/lib/postgresql/data, ./init-db:/docker-entrypoint-initdb.d]`
  - `healthcheck: test: ["CMD-SHELL", "pg_isready -U postgres"]`
  - Обновить `apiagent` и `webchat` depends_on: добавить зависимость от `postgres: condition: service_healthy`.

- [ ] **Шаг 3: Обновить `.env.example`**
  Добавить секцию конфигурации PostgreSQL (`POSTGRES_USER=postgres`, `POSTGRES_PASSWORD=postgres`, `POSTGRES_PORT=5432`).

- [ ] **Шаг 4: Проверить синтаксис Docker Compose**
  Запустить: `docker-compose config`
  Ожидаемый результат: код возврата 0, отображаются все 4 сервиса (`postgres`, `qdrant`, `apiagent`, `webchat`).

- [ ] **Шаг 5: Зафиксировать изменения (Commit)**

---

### Task 2: Интеграция `APIAgent` с `agent_db` через `PostgresSaver`

**Файлы:**
- Изменить: `APIAgent/pyproject.toml`
- Изменить: `APIAgent/src/apiagent/config.py`
- Изменить: `APIAgent/src/apiagent/__init__.py`
- Создать: `APIAgent/tests/test_postgres_config.py`

**Интерфейсы:**
- Потребляет: Переменные окружения `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`.
- Производит: `checkpointer` на базе `PostgresSaver.from_conn_string(...)`, проинициализированный через `checkpointer.setup()`.

- [ ] **Шаг 1: Написать тест на конфигурацию PostgreSQL в `APIAgent/tests/test_postgres_config.py`**
  Проверить чтение параметров подключения `postgres_host`, `postgres_port`, `postgres_db`, `postgres_user`, `postgres_password` из Settings.

- [ ] **Шаг 2: Добавить зависимости в `APIAgent/pyproject.toml`**
  Добавить `langgraph-checkpoint-postgres>=2.0.0` и `psycopg[binary,pool]>=3.2.0`.

- [ ] **Шаг 3: Обновить `APIAgent/src/apiagent/config.py`**
  Добавить поля `postgres_host`, `postgres_port`, `postgres_db`, `postgres_user`, `postgres_password` с дефолтными значениями.

- [ ] **Шаг 4: Обновить `APIAgent/src/apiagent/__init__.py`**
  В функции `create_app` собрать строку подключения `postgresql://...` к `agent_db`.
  Если PostgreSQL доступен, создать `PostgresSaver.from_conn_string(conn_string)` и выполнить `checkpointer.setup()`.
  Предусмотреть fallback на `MemorySaver` при ошибке подключения к БД (для изолированных unit-тестов).

- [ ] **Шаг 5: Запустить тесты APIAgent**
  Запустить: `APIAgent/.venv/Scripts/pytest.exe APIAgent/tests/test_postgres_config.py`
  Ожидаемый результат: PASS.

- [ ] **Шаг 6: Зафиксировать изменения (Commit)**

---

### Task 3: Серверная часть WebChat (Express, PostgreSQL `user_db`, сессии)

**Файлы:**
- Создать/Изменить: `WebChat/package.json`
- Создать: `WebChat/server/db.ts`
- Создать: `WebChat/server/auth.ts`
- Создать: `WebChat/server/routes/authRoutes.ts`
- Создать: `WebChat/server/routes/conversationRoutes.ts`
- Создать: `WebChat/server/routes/chatRoutes.ts`
- Создать: `WebChat/server/index.ts`
- Создать: `WebChat/test/server-auth.test.mjs`

**Интерфейсы:**
- Потребляет: Подключение к PostgreSQL `user_db`, `AGENTAI_URL` для связи с APIAgent.
- Производит: HTTP API на порту 3000:
  - `/api/auth/register`, `/api/auth/login`, `/api/auth/logout`, `/api/auth/me`
  - `/api/conversations` (GET, POST), `/api/conversations/:id` (GET, DELETE)
  - `/api/chat` (POST)
  - `/health` (GET)

- [ ] **Шаг 1: Настроить `WebChat/package.json`**
  Добавить зависимости: `express`, `pg`, `bcryptjs`, `cookie-parser`, `uuid`, `dotenv`, `@types/express`, `@types/pg`, `@types/bcryptjs`, `@types/cookie-parser`, `@types/uuid`, `typescript`, `tsx`.
  Добавить scripts: `"server": "tsx server/index.ts"`, `"build": "vite build"`, `"start": "node dist/server/index.js"`.

- [ ] **Шаг 2: Реализовать модуль подключения к БД и миграций `WebChat/server/db.ts`**
  Создать пул `pg.Pool` для `user_db`.
  Функция `initDb()` выполняет DDL-запросы создания таблиц `users`, `sessions`, `conversations`, `messages` и индексов.

- [ ] **Шаг 3: Реализовать сервис аутентификации `WebChat/server/auth.ts`**
  - `hashPassword(password)` / `verifyPassword(password, hash)` с использованием `bcryptjs`.
  - `createSession(userId, userAgent, ip)`: генерирует криптографический session_id, сохраняет в `sessions` со сроком на 30 дней.
  - `authMiddleware`: извлекает cookie `session_id`, ищет валидную сессию в БД, прикрепляет `req.user` или возвращает 401.

- [ ] **Шаг 4: Реализовать маршруты `WebChat/server/routes/`**
  - `authRoutes.ts`: регистрация, логин (с установкой HttpOnly cookie), логаут, me.
  - `conversationRoutes.ts`: список чатов текущего пользователя, создание чата с генерацией `agent_thread_id`, получение сообщений чата, удаление чата.
  - `chatRoutes.ts`: сохранение сообщения пользователя в `messages`, пересылка запроса в `APIAgent` (`POST /api/v1/chat`), сохранение ответа агента в `messages`, возврат ответа клиенту.

- [ ] **Шаг 5: Реализовать точку входа `WebChat/server/index.ts`**
  Express-сервер, `express.json()`, `cookieParser()`, подключение маршрутов, статическая раздача папки `dist/` (React SPA) с fallback на `dist/index.html`.

- [ ] **Шаг 6: Написать и выполнить тесты серверной логики в `WebChat/test/server-auth.test.mjs`**
  Запустить: `node --test WebChat/test/server-auth.test.mjs`
  Ожидаемый результат: PASS.

- [ ] **Шаг 7: Зафиксировать изменения (Commit)**

---

### Task 4: Фронтенд WebChat на React (Vite + TypeScript + Tailwind CSS)

**Файлы:**
- Создать: `WebChat/vite.config.ts`, `WebChat/tailwind.config.js`, `WebChat/postcss.config.js`, `WebChat/tsconfig.json`
- Создать: `WebChat/index.html`
- Создать: `WebChat/src/index.css`
- Создать: `WebChat/src/types/index.ts`
- Создать: `WebChat/src/api/client.ts`, `auth.ts`, `conversations.ts`, `chat.ts`
- Создать: `WebChat/src/hooks/useAuth.ts`, `useConversations.ts`, `useChat.ts`
- Создать: `WebChat/src/components/Header.tsx`, `Sidebar.tsx`, `ChatView.tsx`, `ChatMessage.tsx`, `ChatInput.tsx`, `AuthModal.tsx`
- Создать: `WebChat/src/App.tsx`, `WebChat/src/main.tsx`

**Интерфейсы:**
- Потребляет: REST API сервера WebChat (`/api/auth/*`, `/api/conversations/*`, `/api/chat`).
- Производит: Собранный в `WebChat/dist` React SPA с адаптивным интерфейсом, темами и подсветкой кода.

- [ ] **Шаг 1: Настроить сборку Vite и Tailwind CSS**
  Конфигурация `vite.config.ts` (плагин `@vitejs/plugin-react`), `tailwind.config.js` (поддержка dark mode через class), `index.html`.

- [ ] **Шаг 2: Реализовать типы и API-клиенты в `WebChat/src/api/`**
  Типизированные интерфейсы: `User`, `Session`, `Conversation`, `Message`.
  Функции: `login`, `register`, `logout`, `getMe`, `getConversations`, `createConversation`, `getMessages`, `deleteConversation`, `sendMessage`.

- [ ] **Шаг 3: Реализовать пользовательские хуки `src/hooks/`**
  - `useAuth`: загрузка текущего пользователя при монтировании, вызов модалки логина, методы login/register/logout.
  - `useConversations`: загрузка списка бесед, переключение активной беседы, создание новой, удаление.
  - `useChat`: загрузка истории сообщений активной беседы, оптимистичное добавление пользовательского сообщения, отправка на сервер, отображение ответа агента.

- [ ] **Шаг 4: Реализовать компоненты интерфейса `src/components/`**
  - `Header.tsx`: название чата, переключатель светлой/тёмной темы, кнопка выхода.
  - `Sidebar.tsx`: сайдбар со списком бесед, поиском, кнопкой "Новый чат" и удалением.
  - `ChatView.tsx`: контейнер ленты сообщений со скроллом к последнему сообщению и индикатором загрузки ответа.
  - `ChatMessage.tsx`: рендеринг текста сообщения, парсинг markdown-блоков с подсветкой синтаксиса и кнопкой копирования кода.
  - `ChatInput.tsx`: авторасширяемое текстовое поле (textarea), отправка по Enter (Shift+Enter для переноса), кнопка отправки.
  - `AuthModal.tsx`: форма входа и переключения на регистрацию (username, password).

- [ ] **Шаг 5: Собрать `App.tsx` и выполнить сборку Vite**
  Запустить: `cd WebChat; npm run build`
  Ожидаемый результат: генерация `WebChat/dist` без ошибок компиляции TypeScript.

- [ ] **Шаг 6: Зафиксировать изменения (Commit)**

---

### Task 5: Dockerfile сервиса WebChat и финальная сквозная верификация

**Файлы:**
- Изменить: `WebChat/Dockerfile`
- Изменить: `docker-compose.yml`
- Изменить: `README.md`

**Интерфейсы:**
- Производит: Готовый production-образ `WebChat` в Docker, запускающий React SPA + Node.js backend.
- Обеспечивает: Полный цикл развертывания 4 сервисов в `docker-compose.yml`.

- [ ] **Шаг 1: Обновить `WebChat/Dockerfile`**
  Мультистейдж сборка:
  1. `builder`: установка зависимостей, компиляция React SPA (`npm run build`).
  2. `runner`: Node 24 alpine, копирование `dist/`, зависимостей сервера и запуск `node dist/server/index.js`.
  3. `HEALTHCHECK` по эндпоинту `http://127.0.0.1:3000/health`.

- [ ] **Шаг 2: Обновить `docker-compose.yml`**
  Связать переменные окружения `WebChat`:
  - `DATABASE_URL=postgresql://postgres:postgres@postgres:5432/user_db`
  - `AGENTAI_URL=http://apiagent:8000`
  - `PORT=3000`

- [ ] **Шаг 3: Проверить валидность всей конфигурации Docker Compose**
  Запустить: `docker-compose config`
  Ожидаемый результат: чистый вывод конфигурации без предупреждений и ошибок.

- [ ] **Шаг 4: Обновить `README.md`**
  Описать новую архитектуру двух БД (`user_db` и `agent_db`), систему учетных записей WebChat и команды развертывания.

- [ ] **Шаг 5: Зафиксировать изменения (Commit)**

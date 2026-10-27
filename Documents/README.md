# Documents

Самостоятельный сервис базы знаний VIIO. Пакет `viio_documents` находится в `Documents/src`, имеет собственные зависимости, lock-файл, тесты и Dockerfile. Импортов пакета Assistent нет. Оба агента используют только HTTP-контракт.

- `documents` обслуживает каталог, ACL, версии, скачивание, управление очередью и поиск.
- `document-worker` выполняет извлечение текста, индексацию, публикации и восстановление индекса.
- `pdf-worker` — необязательный Docling CPU/OCR в профиле `pdf`.

Поддерживаются PDF, DOCX, ODT, TXT, Markdown и исходники кода. Оригиналы сохраняются отдельно от текста поиска. Новые PDF/DOCX/ODT/TXT проходят очистку пробелов и служебных символов; исходники и произвольный Markdown сохраняют форматирование. Предпросмотр извлекает текст без индексации; для хранения оригинала используйте массовую загрузку, в том числе одного файла. По умолчанию до 50 файлов по 10 МБ.

Существующие PostgreSQL и Docker-том с оригиналами сохраняются. Внутренняя публичность документа не означает публикацию для внешнего консультанта. Внешний агент получает только явно одобренные снимки; приватный оригинал проверяется отдельно.

## Запуск

Сборка: `docker compose build document-worker` из корня проекта. Образ `viio-documents` используется API и worker. Настройка ключей, отдельные роли БД, миграции, Windows/Linux и production override описаны в [инструкции выпуска A](../docs/release-a-2026-10-28.md).

Для разработки без Docker из каталога Documents выполните `uv sync`. В двух отдельных терминалах:

PowerShell:

```powershell
$env:SERVICE_ROLE='document-worker'
uv run uvicorn viio_documents:create_app --factory --port 8003
```

```powershell
$env:SERVICE_ROLE='documents'
$env:DOCUMENT_WORKER_URL='http://localhost:8003'
uv run uvicorn viio_documents:create_app --factory --port 8002
```

Linux:

```sh
SERVICE_ROLE=document-worker uv run uvicorn viio_documents:create_app --factory --port 8003
SERVICE_ROLE=documents DOCUMENT_WORKER_URL=http://localhost:8003 uv run uvicorn viio_documents:create_app --factory --port 8002
```

Для обоих процессов нужны одинаковые подключения к каталогу/хранилищу и отдельные ключи согласно инструкции. Для production обязательно SECURE_RUNTIME=true. Assistent запускается из своего каталога с DOCUMENTS_URL=http://localhost:8002 и DOCUMENTS_READ_KEY. Роль documents/document-worker больше не запускается через пакет assistent.

## Обслуживание

Сценарии находятся в `Documents/scripts`. Доверенный импорт и синхронизацию индекса выполняйте в document-worker с его runtime-правами; миграции и выдачу ролей — отдельной учётной записью развёртывания:

```sh
docker cp Documents/scripts/sync_public_index.py viio_document_worker:/tmp/sync_public_index.py
docker exec viio_document_worker python /tmp/sync_public_index.py
```

Секреты не передавайте в командной строке и не коммитьте `.env`. Полный плановый откат и резервное копирование описаны в инструкции выпуска A.

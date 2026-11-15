"""Импорт материалов базы знаний cf (SQL-шаблоны, регламенты, архитектура SSRS и ИС ЛО).

Выполняется внутри viio_document_worker, используя штатный ingestion_service:
- Сохраняет оригиналы в LocalFileStore (/data/documents)
- Записывает метаданные в PostgreSQL (documents, document_contents)
- Нарезает Small2Big чанки и индексирует в Qdrant
- Публикует в document_publications
"""
import asyncio
import glob
import json
import logging
import os
from pathlib import Path
import re
import uuid

import pypdf
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from viio_documents import create_app
from viio_documents.config import Settings
from viio_documents.services.bulk_queue import BulkUploadTask

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("import_cf")

OWNER = "cf-import-2026-11-13"

TITLES = {
    '1081353': 'Инструкция по работе с программой по учеткам: добавление пользователя, обновление должностей и прав',
    '1081494': 'Инструкция: Обновление палат отделений из ЕЦП в программе по учеткам',
    '132251652': 'Шаблоны SQL: Регистр ПФР — Выборка инвалидов (категории, даты льгот, НСУ)',
    '177963015': 'Шаблоны SQL: Registr.Выписка — Базовая выборка выписанных рецептов, врачей и диагнозов',
    '179732493': 'Шаблоны SQL: registrTEMP.Выписка — Отчет по выписанным рецептам и полям в ИС ЛО',
    '179732505': 'Шаблоны SQL: TestDataBase.Отпуск Online — Анализ отпуска лекарств по периодам',
    '179732507': 'Шаблоны SQL: TestDataBase.Платный отпуск — Анализ платного отпуска рецептов',
    '179732509': 'Шаблоны SQL: Регистр ВЗН — Запросы по пациентам и рецептам высокозатратных нозологий',
    '183009318': 'Шаблоны SQL: TestDataBase.Выписка — Проверка выписки по периодам',
    '185761795': 'Шаблоны SQL: Регистр Орфанные — Выборка пациентов и рецептов по редким заболеваниям',
    '188186625': 'Шаблоны SQL: Registr.Ручные рецепты — Рецепты в отпуске без выписки',
    '20119567': 'Справка: Общее описание информационной системы ИС ЛО и справочников НСИ',
    '20119652': 'Инструкция и SQL: Аномалии при подсчете инвалидов (85 направление, регистр ПФР)',
    '236781649': 'Шаблоны SQL: Сводный каталог запросов выписки, отпуска, DbAuditor и остатков',
    '237994009': 'Шаблоны SQL: Регистр ПФР — Поиск льготников с учетом СНИЛС, дат и категорий',
    '247758849': 'Архитектура и регламент: Модуль формирования отчетов ИСЛО 2.0 (SSRS и ИСЛО)',
    '263782402': 'Шаблоны SQL: TestDataBase.Отпуск — Определение где и когда обслужен рецепт',
    '263782408': 'Шаблоны SQL: Анализ выписанных и отпущенных рецептов за год',
    '269975556': 'Шаблоны SQL: registrTEMP — Просмотр измененных объектов и триггеров в БД',
    '285278209': 'Шаблоны SQL: TestDataBase.Выписка — Детализация когда, кем и кому выписан рецепт',
    '289144834': 'Шаблоны SQL: Registr — Доступные на выписку ЛС (DrugOpen)',
    '297107459': 'Шаблоны SQL и методика: Регистр ПФР — Рецепты по инвалидам и форма 81',
    '318275590': 'Справочник разработчика: Типы полей и параметров для отчетов SSRS',
    '318275597': 'Руководство разработчика: Создание нового отчета ИСЛО (FormController, Form, View)',
    '323223565': 'Руководство разработчика: Создание и регистрация нового отчета SSRS',
    '323223569': 'Руководство разработчика: Добавление отчета в SSRSReport и различия SSRS / ИСЛО',
    '323223571': 'Архитектура модуля SSRSReport: Структура каталогов, контроллеры и модели',
    '328335378': 'Аналитическая справка: Преимущества ИС ЛО по сравнению с ЕЦП',
    '46694414': 'Руководство пользователя: Инструменты и горячие функции (Фишечки) в ИС ЛО',
    '53149701': 'Справочник разработчика: Типы документов и XML-тегов в ИС ЛО (DocumentObject)'
}

TAGS = {
    '1081353': ['учетки', 'инструкция', 'ецп', 'снилс', 'пользователи', 'администрирование'],
    '1081494': ['учетки', 'палаты', 'отделения', 'ецп', 'мо', 'огрн'],
    '132251652': ['sql', 'шаблон', 'пфр', 'инвалиды', 'нсу', 'льготы', 'регистр'],
    '177963015': ['sql', 'шаблон', 'выписка', 'рецепты', 'врачи', 'диагнозы', 'регистр'],
    '179732493': ['sql', 'шаблон', 'выписка', 'отчет', 'ис ло', 'registrTEMP'],
    '179732505': ['sql', 'шаблон', 'отпуск', 'онлайн', 'аптека', 'тестовая база'],
    '179732507': ['sql', 'шаблон', 'платный отпуск', 'аптека', 'рецепты'],
    '179732509': ['sql', 'шаблон', 'взн', 'высокозатратные нозологии', 'регистр', 'льготники'],
    '183009318': ['sql', 'шаблон', 'выписка', 'период', 'проверка'],
    '185761795': ['sql', 'шаблон', 'орфанные', 'редкие заболевания', 'регистр'],
    '188186625': ['sql', 'шаблон', 'ручные рецепты', 'отпуск', 'выписка', 'расхождения'],
    '20119567': ['ис ло', 'описание', 'нси', 'справочники', 'минздрав'],
    '20119652': ['инвалиды', '85 направление', 'аномалии', 'подсчет', 'пфр', 'sql'],
    '236781649': ['sql', 'каталог', 'шаблоны', 'выписка', 'отпуск', 'dbauditor'],
    '237994009': ['sql', 'пфр', 'поиск', 'снилс', 'категории', 'льготы'],
    '247758849': ['ssrs', 'исло 2.0', 'модуль отчетов', 'архитектура', 'контроллер'],
    '263782402': ['sql', 'отпуск', 'аптека', 'обслуживание рецепта'],
    '263782408': ['sql', 'анализ', 'выписанные', 'отпущенные', 'статистика'],
    '269975556': ['sql', 'триггеры', 'изменения', 'структура бд', 'registrTEMP'],
    '285278209': ['sql', 'выписка', 'мо', 'врач', 'пациент'],
    '289144834': ['sql', 'drugopen', 'доступные лс', 'выписка', 'остатки'],
    '297107459': ['sql', 'форма 81', 'инвалиды', 'рецепты', 'пфр', 'отчетность'],
    '318275590': ['ssrs', 'типы данных', 'параметры отчетов', 'разработчик'],
    '318275597': ['исло', 'создание отчета', 'formcontroller', 'разработчик', 'php'],
    '323223565': ['ssrs', 'создание отчета', 'инструкция', 'разработчик'],
    '323223569': ['ssrsreport', 'сравнение', 'отчеты', 'разработчик', 'исло'],
    '323223571': ['ssrsreport', 'архитектура', 'контроллеры', 'модели', 'файлы'],
    '328335378': ['ис ло', 'ецп', 'преимущества', 'сравнение'],
    '46694414': ['инструкция', 'фишечки', 'горячие клавиши', 'интерфейс'],
    '53149701': ['типы документов', 'xml', 'documentobject', 'структура']
}


def prepare_markdown(pdf_path: str, title: str) -> str:
    reader = pypdf.PdfReader(pdf_path)
    raw = "\n".join([p.extract_text() or "" for p in reader.pages])
    lines = [l.rstrip() for l in raw.split("\n")]
    
    # Очистка лидирующих пустых строк и нумераций (1. 2. a. b.)
    idx = 0
    while idx < len(lines):
        line = lines[idx].strip()
        if not line or re.match(r"^\d+[\.\)]?$|^[a-zA-Z][\.\)]?$", line):
            idx += 1
            continue
        break
    cleaned_lines = lines[idx:]
    
    fence = chr(96) * 3
    md_lines = [f"# {title}\n"]
    in_code = False
    
    for line in cleaned_lines:
        s = line.strip()
        is_sql = bool(re.match(r"^(SELECT|INSERT|UPDATE|DELETE|DECLARE|SET|FROM|WHERE|JOIN|GROUP BY|ORDER BY|CREATE|ALTER|DROP|--)\b", s, re.I))
        is_code = bool(re.match(r"^(use |public function|\$model|Yii::|\<?php|\$this->)", s))
        
        if (is_sql or is_code) and not in_code:
            lang = "sql" if is_sql else "php"
            md_lines.append(f"{fence}{lang}")
            in_code = True
        elif in_code and not s:
            md_lines.append(fence)
            in_code = False
        md_lines.append(line)
        
    if in_code:
        md_lines.append(fence)
        
    return "\n".join(md_lines).strip()


async def main():
    settings = Settings()
    if settings.service_role != "document-worker":
        raise RuntimeError("Импорт должен запускаться в viio_document_worker")

    app = create_app(settings=settings)
    pool = AsyncConnectionPool(
        make_conninfo(
            host=settings.postgres_host,
            port=settings.postgres_port,
            dbname=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password,
        ),
        open=False,
        kwargs={"row_factory": dict_row},
        min_size=1,
        max_size=5,
    )
    await pool.open(wait=True)
    app.state.catalog_service.set_pool(pool)
    ingestion = app.state.ingestion_service

    import_dir = Path("/data/documents/cf_import")
    files = sorted(import_dir.glob("*.pdf"))
    logger.info("Найдено файлов для импорта: %d", len(files))

    imported_count = 0
    for f in files:
        prefix = f.name.split("_")[0]
        title = TITLES.get(prefix, f.stem)
        doc_tags = TAGS.get(prefix, ["база знаний", "ис ло", "sql"])
        
        # Подготовка текста
        md_content = prepare_markdown(str(f), title)
        original_bytes = f.read_bytes()
        
        metadata = {
            "tags": doc_tags,
            "description": title,
            "source_file": f.name,
            "import_batch": OWNER,
            "content_scope": "code_and_knowledge",
        }

        task = BulkUploadTask(
            task_id=uuid.uuid4().hex,
            filename=f.name,
            data=original_bytes,
            title=title[:255],
            owner_id=OWNER,
            is_public=False,
            mime_type="application/pdf",
            allowed_roles=["employee", "programmer", "admin"],
            allowed_departments=[],
            reviewed_content=md_content,
            metadata={"catalog_metadata": metadata},
        )

        storage_key = ingestion.store.save(original_bytes, suffix=".pdf")
        doc_id, chunks = await ingestion(task, storage_key)
        
        # Публикуем документ
        await ingestion.data.publish(doc_id, title[:255], md_content, OWNER)
        
        imported_count += 1
        logger.info("[%02d/%d] Импортирован: %s (id: %s, chunks: %d)", imported_count, len(files), title, doc_id, chunks)

    await pool.close()
    logger.info("Успешно импортировано документов: %d", imported_count)


if __name__ == "__main__":
    asyncio.run(main())

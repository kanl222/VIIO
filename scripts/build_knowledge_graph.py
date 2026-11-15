# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "httpx>=0.28.0",
#     "neo4j>=6.4.1",
# ]
# ///
"""
Построение Графа Знаний (Knowledge Graph) из базы документов.
Скрипт читает документы, нарезает их на чанки, отправляет в LLM (LM Studio / Ollama / OpenAI)
для извлечения сущностей и связей (триплетов), и сохраняет их в Memgraph.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

import httpx
from neo4j import GraphDatabase

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("graph_builder")

EXTRACTION_SYSTEM_PROMPT = """Ты — аналитик данных и эксперт по онтологиям предметной области аптечных складов и информационных систем (WMS, ERP, МДЛП, GDP).
Твоя задача — извлечь из текста структуру процессов, регламентов, режимов хранения, систем и действий в строгом соответствии с дата-моделью.

Дата-модель онтологии:
1. Типы узлов (label):
   - Department: Отдел компании (например, "Складской комплекс", "Отдел обеспечения качества", "Отдел закупок").
   - Role: Должность или роль (например, "Заведующий складом", "Кладовщик приёмки", "Фармацевт-контролёр").
   - Process: Бизнес-процесс (например, "Приёмка лекарственных препаратов", "Инвентаризация").
   - Step: Шаг или действие алгоритма (например, "Входной документарный контроль", "Сканирование DataMatrix").
   - Product: Лекарственный препарат или товар (например, "Вакцина Гам-КОВИД-Вак", "Парацетамол").
   - StorageCondition: Температурный/влажностный режим хранения (например, "Холодовая цепь 2–8 °C", "Умеренный режим 15–25 °C").
   - Location: Складская зона или ячейка (например, "Холодильная камера 1", "Зона приёмки").
   - Category: Категория препаратов (например, "ЖНВЛП", "Термолабильные препараты", "НС и ПВ").
   - Regulation: Нормативный документ или стандарт (например, "GDP", "ПП РФ № 1556", "ФЗ-61").
   - System: Информационная система (например, "WMS VIIO", "1С:ERP Предприятие", "ИС «МДЛП»").
   - DataEntity: Сущность или справочник данных (например, "Остатки и статусы серий", "Справочник номенклатуры").
   - MenuPath: Путь меню в интерфейсе через знак ">" (например, "Управление запасами > Инвентаризация").

2. Типы связей (relation):
   - OWNS: (Department) -> (Process)
   - HAS_STEP: (Process) -> (Step)
   - NEXT: (Step) -> (Step)
   - RESPONSIBLE_FOR: (Role) -> (Step)
   - EXECUTED_IN: (Step) -> (System)
   - REQUIRES: (Product) -> (StorageCondition)
   - PROVIDES: (Location) -> (StorageCondition)
   - BELONGS_TO: (Product) -> (Category)
   - COMPLIES_WITH: (Process|Product|Category) -> (Regulation)
   - SOURCE_OF_TRUTH_FOR: (System) -> (DataEntity)
   - INTEGRATES_WITH: (System) -> (System)
   - HAS_MENU: (System) -> (MenuPath)
   - CONTAINS_ACTION: (MenuPath) -> (Step)

3. Формат ответа — СТРОГО валидный JSON:
{
  "entities": [
    {"name": "Приёмка лекарственных препаратов", "label": "Process"},
    {"name": "Поэкземплярное сканирование маркировки МДЛП", "label": "Step"},
    {"name": "Кладовщик приёмки", "label": "Role"},
    {"name": "WMS VIIO", "label": "System"},
    {"name": "Холодовая цепь 2–8 °C", "label": "StorageCondition"}
  ],
  "relations": [
    {"source": "Приёмка лекарственных препаратов", "relation": "HAS_STEP", "target": "Поэкземплярное сканирование маркировки МДЛП"},
    {"source": "Кладовщик приёмки", "relation": "RESPONSIBLE_FOR", "target": "Поэкземплярное сканирование маркировки МДЛП"},
    {"source": "Поэкземплярное сканирование маркировки МДЛП", "relation": "EXECUTED_IN", "target": "WMS VIIO"}
  ]
}
"""


def clean_json_response(raw_text: str) -> dict[str, Any]:
    """Извлекает и парсит JSON из ответа нейросети, отсекая <think> и восстанавливая синтаксис."""
    text = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL).strip()
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if match:
        text = match.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            text = text[start : end + 1]

    # Попытка 1: стандартный парсинг
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return {
                "entities": data.get("entities", []),
                "relations": data.get("relations", []),
            }
    except Exception:
        pass

    # Попытка 2: починка типичных синтаксических сбоев моделей (trailing commas)
    try:
        cleaned = re.sub(r",\s*([\]}])", r"\1", text)
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return {
                "entities": data.get("entities", []),
                "relations": data.get("relations", []),
            }
    except Exception as e:
        logger.warning(f"Не удалось распарсить JSON из ответа LLM: {e}\nОтвет: {text[:250]}...")

    return {"entities": [], "relations": []}


def chunk_text(text: str, chunk_size: int = 1800, overlap: int = 200) -> list[str]:
    """Разбивает длинный текст на перекрывающиеся фрагменты."""
    text = text.strip()
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end < len(text):
            # Стараемся разрезать по границе абзаца или предложения
            newline = text.rfind("\n", start, end)
            if newline > start + (chunk_size // 2):
                end = newline
            else:
                dot = text.rfind(". ", start, end)
                if dot > start + (chunk_size // 2):
                    end = dot + 1

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = max(end - overlap, start + 1)
        if end >= len(text):
            break
    return chunks


class GraphBuilder:
    def __init__(
        self,
        llm_url: str,
        model: str,
        api_key: str,
        memgraph_uri: str,
        dry_run: bool = False,
    ):
        self.llm_url = llm_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.dry_run = dry_run
        self.client = httpx.Client(timeout=120.0)

        if not self.dry_run:
            self.driver = GraphDatabase.driver(memgraph_uri, auth=("", ""))
            self._init_db()
        else:
            self.driver = None

    def _init_db(self):
        """Создание индексов и ограничений в Memgraph по онтологии."""
        try:
            with self.driver.session() as session:
                session.run("CREATE CONSTRAINT ON (d:Document) ASSERT d.id IS UNIQUE;")
                session.run("CREATE CONSTRAINT ON (e:Entity) ASSERT e.name IS UNIQUE;")
                session.run("CREATE CONSTRAINT ON (s:System) ASSERT s.name IS UNIQUE;")
                session.run("CREATE CONSTRAINT ON (m:MenuPath) ASSERT m.full_path IS UNIQUE;")
                session.run("CREATE CONSTRAINT ON (o:Operation) ASSERT o.name IS UNIQUE;")
                session.run("CREATE CONSTRAINT ON (r:Role) ASSERT r.title IS UNIQUE;")
                session.run("CREATE CONSTRAINT ON (c:Condition) ASSERT c.rule_text IS UNIQUE;")
                session.run("CREATE CONSTRAINT ON (dc:DrugCategory) ASSERT dc.name IS UNIQUE;")
                session.run("CREATE INDEX ON :Document(title);")
                session.run("CREATE INDEX ON :Entity(type);")
            logger.info("Индексы и ограничения онтологии в Memgraph проверены.")
        except Exception as e:
            logger.warning(f"Инициализация индексов: {e}")

    def close(self):
        self.client.close()
        if self.driver:
            self.driver.close()

    def extract_triplets(self, text: str) -> dict[str, list[dict]]:
        """Отправляет чанк в LLM для извлечения триплетов."""
        url = f"{self.llm_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": f"Извлеки структуру интерфейса, регламенты, действия и условия:\n\n{text}"},
            ],
            "temperature": 0.1,
            "max_tokens": 1500,
        }

        try:
            resp = self.client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            choice = data["choices"][0]["message"]
            raw_content = choice.get("content") or choice.get("reasoning_content") or ""
            return clean_json_response(raw_content)
        except Exception as e:
            logger.error(f"Ошибка запроса к LLM ({self.llm_url}): {e}")
            return {"entities": [], "relations": []}

    def save_to_memgraph(
        self,
        doc_id: str,
        title: str,
        triplets: dict[str, list[dict]],
    ) -> tuple[int, int]:
        """Сохраняет узлы и связи в Memgraph в соответствии с дата-моделью."""
        if self.dry_run or not self.driver:
            return len(triplets["entities"]), len(triplets["relations"])

        entities_count = 0
        relations_count = 0

        with self.driver.session() as session:
            # 1. Узел документа
            session.run(
                """
                MERGE (d:Document {id: $doc_id})
                SET d.title = $title, d.updated_at = timestamp()
                """,
                doc_id=doc_id,
                title=title,
            )

            # 2. Сущности и типизированные узлы онтологии
            for ent in triplets.get("entities", []):
                name = str(ent.get("name", "")).strip()
                label = str(ent.get("label") or ent.get("type") or "Entity").strip()
                if not name:
                    continue

                # Базовый узел Entity для сквозного поиска
                session.run(
                    """
                    MATCH (d:Document {id: $doc_id})
                    MERGE (e:Entity {name: $name})
                    ON CREATE SET e.type = $label, e.mentions_count = 1
                    ON MATCH SET e.mentions_count = e.mentions_count + 1
                    MERGE (d)-[m:MENTIONS]->(e)
                    ON CREATE SET m.count = 1
                    ON MATCH SET m.count = m.count + 1
                    """,
                    doc_id=doc_id,
                    name=name,
                    label=label,
                )

                # Присвоение канонических меток онтологии на узел Entity
                if label == "System":
                    session.run("MATCH (e:Entity {name: $name}) SET e:System", name=name)
                elif label == "MenuPath":
                    session.run(
                        """
                        MATCH (d:Document {id: $doc_id})
                        MATCH (e:Entity {name: $name})
                        SET e:MenuPath, e.full_path = $name
                        MERGE (e)-[:DOCUMENTED_BY]->(d)
                        """,
                        doc_id=doc_id,
                        name=name,
                    )
                elif label == "Operation":
                    session.run("MATCH (e:Entity {name: $name}) SET e:Operation", name=name)
                elif label == "Role":
                    session.run("MATCH (e:Entity {name: $name}) SET e:Role, e.title = $name", name=name)
                elif label == "Condition":
                    session.run("MATCH (e:Entity {name: $name}) SET e:Condition, e.rule_text = $name", name=name)
                elif label == "DrugCategory":
                    session.run("MATCH (e:Entity {name: $name}) SET e:DrugCategory", name=name)

                entities_count += 1

            # 3. Связи между сущностями
            for rel in triplets.get("relations", []):
                src = str(rel.get("source", "")).strip()
                tgt = str(rel.get("target", "")).strip()
                raw_rel = str(rel.get("relation", "RELATES_TO")).strip()
                rel_type = "".join(c for c in raw_rel.upper() if c.isalnum() or c == "_")
                if not rel_type:
                    rel_type = "RELATES_TO"

                if not src or not tgt or src == tgt:
                    continue

                session.run(
                    f"""
                    MERGE (s:Entity {{name: $source}})
                    MERGE (t:Entity {{name: $target}})
                    MERGE (s)-[r:{rel_type}]->(t)
                    ON CREATE SET r.weight = 1, r.doc_id = $doc_id
                    ON MATCH SET r.weight = r.weight + 1
                    """,
                    source=src,
                    target=tgt,
                    doc_id=doc_id,
                )
                relations_count += 1

        return entities_count, relations_count

    def get_processed_doc_ids(self) -> set[str]:
        """Возвращает ID документов, которые уже сохранены в базе."""
        if not self.driver:
            return set()
        try:
            with self.driver.session() as session:
                res = session.run("MATCH (d:Document) RETURN d.id AS id")
                return {record["id"] for record in res if record.get("id")}
        except Exception as e:
            logger.warning(f"Не удалось получить список существующих документов: {e}")
            return set()

    def process_directory(
        self,
        source_dir: Path,
        max_docs: int = 0,
        chunk_size: int = 2500,
        force: bool = False,
    ):
        """Обход файлов базы знаний и наполнение графа."""
        files = sorted(
            [f for f in source_dir.glob("*.txt")] + [f for f in source_dir.glob("*.md")]
        )
        if not files:
            logger.warning(f"В папке {source_dir} не найдено .txt или .md файлов.")
            return

        total_files = len(files) if max_docs <= 0 else min(len(files), max_docs)
        existing_doc_ids = set() if force else self.get_processed_doc_ids()
        if existing_doc_ids:
            logger.info(
                f"Найдено уже обработанных документов в графе: {len(existing_doc_ids)} (будут пропущены)."
            )

        logger.info(f"Найдено документов для обработки: {total_files}")

        total_entities = 0
        total_relations = 0

        for idx, file_path in enumerate(files[:total_files], 1):
            doc_id = file_path.stem
            if not force and doc_id in existing_doc_ids:
                logger.info(f"[{idx}/{total_files}] Документ '{doc_id}' уже в графе, пропускаем.")
                continue

            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                logger.error(f"Ошибка чтения {file_path.name}: {e}")
                continue

            first_line = content.splitlines()[0] if content.splitlines() else file_path.name
            title = first_line.lstrip("# ").strip()[:150] or file_path.name

            chunks = chunk_text(content, chunk_size=chunk_size)
            logger.info(f"[{idx}/{total_files}] '{title}' (чанков: {len(chunks)})")

            for chunk_idx, chunk in enumerate(chunks, 1):
                triplets = self.extract_triplets(chunk)
                e_cnt, r_cnt = self.save_to_memgraph(doc_id, title, triplets)
                total_entities += e_cnt
                total_relations += r_cnt
                logger.debug(
                    f"  Чанк {chunk_idx}/{len(chunks)}: +{e_cnt} сущностей, +{r_cnt} связей"
                )

        logger.info(
            f"Готово! Обработано документов: {total_files}. "
            f"Извлечено сущностей: {total_entities}, связей: {total_relations}."
        )


def main():
    parser = argparse.ArgumentParser(description="Построение Графа Знаний в Memgraph с помощью LLM")
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path("data/public-canonical-storage"),
        help="Путь к каталогу с текстовыми документами базы знаний",
    )
    parser.add_argument(
        "--max-docs",
        type=int,
        default=0,
        help="Максимальное количество документов для обработки (0 — все документы)",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=2500,
        help="Размер чанка для длинных документов в символах",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Принудительно переобработать даже те документы, которые уже есть в графе",
    )
    parser.add_argument(
        "--llm-url",
        type=str,
        default=os.getenv("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1").replace("host.docker.internal", "127.0.0.1"),
        help="URL OpenAI-совместимого API (например, LM Studio или Ollama)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="qwen/qwen3-1.7b",
        help="Название модели в LLM-провайдере",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=os.getenv("LMSTUDIO_API_KEY", "lm-studio"),
        help="API ключ для LLM",
    )
    parser.add_argument(
        "--memgraph-uri",
        type=str,
        default="bolt://127.0.0.1:7687",
        help="Bolt URI для подключения к Memgraph",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Запустить без записи в Memgraph (только вывод в консоль)",
    )

    args = parser.parse_args()

    builder = GraphBuilder(
        llm_url=args.llm_url,
        model=args.model,
        api_key=args.api_key,
        memgraph_uri=args.memgraph_uri,
        dry_run=args.dry_run,
    )
    try:
        builder.process_directory(
            args.source_dir,
            max_docs=args.max_docs,
            chunk_size=args.chunk_size,
            force=args.force,
        )
    finally:
        builder.close()


if __name__ == "__main__":
    main()

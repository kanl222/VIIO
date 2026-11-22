# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "neo4j>=6.4.1",
# ]
# ///
"""Скрипт загрузки и прикрепления данных к графу знаний Memgraph из JSON-файлов (Semantic GraphRAG).

Читает конфигурацию веток онтологии из отдельной папки с JSON-файлами (по умолчанию data/knowledge_graph/):
1. 01_organizations.json — Организации и их нормативное регулирование
2. 02_departments.json — Структурные подразделения и отделы
3. 03_employees.json — Должностные лица, контакты и привязка к отделам (WORKS_IN)
4. 04_regulations.json — Нормативно-правовые акты (ФЗ, ПП РФ, Приказы МЗ РФ, акты ОО)
5. 05_systems.json — Информационные системы (ИС ЛО, ЕГИСЗ, МДЛП, Карта Оренбуржца)
6. 06_documents.json — Регламенты и документы базы знаний (№ 70–78)
7. 07_processes.json — Бизнес-процессы учета, льготного отпуска, маркировки и списания
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from neo4j import GraphDatabase

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("graph_attacher")

MEMGRAPH_URI = os.getenv("MEMGRAPH_URI", "bolt://memgraph:7687")
MEMGRAPH_USER = os.getenv("MEMGRAPH_USER", "")
MEMGRAPH_PASSWORD = os.getenv("MEMGRAPH_PASSWORD", "")


def load_branch_file(file_path: Path) -> dict[str, Any]:
    """Загружает и валидирует один JSON-файл ветки онтологии."""
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if "nodes" not in data or "relations" not in data:
        raise ValueError(f"Файл {file_path} должен содержать секции 'nodes' и 'relations'")
    return data


def sync_nodes(session, nodes: list[dict[str, Any]]):
    """Синхронизирует узлы графа через параметризованные MERGE-запросы."""
    for node in nodes:
        label = node.get("label")
        match_key = node.get("match_key")
        props = node.get("properties", {})
        
        if not label or not match_key:
            logger.warning("Пропуск некорректного узла (нет label или match_key): %s", node)
            continue
            
        match_val = props.get(match_key)
        if match_val is None:
            logger.warning("Пропуск узла %s: отсутствует значение ключа %s", label, match_key)
            continue

        query = f"""
        MERGE (n:{label} {{{match_key}: $match_val}})
        SET n += $props
        """
        session.run(query, match_val=match_val, props=props)


def sync_relations(session, relations: list[dict[str, Any]]):
    """Синхронизирует связи между узлами через параметризованные MERGE-запросы."""
    for rel in relations:
        rel_type = rel.get("type")
        from_spec = rel.get("from", {})
        to_spec = rel.get("to", {})
        props = rel.get("properties", {})

        from_label = from_spec.get("label")
        from_key = from_spec.get("key")
        from_val = from_spec.get("value")

        to_label = to_spec.get("label")
        to_key = to_spec.get("key")
        to_val = to_spec.get("value")

        if not all([rel_type, from_label, from_key, from_val is not None, to_label, to_key, to_val is not None]):
            logger.warning("Пропуск некорректной связи: %s", rel)
            continue

        query = f"""
        MATCH (a:{from_label} {{{from_key}: $from_val}})
        MATCH (b:{to_label} {{{to_key}: $to_val}})
        MERGE (a)-[r:{rel_type}]->(b)
        """
        if props:
            query += " SET r += $props"

        session.run(query, from_val=from_val, to_val=to_val, props=props)


def cleanup_obsolete_entities(session):
    """Удаляет вымышленные и устаревшие тестовые узлы, если они остались в базе."""
    obsolete_queries = [
        "MATCH (e:Employee {name: 'Квасов Александр Владимирович'}) DETACH DELETE e;",
        "MATCH (e:Employee {name: 'Квасов Владимир Александрович'}) DETACH DELETE e;",
        "MATCH (e:Employee {name: 'Дебус Ирина Александровна'}) DETACH DELETE e;",
        "MATCH (e:Employee {name: 'Некрасова Ольга Владимировна'}) DETACH DELETE e;",
        "MATCH (e:Employee {name: 'Бурасова Светлана Сергеевна'}) DETACH DELETE e;",
        "MATCH (e:Employee {name: 'Вербина Евгения Андреевна'}) DETACH DELETE e;",
        "MATCH (e:Employee {name: 'Князев Алексей Николаевич'}) DETACH DELETE e;",
        "MATCH (e:Employee {name: 'Сумкин Евгений Владимирович'}) DETACH DELETE e;",
        "MATCH (e:Employee {name: 'Рагузин Сергей Владимирович'}) DETACH DELETE e;",
        "MATCH (e:Employee {name: 'Терентьева Наталья Ивановна'}) DETACH DELETE e;",
        "MATCH (e:Employee {name: 'Балябкина Елена Петровна'}) DETACH DELETE e;",
        "MATCH (e:Employee {name: 'Серова Татьяна Николаевна'}) DETACH DELETE e;",
    ]
    for q in obsolete_queries:
        result = session.run(q)
        summary = result.consume()
        if summary.counters.nodes_deleted > 0:
            logger.info("Удален устаревший узел: %d шт.", summary.counters.nodes_deleted)


def attach_knowledge_from_dir(data_dir: Path, uri: str = MEMGRAPH_URI, user: str = MEMGRAPH_USER, password: str = MEMGRAPH_PASSWORD):
    """Считывает все JSON-файлы из указанной папки и загружает их в Memgraph."""
    if not data_dir.exists() or not data_dir.is_dir():
        raise FileNotFoundError(f"Каталог с ветками графа не найден: {data_dir}")

    json_files = sorted(data_dir.glob("*.json"))
    if not json_files:
        raise ValueError(f"В каталоге {data_dir} не найдено .json файлов с данными графа!")

    logger.info("Найдено %d файлов веток графа в каталоге %s", len(json_files), data_dir)
    auth = (user, password) if user or password else None
    driver = GraphDatabase.driver(uri, auth=auth)

    with driver.session() as session:
        logger.info("Подключение к Memgraph успешно. Очистка устаревших тестовых записей...")
        cleanup_obsolete_entities(session)

        for file_path in json_files:
            try:
                branch_data = load_branch_file(file_path)
                branch_name = branch_data.get("branch", file_path.stem)
                desc = branch_data.get("description", "")
                nodes = branch_data.get("nodes", [])
                relations = branch_data.get("relations", [])

                logger.info("--> Загрузка ветки '%s' (%s): узлов: %d, связей: %d", branch_name, desc, len(nodes), len(relations))
                
                # 1. Синхронизация узлов
                sync_nodes(session, nodes)

                # 2. Синхронизация связей
                sync_relations(session, relations)

            except Exception as exc:
                logger.error("Ошибка при обработке файла %s: %s", file_path.name, exc, exc_info=True)
                raise

        # -------------------------------------------------------------------------
        # Итоговая статистика графа
        # -------------------------------------------------------------------------
        logger.info("Проверка итоговой статистики Memgraph...")
        nodes_stat = session.run("MATCH (n) RETURN labels(n) AS lbl, count(n) AS cnt ORDER BY cnt DESC").data()
        rels_stat = session.run("MATCH ()-[r]->() RETURN type(r) AS rel, count(r) AS cnt ORDER BY cnt DESC").data()

        print("\n================== ИТОГОВАЯ СТАТИСТИКА ГРАФА MEMGRAPH ==================")
        print("Узлы (Nodes):")
        total_n = 0
        for r in nodes_stat:
            total_n += r["cnt"]
            print(f"  {r['lbl']}: {r['cnt']}")
        print(f"Всего узлов: {total_n}")

        print("\nСвязи (Relations):")
        total_r = 0
        for r in rels_stat:
            total_r += r["cnt"]
            print(f"  {r['rel']}: {r['rel'] if 'rel' in r else r['cnt']}")
        print(f"Всего связей: {total_r}")
        print("=======================================================================\n")

    driver.close()
    logger.info("Прикрепление данных из JSON к графу Memgraph успешно завершено!")


def main():
    default_dir = Path(__file__).resolve().parents[1] / "data" / "knowledge_graph"
    parser = argparse.ArgumentParser(description="Загрузка веток графа знаний Memgraph из JSON-файлов.")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=default_dir,
        help=f"Путь к папке с JSON-файлами веток графа (по умолчанию: {default_dir})",
    )
    parser.add_argument(
        "--uri",
        default=MEMGRAPH_URI,
        help=f"URI подключения к Memgraph (по умолчанию: {MEMGRAPH_URI})",
    )
    args = parser.parse_args()

    attach_knowledge_from_dir(args.data_dir, uri=args.uri)


if __name__ == "__main__":
    main()

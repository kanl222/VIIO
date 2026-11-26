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
8. 08_abbreviations.json — Сокращения, определения и варианты написания
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from knowledge_graph_validation import alias_key, load_branches, validate_identifier

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
        validate_identifier(label)
        validate_identifier(match_key)
        if label == 'Abbreviation':
            props = {**props, 'search_aliases': sorted({alias_key(value) for value in
                [props['name'], props.get('short_form', ''), *props.get('aliases', [])] if value})}
        
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
        validate_identifier(rel_type)
        from_spec = rel.get("from", {})
        to_spec = rel.get("to", {})
        props = rel.get("properties", {})

        from_label = from_spec.get("label")
        from_key = from_spec.get("key")
        from_val = from_spec.get("value")

        to_label = to_spec.get("label")
        to_key = to_spec.get("key")
        to_val = to_spec.get("value")
        for identifier in (from_label, from_key, to_label, to_key):
            validate_identifier(identifier)

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


def reconcile_nodes(transaction, operations):
    """Apply explicitly reviewed local replacements; retain all incident relationships."""
    for operation in operations:
        old = operation['old']
        target = operation.get('target')
        for spec in (old, target):
            if spec:
                validate_identifier(spec['label'])
                validate_identifier(spec['key'])
        query = f"MATCH (n:{old['label']} {{{old['key']}: $value}}) RETURN id(n) AS id"
        for row in transaction.run(query, value=old['value']).data():
            old_id = row['id']
            if target:
                rows = transaction.run(
                    f"MATCH (n:{target['label']} {{{target['key']}: $value}}) RETURN id(n) AS id",
                    value=target['value']).data()
                if len(rows) != 1:
                    raise ValueError('Replacement target must resolve to exactly one node')
                new_id = rows[0]['id']
                if new_id == old_id:
                    raise ValueError('Cannot replace a node with itself')
                edges = transaction.run(
                    'MATCH (a)-[r]->(b) WHERE id(a)=$id OR id(b)=$id '
                    'RETURN id(a) AS source, id(b) AS target, type(r) AS type, properties(r) AS props', id=old_id).data()
                for edge in edges:
                    validate_identifier(edge['type'])
                    source = new_id if edge['source'] == old_id else edge['source']
                    destination = new_id if edge['target'] == old_id else edge['target']
                    transaction.run(f"MATCH (a),(b) WHERE id(a)=$a AND id(b)=$b "
                                    f"MERGE (a)-[r:{edge['type']}]->(b) SET r += $props",
                                    a=source, b=destination, props=edge['props']).consume()
            transaction.run('MATCH (n) WHERE id(n)=$id DETACH DELETE n', id=old_id).consume()


def attach_knowledge_from_dir(data_dir: Path, uri: str = MEMGRAPH_URI, user: str = MEMGRAPH_USER, password: str = MEMGRAPH_PASSWORD, reconcile_manifest: Path | None = None):
    """Считывает все JSON-файлы из указанной папки и загружает их в Memgraph."""
    # Validate every branch before any cleanup or write; one failed branch writes nothing.
    branches = load_branches(data_dir)
    operations = json.loads(reconcile_manifest.read_text(encoding='utf8')) if reconcile_manifest else []
    # Validate identifiers before opening a connection. Manifests contain local data only.
    for operation in operations:
        for spec in (operation['old'], operation.get('target')):
            if spec:
                validate_identifier(spec['label'])
                validate_identifier(spec['key'])
                if spec.get('value') is None:
                    raise ValueError('Reconciliation identity requires a value')
    auth = (user, password) if user or password else None
    driver = GraphDatabase.driver(uri, auth=auth)
    with driver.session() as session:
        with session.begin_transaction() as transaction:
            for branch in branches:
                sync_nodes(transaction, branch['nodes'])
            if operations:
                reconcile_nodes(transaction, operations)
            # All nodes exist before any cross-file relationship is created.
            for branch in branches:
                sync_relations(transaction, branch['relations'])
            transaction.commit()

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
            print(f"  {r['rel']}: {r['cnt']}")
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
    parser.add_argument("--validate-only", action="store_true", help="Validate all branches without connecting to Memgraph")
    parser.add_argument("--reconcile-manifest", type=Path, help="Reviewed local JSON replacements/deletions, applied in the same transaction")
    args = parser.parse_args()
    if args.validate_only:
        from knowledge_graph_validation import validate_branches
        print(json.dumps(validate_branches(load_branches(args.data_dir))))
        return

    attach_knowledge_from_dir(args.data_dir, uri=args.uri, reconcile_manifest=args.reconcile_manifest)


if __name__ == "__main__":
    main()

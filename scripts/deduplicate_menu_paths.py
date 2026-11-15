"""Объединение опечаток и вариаций окончаний в путях меню (Entity Resolution)."""

from __future__ import annotations

import logging
import re
from neo4j import GraphDatabase

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("dedup_menu")


def normalize_path(path: str) -> str:
    p = path.strip()
    p = re.sub(r"Управление запас[ы|ов]", "Управление запасами", p)
    p = re.sub(r"Учет в отделения\b", "Учет в отделении", p)
    p = re.sub(r"\s*>\s*", " > ", p)
    return p


def deduplicate_menus(memgraph_uri: str = "bolt://127.0.0.1:7687"):
    driver = GraphDatabase.driver(memgraph_uri, auth=("", ""))

    with driver.session() as s:
        paths = [r["p"] for r in s.run("MATCH (m:MenuPath) RETURN m.full_path AS p;").data()]
        logger.info(f"Всего MenuPath: {len(paths)}")

        merged = 0
        for p in paths:
            norm = normalize_path(p)
            if norm != p:
                logger.info(f"Слияние синонима: '{p}' -> '{norm}'")
                # 1. Создаем или находим канонический узел
                s.run(
                    """
                    MERGE (target:MenuPath:Entity {full_path: $norm})
                    ON CREATE SET target.name = $norm, target.type = 'MenuPath'
                    """,
                    norm=norm,
                )

                # 2. Переносим исходящие связи
                s.run(
                    """
                    MATCH (source:MenuPath {full_path: $orig})-[r]->(other)
                    MATCH (target:MenuPath {full_path: $norm})
                    WHERE id(source) <> id(target)
                    MERGE (target)-[r2:CONTAINS_ACTION]->(other)
                    DELETE r;
                    """,
                    orig=p,
                    norm=norm,
                )

                # 3. Переносим входящие связи
                s.run(
                    """
                    MATCH (other)-[r]->(source:MenuPath {full_path: $orig})
                    MATCH (target:MenuPath {full_path: $norm})
                    WHERE id(source) <> id(target)
                    MERGE (other)-[r2:HAS_MENU]->(target)
                    DELETE r;
                    """,
                    orig=p,
                    norm=norm,
                )

                # 4. Переносим связи DOCUMENTED_BY
                s.run(
                    """
                    MATCH (source:MenuPath {full_path: $orig})-[r:DOCUMENTED_BY]->(d:Document)
                    MATCH (target:MenuPath {full_path: $norm})
                    WHERE id(source) <> id(target)
                    MERGE (target)-[:DOCUMENTED_BY]->(d)
                    DELETE r;
                    """,
                    orig=p,
                    norm=norm,
                )

                # 5. Удаляем узел-синоним
                s.run(
                    """
                    MATCH (source:MenuPath {full_path: $orig})
                    OPTIONAL MATCH (source)-[r]-()
                    WITH source, count(r) AS degree
                    WHERE degree = 0
                    DELETE source;
                    """,
                    orig=p,
                )
                merged += 1

        logger.info(f"Слито вариаций путей: {merged}")
        s.run("CREATE SNAPSHOT;")
        logger.info("Снапшот обновлен на диск.")

    driver.close()


if __name__ == "__main__":
    deduplicate_menus()

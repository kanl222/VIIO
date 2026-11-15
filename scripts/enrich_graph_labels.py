"""Скрипт гармонизации графа Memgraph: унификация Entity и канонических меток онтологии."""

from __future__ import annotations

import logging
from neo4j import GraphDatabase

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("enrich_graph")


def harmonize(memgraph_uri: str = "bolt://127.0.0.1:7687"):
    driver = GraphDatabase.driver(memgraph_uri, auth=("", ""))

    with driver.session() as s:
        # 1. Проверяем общее число узлов
        res = s.run("MATCH (n) RETURN count(n) AS c;").single()
        logger.info(f"Начальное число узлов: {res['c']}")

        # 2. Переносим связи DOCUMENTED_BY от isolated MenuPath к Entity
        logger.info("Перенос связей DOCUMENTED_BY к узлам Entity...")
        s.run(
            """
            MATCH (m:MenuPath)-[r:DOCUMENTED_BY]->(d:Document)
            WHERE NOT m:Entity
            MERGE (e:Entity {name: m.full_path})
            ON CREATE SET e.type = 'MenuPath', e.full_path = m.full_path
            MERGE (e)-[:DOCUMENTED_BY]->(d)
            DELETE r;
            """
        )

        # 3. Удаляем изолированные узлы без связей, дублирующие онтологию
        logger.info("Удаление изолированных узлов без связей...")
        for label in ["System", "Operation", "Condition", "Role", "DrugCategory", "MenuPath"]:
            query = f"""
                MATCH (n:{label})
                WHERE NOT n:Entity
                OPTIONAL MATCH (n)-[r]-()
                WITH n, count(r) AS edges
                WHERE edges = 0
                DELETE n;
            """
            s.run(query)

        # 4. Проставляем канонические метки онтологии на узлы Entity
        logger.info("Присвоение канонических меток узлам Entity...")
        s.run("MATCH (e:Entity) WHERE e.type = 'System' SET e:System;")
        s.run("MATCH (e:Entity) WHERE e.type = 'Operation' SET e:Operation;")
        s.run("MATCH (e:Entity) WHERE e.type = 'Condition' SET e:Condition, e.rule_text = e.name;")
        s.run("MATCH (e:Entity) WHERE e.type = 'Role' SET e:Role, e.title = e.name;")
        s.run("MATCH (e:Entity) WHERE e.type = 'DrugCategory' SET e:DrugCategory;")
        s.run("MATCH (e:Entity) WHERE e.type = 'MenuPath' SET e:MenuPath, e.full_path = e.name;")

        # 5. Дополнительная разметка по типам связей
        logger.info("Разметка смежных узлов по типам связей...")
        s.run("MATCH ()-[:APPLIES_TO]->(t:Entity) SET t:DrugCategory, t.type = 'DrugCategory';")
        s.run("MATCH ()-[:EXECUTED_BY]->(t:Entity) SET t:Role, t.type = 'Role', t.title = t.name;")
        s.run("MATCH ()-[:REQUIRES_CONDITION]->(t:Entity) SET t:Condition, t.type = 'Condition', t.rule_text = t.name;")
        s.run("MATCH ()-[:CONTAINS_ACTION]->(t:Entity) SET t:Operation, t.type = 'Operation';")
        s.run("MATCH ()-[:NEXT_STEP]->(t:Entity) SET t:Operation, t.type = 'Operation';")
        s.run("MATCH (s:Entity)-[:HAS_MENU]->() SET s:System, s.type = 'System';")
        s.run("MATCH ()-[:HAS_MENU]->(m:Entity) SET m:MenuPath, m.type = 'MenuPath', m.full_path = m.name;")

        # 6. Финальная статистика
        res_nodes = s.run("MATCH (n) RETURN count(n) AS c;").single()
        res_edges = s.run("MATCH ()-[r]->() RETURN count(r) AS c;").single()
        logger.info(f"Итого узлов: {res_nodes['c']}, связей: {res_edges['c']}")

        print("\nРаспределение узлов по меткам:")
        labels_info = s.run(
            """
            MATCH (n)
            UNWIND labels(n) AS l
            WITH l
            WHERE l <> 'Entity'
            RETURN l AS Метка, count(*) AS Количество
            ORDER BY Количество DESC;
            """
        )
        for row in labels_info:
            print(f"  {row['Метка']:15}: {row['Количество']}")

    driver.close()
    logger.info("Гармонизация успешно завершена!")


if __name__ == "__main__":
    harmonize()

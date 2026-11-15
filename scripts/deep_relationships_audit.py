"""Скрипт глубокого аудита связей, семантики и целостности данных в графе Memgraph."""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from neo4j import GraphDatabase

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("deep_audit")


def run_deep_audit(memgraph_uri: str = "bolt://127.0.0.1:7687"):
    driver = GraphDatabase.driver(memgraph_uri, auth=("", ""))

    with driver.session() as s:
        print("\n=================================================================")
        print("    ГЛУБОКИЙ АУДИТ СВЯЗЕЙ И ЦЕЛОСТНОСТИ ДАННЫХ В MEMGRAPH")
        print("=================================================================")

        # -------------------------------------------------------------
        # 1. Сводка по типам связей и сигнатурам (SourceLabel -> Rel -> TargetLabel)
        # -------------------------------------------------------------
        print("\n--- 1. СИГНАТУРЫ И РАСПРЕДЕЛЕНИЕ СВЯЗЕЙ ---")
        rel_signatures = s.run(
            """
            MATCH (a)-[r]->(b)
            WITH labels(a) AS la, type(r) AS t, labels(b) AS lb
            RETURN la, t, lb, count(*) AS c
            ORDER BY c DESC;
            """
        ).data()

        rel_type_counts = Counter()
        for row in rel_signatures:
            la_filtered = [l for l in row["la"] if l != "Entity"] or ["Entity"]
            lb_filtered = [l for l in row["lb"] if l != "Entity"] or ["Entity"]
            src = ":".join(la_filtered)
            tgt = ":".join(lb_filtered)
            rel = row["t"]
            cnt = row["c"]
            rel_type_counts[rel] += cnt
            print(f"  (:{src:12}) -[:{rel:18}]-> (:{tgt:12}) : {cnt:4d}")

        print(f"\nИтого связей: {sum(rel_type_counts.values())}")

        # -------------------------------------------------------------
        # 2. Проверка петель на себя (Self-loops: a -> a)
        # -------------------------------------------------------------
        print("\n--- 2. ПРОВЕРКА ПЕТЕЛЬ (Self-loops: узел ссылается сам на себя) ---")
        self_loops = s.run(
            """
            MATCH (a)-[r]->(a)
            RETURN id(a) AS id, labels(a) AS l, properties(a) AS p, type(r) AS t;
            """
        ).data()
        if self_loops:
            print(f"  [X] Найдено петель на себя: {len(self_loops)}")
            for sl in self_loops[:5]:
                name = sl["p"].get("name") or sl["p"].get("title") or sl["p"].get("full_path")
                print(f"      Узел '{name}' со связью [:{sl['t']}]")
        else:
            print("  [OK] Петель на себя не обнаружено (0).")

        # -------------------------------------------------------------
        # 3. Проверка дубликатов связей между одинаковыми узлами
        # -------------------------------------------------------------
        print("\n--- 3. МУЛЬТИ-СВЯЗИ (дубликаты одинакового типа между парой узлов) ---")
        dup_rels = s.run(
            """
            MATCH (a)-[r]->(b)
            WITH a, b, type(r) AS t, count(r) AS cnt
            WHERE cnt > 1
            RETURN a.name AS a_name, t, b.name AS b_name, cnt
            LIMIT 10;
            """
        ).data()
        if dup_rels:
            print(f"  [!] Пар узлов с дублирующимися связями одного типа: {len(dup_rels)}")
            for d in dup_rels[:5]:
                print(f"      '{d['a_name']}' -[:{d['t']}]x{d['cnt']}-> '{d['b_name']}'")
        else:
            print("  [OK] Все связи между парами узлов уникальны.")

        # -------------------------------------------------------------
        # 4. Проверка полноты обязательных свойств узлов
        # -------------------------------------------------------------
        print("\n--- 4. ПОЛНОТА ОБЯЗАТЕЛЬНЫХ ПОЛЕЙ УЗЛОВ ---")
        # Document.id, Document.title
        doc_nulls = s.run(
            "MATCH (d:Document) WHERE d.id IS NULL OR d.title IS NULL OR trim(d.title) = '' RETURN count(d) AS c;"
        ).single()["c"]
        # MenuPath.full_path
        menu_nulls = s.run(
            "MATCH (m:MenuPath) WHERE m.full_path IS NULL OR trim(m.full_path) = '' RETURN count(m) AS c;"
        ).single()["c"]
        # Operation.name
        op_nulls = s.run(
            "MATCH (o:Operation) WHERE o.name IS NULL OR trim(o.name) = '' RETURN count(o) AS c;"
        ).single()["c"]
        # System.name
        sys_nulls = s.run(
            "MATCH (sys:System) WHERE sys.name IS NULL OR trim(sys.name) = '' RETURN count(sys) AS c;"
        ).single()["c"]

        print(f"  Document (id/title null): {doc_nulls} {'[OK]' if doc_nulls == 0 else '[X]'}")
        print(f"  MenuPath (full_path null): {menu_nulls} {'[OK]' if menu_nulls == 0 else '[X]'}")
        print(f"  Operation (name null):    {op_nulls} {'[OK]' if op_nulls == 0 else '[X]'}")
        print(f"  System (name null):       {sys_nulls} {'[OK]' if sys_nulls == 0 else '[X]'}")

        # -------------------------------------------------------------
        # 5. Проверка связности документов с интерфейсом и операциями
        # -------------------------------------------------------------
        print("\n--- 5. СВЯЗНОСТЬ ДОКУМЕНТОВ (Document -> MenuPath / Entities) ---")
        doc_coverage = s.run(
            """
            MATCH (d:Document)
            OPTIONAL MATCH (d)<-[:DOCUMENTED_BY]-(m:MenuPath)
            OPTIONAL MATCH (d)-[:MENTIONS]->(e:Entity)
            WITH d, count(DISTINCT m) AS menu_cnt, count(DISTINCT e) AS ent_cnt
            RETURN d.id AS id, d.title AS title, menu_cnt, ent_cnt
            ORDER BY menu_cnt ASC, ent_cnt ASC;
            """
        ).data()

        zero_menu_docs = [d for d in doc_coverage if d["menu_cnt"] == 0]
        zero_ent_docs = [d for d in doc_coverage if d["ent_cnt"] == 0]
        rich_docs = [d for d in doc_coverage if d["menu_cnt"] > 0 and d["ent_cnt"] > 0]

        print(f"  Всего документов: {len(doc_coverage)}")
        print(f"  Документов с пунктами меню и сущностями: {len(rich_docs)}")
        print(f"  Документов без прямых пунктов меню: {len(zero_menu_docs)}")
        print(f"  Документов без сущностей (изолированных): {len(zero_ent_docs)}")

        if zero_menu_docs:
            print("  Примеры документов без структуры меню (чисто нормативные/приказы):")
            for d in zero_menu_docs[:5]:
                print(f"    - '{d['title']}' (сущностей: {d['ent_cnt']})")

        # -------------------------------------------------------------
        # 6. Проверка сквозных бизнес-маршрутов
        # -------------------------------------------------------------
        print("\n--- 6. СКВОЗНЫЕ МАРШРУТЫ (System -> MenuPath -> Operation -> Condition) ---")
        paths_count = s.run(
            """
            MATCH (s:System)-[:HAS_MENU]->(m:MenuPath)-[:CONTAINS_ACTION]->(o:Operation)-[:REQUIRES_CONDITION]->(c:Condition)
            RETURN count(*) AS c;
            """
        ).single()["c"]
        print(f"  Полных цепочек (Система -> Меню -> Действие -> Условие): {paths_count}")

        roles_linked = s.run(
            """
            MATCH (o:Operation)-[:EXECUTED_BY]->(r:Role)
            RETURN count(DISTINCT o) AS op_count, count(DISTINCT r) AS role_count;
            """
        ).single()
        print(f"  Операций с назначенными ролями: {roles_linked['op_count']} (ролей задействовано: {roles_linked['role_count']})")

        drug_linked = s.run(
            """
            MATCH (o:Operation)-[:APPLIES_TO]->(dc:DrugCategory)
            RETURN count(DISTINCT o) AS op_count, count(DISTINCT dc) AS cat_count;
            """
        ).single()
        print(f"  Операций со спецификой по категориям препаратов: {drug_linked['op_count']} (категорий: {drug_linked['cat_count']})")

        next_steps = s.run(
            """
            MATCH (o1:Operation)-[:NEXT_STEP]->(o2:Operation)
            RETURN count(*) AS c;
            """
        ).single()["c"]
        print(f"  Последовательностей шагов (NEXT_STEP): {next_steps}")

    driver.close()


if __name__ == "__main__":
    run_deep_audit()

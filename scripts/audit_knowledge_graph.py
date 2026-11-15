"""Скрипт глубокого аудита и валидации узлов и связей графа Memgraph."""

from __future__ import annotations

import logging
from collections import defaultdict
from neo4j import GraphDatabase

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("graph_audit")


def audit_graph(memgraph_uri: str = "bolt://127.0.0.1:7687"):
    driver = GraphDatabase.driver(memgraph_uri, auth=("", ""))

    with driver.session() as s:
        # 1. Общие метрики
        total_nodes = s.run("MATCH (n) RETURN count(n) AS c;").single()["c"]
        total_edges = s.run("MATCH ()-[r]->() RETURN count(r) AS c;").single()["c"]
        print(f"\n==========================================")
        print(f"АУДИТ ГРАФА ЗНАНИЙ (Всего узлов: {total_nodes}, связей: {total_edges})")
        print(f"==========================================")

        # 2. Аудит документов (Document)
        docs = s.run("MATCH (d:Document) RETURN d.id AS id, d.title AS title;").data()
        print(f"\n--- 1. ДОКУМЕНТЫ ({len(docs)} шт.) ---")
        suspicious_docs = []
        for d in docs:
            t = d["title"].strip()
            # Подозрительные: начинаются с маленькой буквы, обрезанные, содержат странные символы
            if (
                t[0].islower()
                or len(t) < 5
                or t.startswith("та,")
                or t.startswith("анное")
                or t == "Cone"
            ):
                suspicious_docs.append(d)
                print(f"  [X] ПОДОЗРИТЕЛЬНЫЙ: id={d['id'][:8]} | '{t}'")
            else:
                pass
        print(f"  Корректных документов: {len(docs) - len(suspicious_docs)}/{len(docs)}")

        # 3. Аудит систем (System)
        systems = s.run("MATCH (s:System) RETURN s.name AS name;").data()
        print(f"\n--- 2. ИНФОРМАЦИОННЫЕ СИСТЕМЫ ({len(systems)} шт.) ---")
        suspicious_systems = []
        for sys in systems:
            name = sys["name"].strip()
            if len(name) > 80 or name.startswith("Реализована") or name.startswith("Министерство"):
                suspicious_systems.append(name)
                print(f"  [!] Нуждается в очистке: '{name[:70]}...'")
            else:
                print(f"  [OK] '{name}'")

        # 4. Аудит MenuPath
        menus = s.run("MATCH (m:MenuPath) RETURN m.name AS name, m.full_path AS full_path;").data()
        print(f"\n--- 3. ПУТИ МЕНЮ ({len(menus)} шт.) ---")
        hierarchical_menus = [m for m in menus if ">" in (m["full_path"] or m["name"] or "")]
        single_item_menus = [m for m in menus if ">" not in (m["full_path"] or m["name"] or "")]
        print(f"  Иерархических путей (с '>'): {len(hierarchical_menus)}")
        print(f"  Простых/одиночных пунктов: {len(single_item_menus)}")
        print("  Примеры корректных путей меню:")
        for m in hierarchical_menus[:5]:
            print(f"    - '{m['full_path']}'")

        # 5. Аудит операций (Operation)
        ops = s.run("MATCH (o:Operation) RETURN o.name AS name;").data()
        print(f"\n--- 4. ОПЕРАЦИИ / ДЕЙСТВИЯ ({len(ops)} шт.) ---")
        empty_ops = [o for o in ops if not o["name"] or len(o["name"].strip()) < 3]
        long_ops = [o for o in ops if len(o["name"]) > 100]
        normal_ops = len(ops) - len(empty_ops) - len(long_ops)
        print(f"  Корректных емких действий: {normal_ops}")
        print(f"  Слишком длинных (>100 симв): {len(long_ops)}")
        print(f"  Пустых / слишком коротких: {len(empty_ops)}")
        print("  Примеры канонических операций:")
        for o in ops[:5]:
            print(f"    - '{o['name']}'")

        # 6. Аудит условий (Condition)
        conds = s.run("MATCH (c:Condition) RETURN c.name AS name;").data()
        print(f"\n--- 5. УСЛОВИЯ / ПРАВИЛА ({len(conds)} шт.) ---")
        print(f"  Всего условий: {len(conds)}")
        print("  Примеры условий:")
        for c in conds[:5]:
            print(f"    - '{c['name']}'")

        # 7. Аудит ролей (Role)
        roles = s.run("MATCH (r:Role) RETURN r.name AS name;").data()
        print(f"\n--- 6. РОЛИ ({len(roles)} шт.) ---")
        for r in roles[:10]:
            print(f"  - '{r['name']}'")

        # 8. Аудит изолированных узлов (без рёбер)
        isolated = s.run(
            """
            MATCH (n)
            OPTIONAL MATCH (n)-[r]-()
            WITH n, count(r) AS degree
            WHERE degree = 0
            RETURN count(n) AS c;
            """
        ).single()["c"]
        print(f"\n--- 7. СВЯЗНОСТЬ ГРАФА ---")
        print(f"  Изолированных узлов (без единой связи): {isolated}")

    driver.close()


if __name__ == "__main__":
    audit_graph()

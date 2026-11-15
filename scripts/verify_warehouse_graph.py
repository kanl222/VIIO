"""Комплексная валидация и аудит новой онтологической структуры графа в Memgraph."""

from __future__ import annotations

import logging
from collections import Counter
from neo4j import GraphDatabase

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("graph_verify")


def run_full_verification(uri: str = "bolt://127.0.0.1:7687"):
    driver = GraphDatabase.driver(uri, auth=("", ""))

    print("\n" + "=" * 70)
    print("      КОМПЛЕКСНАЯ ПРОВЕРКА ГРАФОВОЙ БАЗЫ ДАННЫХ (MEMGRAPH)      ")
    print("=" * 70)

    with driver.session() as s:
        # 1. Общие метрики
        total_nodes = s.run("MATCH (n) RETURN count(n) AS c;").single()["c"]
        total_edges = s.run("MATCH ()-[r]->() RETURN count(r) AS c;").single()["c"]
        print(f"\n[1] ОБЩИЕ МЕТРИКИ:")
        print(f"  • Всего узлов:   {total_nodes}")
        print(f"  • Всего связей:  {total_edges}")

        # 2. Распределение узлов по меткам
        label_res = s.run("MATCH (n) RETURN labels(n) AS lbls;").data()
        label_counts = Counter()
        for r in label_res:
            for lbl in r["lbls"]:
                label_counts[lbl] += 1

        print(f"\n[2] РАСПРЕДЕЛЕНИЕ УЗЛОВ ПО МЕТКАМ (LABELS):")
        core_labels = [
            "Organization", "Department", "Role",
            "Process", "Step", "Document", "Regulation",
            "Product", "Batch", "Location", "StorageCondition", "Category",
            "System", "DataEntity", "MenuPath", "Operation"
        ]
        for lbl in core_labels:
            cnt = label_counts.get(lbl, 0)
            status = "✓ OK" if cnt > 0 else "○ Нет данных"
            print(f"  • {lbl:<18}: {cnt:>5} узлов  [{status}]")

        # 3. Распределение связей по типам
        rel_res = s.run("MATCH ()-[r]->() RETURN type(r) AS t, count(r) AS c ORDER BY c DESC;").data()
        print(f"\n[3] РАСПРЕДЕЛЕНИЕ СВЯЗЕЙ ПО ТИПАМ (RELATIONSHIPS):")
        for r in rel_res[:15]:
            print(f"  • {r['t']:<22}: {r['c']:>5} связей")

        # 4. Проверка ограничений уникальности (Constraints & Integrity)
        print(f"\n[4] ТЕСТИРОВАНИЕ ОГРАНИЧЕНИЙ УНИКАЛЬНОСТИ:")
        constraints_to_test = [
            ("Department", "code", "WH"),
            ("System", "name", "WMS VIIO"),
            ("Regulation", "code", "GDP"),
            ("Product", "id", "PROD-001"),
            ("Batch", "id", "BATCH-2026-001"),
            ("Location", "code", "CHAMBER-COLD-01"),
            ("DataEntity", "name", "Остатки и статусы серий"),
        ]
        for lbl, prop, val in constraints_to_test:
            try:
                res = s.run(f"CREATE (:{lbl} {{{prop}: $val}});", val=val)
                res.consume()
                print(f"  [X] ОШИБКА: Ограничение {lbl}.{prop} НЕ сработало!")
            except Exception as e:
                msg = str(e).lower()
                if "constraint" in msg or "unique" in msg or "violation" in msg:
                    print(f"  [✓] {lbl}.{prop} уникален: дубликат корректно заблокирован СУБД")
                else:
                    print(f"  [?] {lbl}.{prop}: {e}")

        # 5. Проверка целостности сквозных цепочек (End-to-End Walk)
        print(f"\n[5] ВАЛИДАЦИЯ СКВОЗНЫХ ЦЕПОЧЕК ОНТОЛОГИИ:")

        # А. Цепочка процессов и шагов
        steps_chain = s.run(
            """
            MATCH (p:Process {name: 'Приёмка лекарственных препаратов'})-[:HAS_STEP]->(s:Step)
            OPTIONAL MATCH (s)-[:NEXT]->(next:Step)
            OPTIONAL MATCH (r:Role)-[:RESPONSIBLE_FOR]->(s)
            OPTIONAL MATCH (s)-[:EXECUTED_IN]->(sys:System)
            RETURN s.order AS order, s.name AS step, r.name AS role, sys.name AS system, next.name AS next
            ORDER BY s.order;
            """
        ).data()
        print(f"  А. Бизнес-процесс «Приёмка ЛП» ({len(steps_chain)} последовательных шага):")
        for st in steps_chain:
            role_part = f" -> Ответственный: {st['role']}" if st['role'] else ""
            sys_part = f" [в {st['system']}]" if st['system'] else ""
            print(f"     Шаг {st['order']}: {st['step']}{role_part}{sys_part}")

        # Б. Контроль соответствия температурного режима (Cold Chain Compliance)
        compliance_check = s.run(
            """
            MATCH (b:Batch)-[:OF_PRODUCT]->(p:Product)-[:REQUIRES]->(sc:StorageCondition),
                  (b)-[:STORED_IN]->(l:Location)
            OPTIONAL MATCH (l)-[:PROVIDES]->(l_sc:StorageCondition)
            RETURN b.number AS batch, p.name AS product, l.code AS loc,
                   sc.name AS req, l_sc.name AS prov,
                   (sc = l_sc) AS is_compliant;
            """
        ).data()
        print(f"\n  Б. Соответствие режимов хранения (Холодовая цепь):")
        for c in compliance_check:
            status_text = "✓ В РЕЖИМЕ" if c["is_compliant"] else "X НАРУШЕНИЕ РЕЖИМА"
            print(f"     Серия: {c['batch']} | Препарат: {c['product']}")
            print(f"     Локация: {c['loc']} (Предоставляет: {c['prov']})")
            print(f"     Требуется: {c['req']} -> [{status_text}]")

        # В. Мастер-данные информационных систем
        masters = s.run(
            """
            MATCH (sys:System)-[:SOURCE_OF_TRUTH_FOR]->(de:DataEntity)
            RETURN sys.name AS sys, collect(de.name) AS entities;
            """
        ).data()
        print(f"\n  В. Мастер-системы данных (Source of Truth):")
        for m in masters:
            print(f"     • {m['sys']}:")
            for ent in m["entities"]:
                print(f"       - {ent}")

        # Г. Нормативная привязка (Regulation Compliance)
        regs = s.run(
            """
            MATCH (r:Regulation)
            OPTIONAL MATCH (target)-[:COMPLIES_WITH]->(r)
            RETURN r.code AS code, r.title AS title, collect(coalesce(target.name, labels(target)[0])) AS targets;
            """
        ).data()
        print(f"\n  Г. Нормативная база (Compliance):")
        for r in regs:
            t_str = ", ".join(r["targets"]) if r["targets"] else "Общий стандарт"
            print(f"     • [{r['code']}] {r['title']} -> Связанные сущности: {t_str}")

        # 6. Проверка изолированных узлов новой онтологии
        orphan_check = s.run(
            """
            MATCH (n)
            WHERE (n:Process OR n:Product OR n:Batch OR n:Location OR n:StorageCondition OR n:System OR n:Department)
            OPTIONAL MATCH (n)-[r]-()
            WITH n, r
            WHERE r IS NULL
            RETURN labels(n)[0] AS lbl, coalesce(n.name, n.code, n.id) AS name;
            """
        ).data()
        print(f"\n[6] ПРОВЕРКА ИЗОЛИРОВАННЫХ УЗЛОВ НОВОЙ ОНТОЛОГИИ:")
        if orphan_check:
            print(f"  [!] Найдено {len(orphan_check)} изолированных узлов:")
            for o in orphan_check:
                print(f"      - ({o['lbl']}): {o['name']}")
        else:
            print("  [✓] 0 изолированных узлов: все сущности новой структуры связаны в топологию графа!")

    driver.close()
    print("\n" + "=" * 70)
    print("                 ПРОВЕРКА УСПЕШНО ЗАВЕРШЕНА                    ")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    run_full_verification()

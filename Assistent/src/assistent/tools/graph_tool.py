"""Инструмент поиска по графу знаний Memgraph для агента Assistent (Semantic GraphRAG).

Реализует поиск по онтологии аптечного склада:
- Процессы и шаги (Process -> Step, NEXT, RESPONSIBLE_FOR, EXECUTED_IN, COMPLIES_WITH)
- Товары, серии и режимы хранения (Product, Batch, StorageCondition, Location, Category)
- Системы и мастер-данные (System, DataEntity, Integration, Department)
- Нормативные требования (Regulation)
- Навигация по интерфейсу (MenuPath -> Operation)
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any
from collections import defaultdict

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from neo4j import GraphDatabase

from assistent.services.diagnostics import record

logger = logging.getLogger(__name__)

STOP_WORDS = {
    "в", "во", "на", "по", "с", "со", "к", "ко", "из", "изо", "от", "до", "для", "о", "об",
    "как", "где", "что", "кто", "чем", "кому", "какой", "какие", "какая", "какое",
    "ли", "же", "бы", "и", "или", "не", "лишь", "только", "найти", "подскажи", "покажи",
}


def extract_keywords(query: str) -> list[str]:
    """Извлекает значимые ключевые слова для поиска по графу."""
    words = re.findall(r"[а-яёa-z0-9]+", query.lower())
    meaningful = [w for w in words if w not in STOP_WORDS and len(w) >= 3]
    return meaningful if meaningful else words


class KnowledgeGraphClient:
    """Клиент для выполнения быстрых поисковых и структурных запросов к Memgraph."""

    def __init__(self, uri: str = "bolt://localhost:7687", user: str = "", password: str = ""):
        self.uri = uri
        self.auth = (user, password)
        self._driver = None

    def get_driver(self):
        if self._driver is None:
            self._driver = GraphDatabase.driver(self.uri, auth=self.auth)
        return self._driver

    def close(self):
        if self._driver:
            self._driver.close()
            self._driver = None

    def search_graph(self, query: str, limit: int = 8) -> dict[str, Any]:
        """Быстрый многоэтапный поиск по графу без декартовых произведений (< 50 мс)."""
        keywords = extract_keywords(query)
        if not keywords:
            return {
                "abbreviations": [],
                "processes": [],
                "storage_rules": [],
                "systems": [],
                "regulations": [],
                "employees": [],
                "menus": [],
                "operations": [],
                "sources": [],
            }

        driver = self.get_driver()
        with driver.session() as session:
            # 0. Поиск аббревиатур и терминов онтологии (Abbreviation)
            abbr_data = []
            tokens = [w for w in re.findall(r"[а-яёa-z0-9\-]+", query.lower()) if len(w) >= 2]
            if tokens:
                search_tokens = list(set(tokens) | {w.upper() for w in tokens} | {w.capitalize() for w in tokens})
                abbr_rows = session.run(
                    """
                    MATCH (a:Abbreviation)
                    WHERE a.name IN $tokens
                       OR (a.short_form IS NOT NULL AND a.short_form IN $tokens)
                       OR any(t IN $tokens WHERE a.name CONTAINS t OR a.full_name CONTAINS t)
                    OPTIONAL MATCH (a)-[:EXPANDS_TO|REFERS_TO]->(target)
                    RETURN a.name AS name, a.full_name AS full_name, a.short_form AS short_form,
                           a.description AS description, a.category AS category,
                           labels(target)[0] AS target_type, coalesce(target.name, target.title, target.code) AS target_name
                    LIMIT $limit;
                    """,
                    tokens=search_tokens,
                    limit=limit,
                ).data()
                grouped_abbr = {}
                for r in abbr_rows:
                    k = r["name"]
                    if k not in grouped_abbr:
                        grouped_abbr[k] = {
                            "name": r["name"],
                            "full_name": r["full_name"],
                            "short_form": r["short_form"],
                            "description": r["description"],
                            "category": r["category"],
                            "related": [],
                        }
                    if r.get("target_name"):
                        t_str = f"{r['target_type']}: {r['target_name']}" if r.get("target_type") else r["target_name"]
                        if t_str not in grouped_abbr[k]["related"]:
                            grouped_abbr[k]["related"].append(t_str)
                abbr_data = list(grouped_abbr.values())

            # 1. Поиск процессов и шагов (Process / Step)
            processes_data = []
            for kw in keywords[:2]:
                stem = kw[:5] if len(kw) >= 5 else kw
                p_rows = session.run(
                    """
                    MATCH (p:Process)
                    WHERE p.name CONTAINS $t_lower
                       OR p.name CONTAINS $t_title
                       OR p.name CONTAINS $t_upper
                    OPTIONAL MATCH (p)-[:HAS_STEP]->(s:Step)
                    OPTIONAL MATCH (s)-[:NEXT]->(next:Step)
                    OPTIONAL MATCH (r:Role)-[:RESPONSIBLE_FOR]->(s)
                    OPTIONAL MATCH (s)-[:EXECUTED_IN]->(sys:System)
                    OPTIONAL MATCH (p)-[:COMPLIES_WITH]->(reg:Regulation)
                    RETURN p.name AS process, p.version AS version, p.owner AS owner,
                           s.order AS step_order, coalesce(s.name, s.description) AS step_name,
                           s.description AS step_desc, next.name AS next_step,
                           r.name AS role, sys.name AS system, reg.code AS reg_code
                    ORDER BY p.name, s.order
                    LIMIT $limit;
                    """,
                    t_lower=stem.lower(),
                    t_title=stem.capitalize(),
                    t_upper=stem.upper(),
                    limit=limit * 2,
                ).data()

                by_proc = defaultdict(lambda: {"process": "", "owner": "", "steps": [], "regulations": []})
                for r in p_rows:
                    pname = r["process"]
                    by_proc[pname]["process"] = pname
                    by_proc[pname]["owner"] = r.get("owner")
                    if r.get("reg_code") and r["reg_code"] not in by_proc[pname]["regulations"]:
                        by_proc[pname]["regulations"].append(r["reg_code"])
                    if r.get("step_name"):
                        step_info = {
                            "order": r["step_order"],
                            "name": r["step_name"],
                            "desc": r.get("step_desc"),
                            "role": r.get("role"),
                            "system": r.get("system"),
                            "next": r.get("next_step"),
                        }
                        if not any(s["order"] == step_info["order"] for s in by_proc[pname]["steps"]):
                            by_proc[pname]["steps"].append(step_info)

                for item in by_proc.values():
                    processes_data.append(item)

            # 2. Поиск товаров, серий и режимов хранения (Product / Batch / StorageCondition)
            storage_data = []
            for kw in keywords[:2]:
                stem = kw[:5] if len(kw) >= 5 else kw
                st_rows = session.run(
                    """
                    MATCH (p:Product)
                    WHERE p.name CONTAINS $t_lower
                       OR p.name CONTAINS $t_title
                       OR p.name CONTAINS $t_upper
                       OR p.mnn CONTAINS $t_lower
                    OPTIONAL MATCH (p)-[:REQUIRES]->(sc:StorageCondition)
                    OPTIONAL MATCH (p)-[:BELONGS_TO]->(c:Category)
                    OPTIONAL MATCH (b:Batch)-[:OF_PRODUCT]->(p)
                    OPTIONAL MATCH (b)-[:STORED_IN]->(loc:Location)
                    OPTIONAL MATCH (loc)-[:PROVIDES]->(loc_sc:StorageCondition)
                    RETURN p.name AS product, p.mnn AS mnn, p.form AS form, p.dosage AS dosage,
                           sc.name AS required_condition, c.name AS category,
                           b.number AS batch_number, b.status AS batch_status, b.expiry_date AS expiry_date,
                           loc.code AS location, loc_sc.name AS provided_condition
                    LIMIT $limit;
                    """,
                    t_lower=stem.lower(),
                    t_title=stem.capitalize(),
                    t_upper=stem.upper(),
                    limit=limit,
                ).data()
                storage_data.extend(st_rows)

            # 3. Поиск информационных систем и мастер-данных (System / DataEntity / Integration)
            systems_data = []
            seen_systems = set()
            for kw in keywords[:3]:
                stem = kw[:5] if len(kw) >= 5 else kw
                sys_rows = session.run(
                    """
                    MATCH (sys:System)
                    OPTIONAL MATCH (sys)-[:SOURCE_OF_TRUTH_FOR]->(de:DataEntity)
                    WITH sys, collect(DISTINCT de.name) AS master_data
                    WHERE sys.name CONTAINS $t_lower
                       OR sys.name CONTAINS $t_title
                       OR sys.name CONTAINS $t_upper
                       OR sys.type CONTAINS $t_upper
                       OR any(m IN master_data WHERE m CONTAINS $t_lower OR m CONTAINS $t_title)
                    OPTIONAL MATCH (sys)-[:INTEGRATES_WITH]->(other:System)
                    OPTIONAL MATCH (dep:Department)-[:USES_SYSTEM]->(sys)
                    RETURN sys.name AS system, sys.type AS type, sys.criticality AS criticality,
                           master_data,
                           collect(DISTINCT other.name) AS integrations,
                           collect(DISTINCT dep.name) AS departments
                    LIMIT $limit;
                    """,
                    t_lower=stem.lower(),
                    t_title=stem.capitalize(),
                    t_upper=stem.upper(),
                    limit=limit,
                ).data()
                for r in sys_rows:
                    if r["system"] not in seen_systems:
                        seen_systems.add(r["system"])
                        systems_data.append(r)

            # 4. Поиск нормативных требований (Regulation)
            reg_data = []
            for kw in keywords[:2]:
                stem = kw[:5] if len(kw) >= 5 else kw
                r_rows = session.run(
                    """
                    MATCH (r:Regulation)
                    WHERE r.code CONTAINS $t_upper
                       OR r.title CONTAINS $t_lower
                       OR r.title CONTAINS $t_title
                    OPTIONAL MATCH (entity)-[:COMPLIES_WITH]->(r)
                    RETURN r.code AS code, r.title AS title, r.source AS source, r.edition AS edition,
                           collect(DISTINCT coalesce(entity.name, labels(entity)[0])) AS complies_entities
                    LIMIT $limit;
                    """,
                    t_lower=stem.lower(),
                    t_title=stem.capitalize(),
                    t_upper=stem.upper(),
                    limit=limit,
                ).data()
                reg_data.extend(r_rows)

            # 4.1. Поиск сотрудников, руководства и отделов (Employee / Department / Organization)
            emp_data = []
            seen_emps = set()
            for kw in keywords[:3]:
                stem = kw[:5] if len(kw) >= 5 else kw
                e_rows = session.run(
                    """
                    MATCH (e:Employee)
                    OPTIONAL MATCH (e)-[:WORKS_IN]->(dep:Department)
                    WITH e, dep
                    WHERE e.name CONTAINS $t_lower
                       OR e.name CONTAINS $t_title
                       OR e.name CONTAINS $t_upper
                       OR (e.position IS NOT NULL AND (e.position CONTAINS $t_lower OR e.position CONTAINS $t_title))
                       OR (dep.name IS NOT NULL AND (dep.name CONTAINS $t_lower OR dep.name CONTAINS $t_title))
                    OPTIONAL MATCH (org:Organization)-[:OWNS]->(dep)
                    RETURN e.name AS name, e.position AS position, e.phone AS phone,
                           e.support_phones AS support_phones, e.org AS org,
                           dep.name AS department, dep.code AS dept_code
                    LIMIT $limit;
                    """,
                    t_lower=stem.lower(),
                    t_title=stem.capitalize(),
                    t_upper=stem.upper(),
                    limit=limit,
                ).data()
                for r in e_rows:
                    if r["name"] not in seen_emps:
                        seen_emps.add(r["name"])
                        emp_data.append(r)

            # 5. Поиск разделов меню (MenuPath)
            matched_menus = {}
            for kw in keywords[:2]:
                stem = kw[:5] if len(kw) >= 5 else kw
                rows = session.run(
                    """
                    MATCH (m:MenuPath)
                    WHERE m.full_path CONTAINS $t_lower
                       OR m.full_path CONTAINS $t_title
                       OR m.full_path CONTAINS $t_upper
                    RETURN id(m) AS id, m.full_path AS menu
                    LIMIT $limit;
                    """,
                    t_lower=stem.lower(),
                    t_title=stem.capitalize(),
                    t_upper=stem.upper(),
                    limit=limit,
                ).data()
                for r in rows:
                    matched_menus[r["id"]] = r["menu"]

            menus_data = []
            menu_ids = list(matched_menus.keys())
            if menu_ids:
                sys_rows = session.run(
                    """
                    MATCH (s:System)-[:HAS_MENU]->(m:MenuPath)
                    WHERE id(m) IN $ids
                    RETURN id(m) AS mid, s.name AS sys;
                    """,
                    ids=menu_ids,
                ).data()
                sys_by_menu = defaultdict(list)
                for r in sys_rows:
                    sys_by_menu[r["mid"]].append(r["sys"])

                doc_rows = session.run(
                    """
                    MATCH (m:MenuPath)-[:DOCUMENTED_BY]->(d:Document)
                    WHERE id(m) IN $ids
                    RETURN id(m) AS mid, d.id AS doc_id, d.title AS doc_title;
                    """,
                    ids=menu_ids,
                ).data()
                docs_by_menu = defaultdict(list)
                for r in doc_rows:
                    docs_by_menu[r["mid"]].append({"id": r["doc_id"], "title": r["doc_title"]})

                act_rows = session.run(
                    """
                    MATCH (m:MenuPath)-[:CONTAINS_ACTION]->(op:Operation)
                    WHERE id(m) IN $ids
                    RETURN id(m) AS mid, op.name AS action;
                    """,
                    ids=menu_ids,
                ).data()
                acts_by_menu = defaultdict(list)
                for r in act_rows:
                    acts_by_menu[r["mid"]].append(r["action"])

                for mid, mpath in matched_menus.items():
                    menus_data.append({
                        "menu": mpath,
                        "systems": list(set(sys_by_menu[mid])),
                        "docs": docs_by_menu[mid],
                        "actions": list(set(acts_by_menu[mid]))[:6],
                    })

            # 6. Поиск операций (Operation)
            matched_ops = {}
            for kw in keywords[:2]:
                stem = kw[:5] if len(kw) >= 5 else kw
                rows = session.run(
                    """
                    MATCH (op:Operation)
                    WHERE op.name CONTAINS $t_lower
                       OR op.name CONTAINS $t_title
                       OR op.name CONTAINS $t_upper
                    RETURN id(op) AS id, op.name AS name
                    LIMIT $limit;
                    """,
                    t_lower=stem.lower(),
                    t_title=stem.capitalize(),
                    t_upper=stem.upper(),
                    limit=limit,
                ).data()
                for r in rows:
                    matched_ops[r["id"]] = r["name"]

            ops_data = []
            op_ids = list(matched_ops.keys())
            if op_ids:
                op_menu_rows = session.run(
                    """
                    MATCH (m:MenuPath)-[:CONTAINS_ACTION]->(op:Operation)
                    WHERE id(op) IN $ids
                    RETURN id(op) AS op_id, m.full_path AS menu;
                    """,
                    ids=op_ids,
                ).data()
                menus_by_op = defaultdict(list)
                for r in op_menu_rows:
                    menus_by_op[r["op_id"]].append(r["menu"])

                rel_rows = session.run(
                    """
                    MATCH (op:Operation)-[r]->(target)
                    WHERE id(op) IN $ids
                    RETURN id(op) AS op_id, type(r) AS rel, labels(target)[0] AS t_label, target.name AS t_name;
                    """,
                    ids=op_ids,
                ).data()

                conds_by_op = defaultdict(list)
                roles_by_op = defaultdict(list)
                next_by_op = defaultdict(list)
                drugs_by_op = defaultdict(list)

                for r in rel_rows:
                    op_id = r["op_id"]
                    rel = r["rel"]
                    name = r["t_name"]
                    if not name:
                        continue
                    if rel in ("REQUIRES_CONDITION", "REQUIRES"):
                        conds_by_op[op_id].append(name)
                    elif rel in ("EXECUTED_BY", "RESPONSIBLE_FOR"):
                        roles_by_op[op_id].append(name)
                    elif rel in ("NEXT_STEP", "NEXT"):
                        next_by_op[op_id].append(name)
                    elif rel in ("APPLIES_TO", "BELONGS_TO"):
                        drugs_by_op[op_id].append(name)

                for op_id, op_name in matched_ops.items():
                    ops_data.append({
                        "operation": op_name,
                        "menus": list(set(menus_by_op[op_id]))[:3],
                        "conditions": list(set(conds_by_op[op_id]))[:4],
                        "roles": list(set(roles_by_op[op_id]))[:3],
                        "next_steps": list(set(next_by_op[op_id]))[:3],
                        "drug_categories": list(set(drugs_by_op[op_id]))[:3],
                    })

            # Сбор документов-источников
            sources = {}
            for m in menus_data:
                for doc in m.get("docs", []):
                    if doc and doc.get("id"):
                        sources[doc["id"]] = doc

            return {
                "abbreviations": abbr_data,
                "processes": processes_data,
                "storage_rules": storage_data,
                "systems": systems_data,
                "regulations": reg_data,
                "employees": emp_data,
                "menus": menus_data,
                "operations": ops_data,
                "sources": list(sources.values()),
            }


def format_graph_context(data: dict[str, Any]) -> str:
    """Форматирует факты из графа знаний в структурированный текст для LLM-агента."""
    abbreviations = data.get("abbreviations", [])
    processes = data.get("processes", [])
    storage_rules = data.get("storage_rules", [])
    systems = data.get("systems", [])
    regulations = data.get("regulations", [])
    employees = data.get("employees", [])
    menus = data.get("menus", [])
    operations = data.get("operations", [])
    sources = data.get("sources", [])

    has_data = any([abbreviations, processes, storage_rules, systems, regulations, employees, menus, operations])
    if not has_data:
        return "В графе знаний аптечного склада не найдено прямых соответствий по запросу."

    blocks = ["### Факты из графа знаний аптечного склада (Memgraph Ontology)\n"]

    # 0. Аббревиатуры и термины онтологии
    if abbreviations:
        blocks.append("#### Расшифровка аббревиатур и терминов (Abbreviation / Acronym):")
        for a in abbreviations[:5]:
            short_s = f" ({a['short_form']})" if a.get("short_form") and a["short_form"] != a["name"] else ""
            cat_s = f" [{a['category']}]" if a.get("category") else ""
            blocks.append(f"- **{a['name']}**{short_s}: **{a['full_name']}**{cat_s}")
            if a.get("description"):
                blocks.append(f"  - Назначение: {a['description']}")
            if a.get("related"):
                blocks.append(f"  - Связанные сущности: {', '.join(a['related'])}")
        blocks.append("")

    # 1. Процессы и шаги алгоритмов
    if processes:
        blocks.append("#### Бизнес-процессы и шаги алгоритмов (Process / Step):")
        for p in processes[:4]:
            owner_str = f" (Владелец: {p['owner']})" if p.get("owner") else ""
            regs_str = f" [Норматив: {', '.join(p['regulations'])}]" if p.get("regulations") else ""
            blocks.append(f"- **Процесс: «{p['process']}»**{owner_str}{regs_str}")
            sorted_steps = sorted(p.get("steps", []), key=lambda s: s.get("order", 0))
            for st in sorted_steps:
                role_str = f" | Ответственный: {st['role']}" if st.get("role") else ""
                sys_str = f" | Система: {st['system']}" if st.get("system") else ""
                blocks.append(f"  {st['order']}. **{st['name']}**{role_str}{sys_str}")
                if st.get("desc") and st["desc"] != st["name"]:
                    blocks.append(f"     Описание: {st['desc']}")
        blocks.append("")

    # 2. Товары, серии и режимы хранения
    if storage_rules:
        blocks.append("#### Режимы хранения, серии и локации (Product / StorageCondition):")
        for row in storage_rules[:5]:
            p_name = row.get("product") or "Препарат"
            req_c = row.get("required_condition") or "Не указан"
            cat = f" (Категория: {row['category']})" if row.get("category") else ""
            blocks.append(f"- **Препарат:** **«{p_name}»**{cat}")
            blocks.append(f"  - Требуемый режим хранения: `{req_c}`")
            if row.get("batch_number"):
                loc_code = row.get("location") or "Не размещена"
                prov_c = row.get("provided_condition") or "Не указан"
                status = row.get("batch_status") or "годен"
                is_match = "СООТВЕТСТВУЕТ" if req_c == prov_c else "ВНИМАНИЕ: НЕСООТВЕТСТВИЕ РЕЖИМА"
                blocks.append(f"  - Серия: `{row['batch_number']}` (Статус: *{status}*)")
                blocks.append(f"  - Локация хранения: `{loc_code}` (Режим зоны: `{prov_c}`) -> [{is_match}]")
        blocks.append("")

    # 3. Информационные системы и мастер-данные
    if systems:
        blocks.append("#### Информационные системы и интеграции (System / DataEntity):")
        for s in systems[:4]:
            s_type = f" [{s.get('type')}]" if s.get("type") else ""
            crit = f" (Критичность: {s.get('criticality')})" if s.get("criticality") else ""
            blocks.append(f"- **Система: «{s.get('system')}»**{s_type}{crit}")
            if s.get("master_data"):
                blocks.append(f"  - Мастер-система для данных: {', '.join(s['master_data'])}")
            if s.get("integrations"):
                blocks.append(f"  - Интеграция с системами: {', '.join(s['integrations'])}")
            if s.get("departments"):
                blocks.append(f"  - Используется отделами: {', '.join(s['departments'])}")
        blocks.append("")

    # 4. Нормативная база
    if regulations:
        blocks.append("#### Нормативные требования (Regulation):")
        for r in regulations[:4]:
            blocks.append(f"- **[{r.get('code')}]** «{r.get('title')}» (Источник: *{r.get('source')}*, ред. {r.get('edition')})")
            if r.get("complies_entities"):
                blocks.append(f"  - Применяется к: {', '.join(r['complies_entities'][:5])}")
        blocks.append("")

    # 4.1. Должностные лица, контакты и отделы
    if employees:
        blocks.append("#### Сотрудники, руководство и контакты (Employee / Department):")
        for e in employees[:6]:
            pos = f" — {e['position']}" if e.get("position") else ""
            dept = f" ({e['department']})" if e.get("department") else ""
            phone = f" | Тел: {e['phone']}" if e.get("phone") else ""
            sup = f" | Доп. линии: {e['support_phones']}" if e.get("support_phones") else ""
            blocks.append(f"- **{e['name']}**{pos}{dept}{phone}{sup}")
        blocks.append("")

    # 5. Разделы меню и операции (интерфейс)
    if menus:
        blocks.append("#### Разделы и пути меню программы (MenuPath):")
        for m in menus[:5]:
            systems_str = ", ".join(m["systems"]) if m.get("systems") else "Информационная система"
            blocks.append(f"- **Путь в меню:** `{m['menu']}` (Система: *{systems_str}*)")
            if m.get("actions"):
                blocks.append(f"  - Доступные действия: {', '.join(m['actions'][:5])}")
            docs = [d["title"] for d in m.get("docs", []) if d and d.get("title")]
            if docs:
                blocks.append(f"  - Регламентирующий документ: *{docs[0]}*")
        blocks.append("")

    if operations:
        blocks.append("#### Операции, действия и условия (Operation):")
        for op in operations[:5]:
            blocks.append(f"- **Действие:** **«{op['operation']}»**")
            if op.get("menus"):
                blocks.append(f"  - Меню: `{op['menus'][0]}`")
            if op.get("roles"):
                blocks.append(f"  - Роль: {', '.join(op['roles'])}")
            if op.get("conditions"):
                blocks.append(f"  - Условия: {'; '.join(op['conditions'])}")
            if op.get("next_steps"):
                blocks.append(f"  - Следующий шаг: {', '.join(op['next_steps'])}")
        blocks.append("")

    if sources:
        blocks.append("#### Документы-первоисточники:")
        for doc in sources[:5]:
            blocks.append(f"- *{doc['title']}* (ID: `{doc['id'][:8]}`)")

    return "\n".join(blocks)


def create_graph_search_tool(settings: Any):
    """Фабрика инструмента search_knowledge_graph для агента."""
    uri = getattr(settings, "memgraph_uri", "bolt://localhost:7687")
    user = getattr(settings, "memgraph_user", "")
    password = getattr(settings, "memgraph_password", "")
    client = KnowledgeGraphClient(uri=uri, user=user, password=password)

    @tool("search_knowledge_graph", response_format="content_and_artifact")
    async def search_knowledge_graph(
        query: str,
        config: RunnableConfig | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Поиск по графу знаний онтологии аптечного склада и информационных систем.

        Используйте этот инструмент, когда пользователь спрашивает:
        - Об аббревиатурах, сокращениях и терминах онтологии ("что такое ИСЛО...", "что значит МДЛП...", "расшифровка ЖНВЛП...", "что такое GDP...")
        - О бизнес-процессах и шагах алгоритмов ("какие шаги приёмки...", "алгоритм инвентаризации...", "последовательность действий")
        - О режимах хранения, сериях и локациях ("какой режим хранения у Гам-КОВИД-Вак...", "где хранится серия...", "соответствует ли режим зоны температуре препарата")
        - О нормативных актах и правилах ("требования GDP...", "правила хранения иммунобиологических...", "МДЛП постановление 1556")
        - Об информационных системах и мастер-данных ("в какой системе ведутся остатки...", "мастер-система для номенклатуры...", "с кем интегрирована WMS")
        - О должностях, ролях и ответственности ("кто отвечает за входной контроль...", "роль фармацевта-контролёра")
        - О навигации по интерфейсу и меню ("где в меню находится...", "в каком разделе...")

        query: ключевые слова процесса, препарата, системы, нормативного акта, меню или действия.
        """
        start = time.monotonic()
        try:
            data = client.search_graph(query)
            context = format_graph_context(data)
            sources = [{"id": d["id"], "title": d["title"], "type": "graph"} for d in data.get("sources", [])]
            record(
                "graph_search",
                title="Поиск по графу знаний (Memgraph)",
                query=query,
                processes_count=len(data.get("processes", [])),
                storage_rules_count=len(data.get("storage_rules", [])),
                systems_count=len(data.get("systems", [])),
                regulations_count=len(data.get("regulations", [])),
                menus_count=len(data.get("menus", [])),
                operations_count=len(data.get("operations", [])),
                duration_ms=round((time.monotonic() - start) * 1000),
                status="ok",
                context_length=len(context),
            )
            return context, {"sources": sources}
        except Exception as e:
            logger.warning(f"Ошибка обращения к Memgraph: {e}")
            record(
                "graph_search",
                title="Поиск по графу знаний (Memgraph)",
                query=query,
                status="error",
                error=str(e),
                duration_ms=round((time.monotonic() - start) * 1000),
            )
            return "Граф знаний временно недоступен. Воспользуйтесь поиском по документам search_knowledge_base.", {}

    return search_knowledge_graph

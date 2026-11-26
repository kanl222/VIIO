"""Инструмент поиска по графу знаний Memgraph для агента Assistent (Semantic GraphRAG)."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import defaultdict
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from neo4j import GraphDatabase

from assistent.observability.diagnostics import record
from assistent.application.stemmer import (
    CONTACT_STEMS,
    ORG_STEMS,
    extract_keywords,
)

logger = logging.getLogger(__name__)

MAX_STEMS = 5
MIN_STEM_LEN = 3


def _norm(expr: str) -> str:
    """Cypher-выражение: нижний регистр и ё -> е."""
    return f"toLower(replace({expr}, 'ё', 'е'))"


def _field_match(f: str, var: str = "t") -> str:
    return f"({f} IS NOT NULL AND ({f} CONTAINS {var} OR replace({f}, 'ё', 'е') CONTAINS {var} OR {_norm(f)} CONTAINS {var}))"


def _match_any(*fields: str, var: str = "t") -> str:
    """Условие: хотя бы одна основа из $stems входит хотя бы в одно из полей."""
    parts = [_field_match(f, var) for f in fields]
    return f"any({var} IN $stems WHERE {' OR '.join(parts)})"


def _empty_result() -> dict[str, Any]:
    return {
        "abbreviations": [], "processes": [], "storage_rules": [], "systems": [],
        "regulations": [], "organizations": [], "departments": [], "employees": [],
        "menus": [], "operations": [], "sources": [],
    }


def _dedup(rows: list[dict], key: str = "name") -> list[dict]:
    return list({r[key]: r for r in rows if r.get(key)}.values())


class KnowledgeGraphClient:
    """Клиент для выполнения поисковых и структурных запросов к Memgraph."""

    def __init__(
        self,
        uri: str = "bolt://localhost:7687",
        user: str = "",
        password: str = "",
        timeout: float = 5.0,
        max_stems: int = 5,
    ):
        self.uri = uri
        self.auth = (user, password)
        self.timeout = timeout
        self.max_stems = max_stems
        self._driver = None

    def get_driver(self):
        if self._driver is None:
            auth = self.auth if (self.auth and self.auth[0]) else None
            self._driver = GraphDatabase.driver(
                self.uri,
                auth=auth,
                connection_timeout=self.timeout,
            )
        return self._driver

    def close(self):
        if self._driver:
            self._driver.close()
            self._driver = None

    def search_graph(self, query: str, limit: int = 8) -> dict[str, Any]:
        """Многоэтапный поиск по графу. Синхронный: из async-кода вызывать через to_thread."""
        all_stems = [s for s in extract_keywords(query) if len(s) >= MIN_STEM_LEN]
        if not all_stems:
            return _empty_result()

        is_contact_query = any(s in CONTACT_STEMS for s in all_stems)
        is_org_query = any(s in ORG_STEMS for s in all_stems)
        # Тематические основы без слов-намерений («склад», «телефон»)
        topic = [s for s in all_stems if s not in CONTACT_STEMS and s not in ORG_STEMS]
        raw_stems = (topic or all_stems)[:self.max_stems]
        stems = list({v for s in raw_stems for v in (s, s.capitalize(), s.upper(), s.replace('ё', 'е')) if len(v) >= MIN_STEM_LEN})
        generic = (is_contact_query or is_org_query) and not topic

        result = _empty_result()
        driver = self.get_driver()
        with driver.session() as session:
            # 0. Аббревиатуры (по исходным токенам, не по основам)
            tokens = [w for w in re.findall(r"[а-яёa-z0-9\-]+", query.lower()) if len(w) >= 2]
            if tokens:
                tokens_lower = list(set(tokens))
                tokens_upper = [w.upper() for w in tokens_lower]
                query_clean = query.lower().strip()
                abbr_rows = session.run(
                    """
                    MATCH (a:Abbreviation)
                    WHERE toLower(a.name) IN $tokens_lower
                       OR (a.short_form IS NOT NULL AND toLower(a.short_form) IN $tokens_lower)
                       OR any(t IN $tokens_upper WHERE a.name = t)
                       OR (size($query_clean) >= 4 AND toLower(a.full_name) CONTAINS $query_clean)
                    OPTIONAL MATCH (a)-[:EXPANDS_TO|REFERS_TO]->(target)
                    RETURN a.name AS name, a.full_name AS full_name, a.short_form AS short_form,
                           a.description AS description, a.category AS category,
                           labels(target)[0] AS target_type,
                           coalesce(target.name, target.title, target.code) AS target_name
                    LIMIT $limit;
                    """,
                    tokens_lower=tokens_lower,
                    tokens_upper=tokens_upper,
                    query_clean=query_clean,
                    limit=limit,
                ).data()
                grouped: dict[str, dict] = {}
                for r in abbr_rows:
                    g = grouped.setdefault(r["name"], {
                        "name": r["name"], "full_name": r["full_name"],
                        "short_form": r["short_form"], "description": r["description"],
                        "category": r["category"], "related": [],
                    })
                    if r.get("target_name"):
                        t_str = (f"{r['target_type']}: {r['target_name']}"
                                 if r.get("target_type") else r["target_name"])
                        if t_str not in g["related"]:
                            g["related"].append(t_str)
                result["abbreviations"] = list(grouped.values())

            # 1. Процессы и шаги
            p_rows = session.run(
                f"""
                MATCH (p:Process)
                WHERE {_match_any("p.name")}
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
                stems=stems,
                limit=limit * 6,  # строк на процесс много (шаги x роли x нормативы)
            ).data()
            by_proc = defaultdict(lambda: {"process": "", "owner": "", "steps": [], "regulations": []})
            for r in p_rows:
                pname = r["process"]
                by_proc[pname]["process"] = pname
                by_proc[pname]["owner"] = r.get("owner")
                if r.get("reg_code") and r["reg_code"] not in by_proc[pname]["regulations"]:
                    by_proc[pname]["regulations"].append(r["reg_code"])
                if r.get("step_name"):
                    step = {
                        "order": r["step_order"], "name": r["step_name"],
                        "desc": r.get("step_desc"), "role": r.get("role"),
                        "system": r.get("system"), "next": r.get("next_step"),
                    }
                    if not any(s["order"] == step["order"] for s in by_proc[pname]["steps"]):
                        by_proc[pname]["steps"].append(step)
            result["processes"] = list(by_proc.values())

            # 2. Товары, серии, режимы хранения
            result["storage_rules"] = session.run(
                f"""
                MATCH (p:Product)
                WHERE {_match_any("p.name", "p.mnn")}
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
                stems=stems,
                limit=limit,
            ).data()

            # 3. Информационные системы и мастер-данные
            result["systems"] = session.run(
                f"""
                MATCH (sys:System)
                OPTIONAL MATCH (sys)-[:SOURCE_OF_TRUTH_FOR]->(de:DataEntity)
                WITH sys, collect(DISTINCT de.name) AS master_data
                WHERE any(t IN $stems WHERE
                       {_norm("sys.name")} CONTAINS t
                    OR (sys.type IS NOT NULL AND {_norm("sys.type")} CONTAINS t)
                    OR any(m IN master_data WHERE {_norm("m")} CONTAINS t))
                OPTIONAL MATCH (sys)-[:INTEGRATES_WITH]->(other:System)
                OPTIONAL MATCH (dep:Department)-[:USES_SYSTEM]->(sys)
                RETURN sys.name AS system, sys.type AS type, sys.criticality AS criticality,
                       master_data,
                       collect(DISTINCT other.name) AS integrations,
                       collect(DISTINCT dep.name) AS departments
                LIMIT $limit;
                """,
                stems=stems,
                limit=limit,
            ).data()

            # 4. Нормативные акты
            result["regulations"] = session.run(
                f"""
                MATCH (r:Regulation)
                WHERE {_match_any("r.code", "r.title")}
                OPTIONAL MATCH (entity)-[:COMPLIES_WITH]->(r)
                RETURN r.code AS code, r.title AS title, r.source AS source, r.edition AS edition,
                       collect(DISTINCT coalesce(entity.name, labels(entity)[0])) AS complies_entities
                LIMIT $limit;
                """,
                stems=stems,
                limit=limit,
            ).data()

            # 4.1. Организации и контакты
            org_cols = """
                RETURN org.name AS name, org.full_name AS full_name, org.short_name AS short_name,
                       org.phone AS phone, org.hotline AS hotline, org.address AS address,
                       org.email AS email, org.website AS website, org.inn AS inn
                LIMIT $limit;
            """
            org_rows: list[dict] = []
            if not generic:
                org_rows = session.run(
                    f"""
                    MATCH (org:Organization)
                    WHERE (org.phone IS NOT NULL OR org.hotline IS NOT NULL)
                      AND {_match_any("org.name", "org.short_name", "org.full_name")}
                    {org_cols}
                    """,
                    stems=stems,
                    limit=limit,
                ).data()
            if not org_rows and (generic or is_contact_query):
                org_rows = session.run(
                    f"""
                    MATCH (org:Organization)
                    WHERE org.phone IS NOT NULL OR org.hotline IS NOT NULL
                    {org_cols}
                    """,
                    limit=limit,
                ).data()
            result["organizations"] = _dedup(org_rows)

            # 4.2. Отделы и подразделения
            dep_cols = """
                OPTIONAL MATCH (org:Organization)-[:OWNS]->(dep)
                RETURN dep.name AS name, dep.code AS code, dep.phone AS phone,
                       dep.hotline AS hotline, dep.email AS email, dep.description AS description,
                       org.name AS organization
                ORDER BY dep.code
                LIMIT $limit;
            """
            dept_rows: list[dict] = []
            if not generic:
                # WHERE сразу после MATCH, иначе он фильтрует только OPTIONAL MATCH
                dept_rows = session.run(
                    f"""
                    MATCH (dep:Department)
                    WHERE {_match_any("dep.name", "dep.description")}
                    {dep_cols}
                    """,
                    stems=stems,
                    limit=limit,
                ).data()
            if not dept_rows and is_contact_query:
                dept_rows = session.run(
                    f"""
                    MATCH (dep:Department)
                    WHERE dep.phone IS NOT NULL OR dep.hotline IS NOT NULL
                    {dep_cols}
                    """,
                    limit=limit,
                ).data()
            result["departments"] = _dedup(dept_rows)

            # 4.3. Сотрудники
            e_rows = session.run(
                f"""
                MATCH (e:Employee)
                OPTIONAL MATCH (e)-[:WORKS_IN]->(dep:Department)
                WITH e, dep
                WHERE {_match_any("e.name", "e.position", "dep.name")}
                RETURN e.name AS name, e.position AS position, e.phone AS phone,
                       e.support_phones AS support_phones, e.org AS org,
                       dep.name AS department, dep.code AS dept_code
                LIMIT $limit;
                """,
                stems=stems,
                limit=limit,
            ).data()
            result["employees"] = _dedup(e_rows)

            # 5. Разделы меню
            menu_rows = session.run(
                f"""
                MATCH (m:MenuPath)
                WHERE {_match_any("m.full_path")}
                RETURN id(m) AS id, m.full_path AS menu
                LIMIT $limit;
                """,
                stems=stems,
                limit=limit,
            ).data()
            matched_menus = {r["id"]: r["menu"] for r in menu_rows}

            menus_data = []
            menu_ids = list(matched_menus)
            if menu_ids:
                sys_by_menu = defaultdict(list)
                for r in session.run(
                    """
                    MATCH (s:System)-[:HAS_MENU]->(m:MenuPath)
                    WHERE id(m) IN $ids
                    RETURN id(m) AS mid, s.name AS sys;
                    """,
                    ids=menu_ids,
                ).data():
                    sys_by_menu[r["mid"]].append(r["sys"])

                docs_by_menu = defaultdict(list)
                for r in session.run(
                    """
                    MATCH (m:MenuPath)-[:DOCUMENTED_BY]->(d:Document)
                    WHERE id(m) IN $ids
                    RETURN id(m) AS mid, d.id AS doc_id, d.title AS doc_title;
                    """,
                    ids=menu_ids,
                ).data():
                    docs_by_menu[r["mid"]].append({"id": r["doc_id"], "title": r["doc_title"]})

                acts_by_menu = defaultdict(list)
                for r in session.run(
                    """
                    MATCH (m:MenuPath)-[:CONTAINS_ACTION]->(op:Operation)
                    WHERE id(m) IN $ids
                    RETURN id(m) AS mid, op.name AS action;
                    """,
                    ids=menu_ids,
                ).data():
                    acts_by_menu[r["mid"]].append(r["action"])

                for mid, mpath in matched_menus.items():
                    menus_data.append({
                        "menu": mpath,
                        "systems": list(dict.fromkeys(sys_by_menu[mid])),
                        "docs": docs_by_menu[mid],
                        "actions": list(dict.fromkeys(acts_by_menu[mid]))[:6],
                    })
            result["menus"] = menus_data

            # 6. Операции
            op_rows = session.run(
                f"""
                MATCH (op:Operation)
                WHERE {_match_any("op.name")}
                RETURN id(op) AS id, op.name AS name
                LIMIT $limit;
                """,
                stems=stems,
                limit=limit,
            ).data()
            matched_ops = {r["id"]: r["name"] for r in op_rows}

            ops_data = []
            op_ids = list(matched_ops)
            if op_ids:
                menus_by_op = defaultdict(list)
                for r in session.run(
                    """
                    MATCH (m:MenuPath)-[:CONTAINS_ACTION]->(op:Operation)
                    WHERE id(op) IN $ids
                    RETURN id(op) AS op_id, m.full_path AS menu;
                    """,
                    ids=op_ids,
                ).data():
                    menus_by_op[r["op_id"]].append(r["menu"])

                conds, roles, nexts, drugs = (defaultdict(list) for _ in range(4))
                for r in session.run(
                    """
                    MATCH (op:Operation)-[r]->(target)
                    WHERE id(op) IN $ids
                    RETURN id(op) AS op_id, type(r) AS rel, target.name AS t_name;
                    """,
                    ids=op_ids,
                ).data():
                    name = r["t_name"]
                    if not name:
                        continue
                    rel, oid = r["rel"], r["op_id"]
                    if rel in ("REQUIRES_CONDITION", "REQUIRES"):
                        conds[oid].append(name)
                    elif rel in ("EXECUTED_BY", "RESPONSIBLE_FOR"):
                        roles[oid].append(name)
                    elif rel in ("NEXT_STEP", "NEXT"):
                        nexts[oid].append(name)
                    elif rel in ("APPLIES_TO", "BELONGS_TO"):
                        drugs[oid].append(name)

                for oid, op_name in matched_ops.items():
                    ops_data.append({
                        "operation": op_name,
                        "menus": list(dict.fromkeys(menus_by_op[oid]))[:3],
                        "conditions": list(dict.fromkeys(conds[oid]))[:4],
                        "roles": list(dict.fromkeys(roles[oid]))[:3],
                        "next_steps": list(dict.fromkeys(nexts[oid]))[:3],
                        "drug_categories": list(dict.fromkeys(drugs[oid]))[:3],
                    })
            result["operations"] = ops_data

        # Документы-источники
        sources = {}
        for m in result["menus"]:
            for doc in m.get("docs", []):
                if doc and doc.get("id"):
                    sources[doc["id"]] = doc
        result["sources"] = list(sources.values())
        return result


def format_graph_context(data: dict[str, Any]) -> str:
    """Форматирует факты из графа знаний в структурированный текст для LLM-агента."""
    abbreviations = data.get("abbreviations", [])
    processes = data.get("processes", [])
    storage_rules = data.get("storage_rules", [])
    systems = data.get("systems", [])
    regulations = data.get("regulations", [])
    organizations = data.get("organizations", [])
    departments = data.get("departments", [])
    employees = data.get("employees", [])
    menus = data.get("menus", [])
    operations = data.get("operations", [])
    sources = data.get("sources", [])

    has_data = any([
        abbreviations, processes, storage_rules, systems, regulations,
        organizations, departments, employees, menus, operations,
    ])
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

    # 4.1. Организации и контакты
    if organizations:
        blocks.append("#### Организации и контакты (Organization):")
        for org in organizations[:5]:
            blocks.append(f"- **Организация: «{org['name']}»**")
            if org.get("phone"):
                blocks.append(f"  - Телефон: {org['phone']}")
            if org.get("hotline"):
                blocks.append(f"  - Горячая линия: {org['hotline']}")
            if org.get("address"):
                blocks.append(f"  - Адрес: {org['address']}")
            if org.get("email"):
                blocks.append(f"  - Email: {org['email']}")
            if org.get("website"):
                blocks.append(f"  - Веб-сайт: {org['website']}")
        blocks.append("")

    # 4.2. Отделы и подразделения
    if departments:
        blocks.append("#### Отделы и подразделения (Department):")
        for d in departments[:5]:
            org_str = f" ({d['organization']})" if d.get("organization") else ""
            blocks.append(f"- **Отдел: «{d['name']}»**{org_str}")
            if d.get("phone"):
                blocks.append(f"  - Телефон: {d['phone']}")
            if d.get("hotline"):
                blocks.append(f"  - Горячая линия: {d['hotline']}")
            if d.get("email"):
                blocks.append(f"  - Email: {d['email']}")
            if d.get("description"):
                blocks.append(f"  - Описание: {d['description']}")
        blocks.append("")

    # 4.3. Должностные лица и сотрудники
    if employees:
        blocks.append("#### Сотрудники, руководство и контакты (Employee):")
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

    return "\n".join(blocks)


def create_graph_search_tool(settings: Any):
    """Фабрика инструмента search_knowledge_graph для агента."""
    if hasattr(settings, "memgraph_enabled") and not getattr(settings, "memgraph_enabled"):
        return None

    uri = getattr(settings, "memgraph_uri", "bolt://localhost:7687")
    user = getattr(settings, "memgraph_user", "")
    password = getattr(settings, "memgraph_password", "")
    timeout = getattr(settings, "memgraph_timeout_seconds", 5)
    max_stems = getattr(settings, "memgraph_max_stems", 5)
    client = KnowledgeGraphClient(uri=uri, user=user, password=password, timeout=float(timeout), max_stems=max_stems)

    @tool("search_knowledge_graph", response_format="content_and_artifact")
    async def search_knowledge_graph(
        query: str,
        config: RunnableConfig,
    ) -> tuple[str, dict[str, Any]]:
        """Поиск по графу знаний онтологии аптечного склада и информационных систем.

        Используйте этот инструмент, когда пользователь спрашивает:
        - Об аббревиатурах, сокращениях и терминах онтологии ("что такое ИСЛО...", "что значит МДЛП...", "расшифровка ЖНВЛП...", "что такое GDP...")
        - О бизнес-процессах и шагах алгоритмов ("какие шаги приёмки...", "алгоритм инвентаризации...", "последовательность действий")
        - О режимах хранения, сериях и локациях ("какой режим хранения у Гам-КОВИД-Вак...", "где хранится серия...", "соответствует ли режим зоны температуре препарата")
        - О нормативных актах и правилах ("требования GDP...", "правила хранения иммунобиологических...", "МДЛП постановление 1556")
        - Об информационных системах и мастер-данных ("в какой системе ведутся остатки...", "мастер-система для номенклатуры...", "с кем интегрирована WMS")
        - О должностях, ролях, организациях и контактах ("кто отвечает за входной контроль...", "телефон склада...", "контакты министерства...")
        - О навигации по интерфейсу и меню ("где в меню находится...", "в каком разделе...")

        query: ключевые слова процесса, препарата, системы, нормативного акта, меню или действия.
        """
        start = time.monotonic()
        try:
            # Драйвер синхронный: уводим в поток с таймаутом
            data = await asyncio.wait_for(
                asyncio.to_thread(client.search_graph, query),
                timeout=float(timeout),
            )
            context = format_graph_context(data)
            duration_ms = round((time.monotonic() - start) * 1000)
            # Граф знаний возвращает факты, не привязываясь к списку документов-источников
            sources: list[dict[str, Any]] = []
            record(
                "graph_search",
                title="Поиск по графу знаний (Memgraph)",
                query=query,
                processes_count=len(data.get("processes", [])),
                storage_rules_count=len(data.get("storage_rules", [])),
                systems_count=len(data.get("systems", [])),
                regulations_count=len(data.get("regulations", [])),
                organizations_count=len(data.get("organizations", [])),
                departments_count=len(data.get("departments", [])),
                employees_count=len(data.get("employees", [])),
                menus_count=len(data.get("menus", [])),
                operations_count=len(data.get("operations", [])),
                duration_ms=duration_ms,
                status="ok",
                context_length=len(context),
            )
            from assistent.observability.telemetry import record_memgraph_telemetry
            record_memgraph_telemetry(
                query=query,
                context=context,
                data=data,
                duration_ms=duration_ms,
                status="ok",
                uri=client.uri,
            )
            return context, {"sources": sources}
        except asyncio.TimeoutError:
            duration_ms = round((time.monotonic() - start) * 1000)
            logger.warning("Таймаут обращения к Memgraph (%s с)", timeout)
            record(
                "graph_search",
                title="Поиск по графу знаний (Memgraph)",
                query=query,
                status="timeout",
                duration_ms=duration_ms,
            )
            from assistent.observability.telemetry import record_memgraph_telemetry
            record_memgraph_telemetry(
                query=query,
                context="",
                duration_ms=duration_ms,
                status="timeout",
                error="Timeout",
                uri=client.uri,
            )
            return "Поиск по графу знаний превысил время ожидания. Воспользуйтесь поиском по документам search_knowledge_base.", {}
        except Exception as e:
            duration_ms = round((time.monotonic() - start) * 1000)
            logger.warning("Ошибка обращения к Memgraph: %s", e)
            record(
                "graph_search",
                title="Поиск по графу знаний (Memgraph)",
                query=query,
                status="error",
                error=str(e),
                duration_ms=duration_ms,
            )
            from assistent.observability.telemetry import record_memgraph_telemetry
            record_memgraph_telemetry(
                query=query,
                context="",
                duration_ms=duration_ms,
                status="error",
                error=str(e),
                uri=client.uri,
            )
            return "Граф знаний временно недоступен. Воспользуйтесь поиском по документам search_knowledge_base.", {}

    return search_knowledge_graph

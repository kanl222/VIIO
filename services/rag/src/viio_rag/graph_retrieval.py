"""Memgraph GraphRAG retriever for VIIO RAG service."""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Any

from viio_rag.stemmer import (
    CONTACT_STEMS,
    ORG_STEMS,
    stem_word,
)

logger = logging.getLogger(__name__)

STOP_WORDS = {
    "в", "во", "на", "по", "с", "со", "к", "ко", "из", "изо", "от", "до", "для", "о", "об",
    "как", "где", "что", "кто", "чем", "кому", "какой", "какие", "какая", "какое", "такое", "такая", "такие",
    "ли", "же", "бы", "и", "или", "не", "лишь", "только", "найти", "подскажи", "покажи",
}

MAX_STEMS = 5
MIN_STEM_LEN = 3


def extract_keywords(query: str) -> list[str]:
    """Извлекает значимые ключевые слова для запроса к графу."""
    words = re.findall(r"[а-яёa-z0-9]+", query.lower())
    meaningful = [w for w in words if w not in STOP_WORDS and len(w) >= 3]
    return meaningful if meaningful else words


def _norm(expr: str) -> str:
    """Cypher-выражение: нижний регистр и ё -> е."""
    return f"toLower(replace({expr}, 'ё', 'е'))"


def _field_match(f: str, var: str = "t") -> str:
    return f"({f} IS NOT NULL AND ({f} CONTAINS {var} OR replace({f}, 'ё', 'е') CONTAINS {var} OR {_norm(f)} CONTAINS {var}))"


def _match_any(*fields: str, var: str = "t") -> str:
    """Условие: хотя бы одна основа из $stems входит хотя бы в одно из полей."""
    parts = [_field_match(f, var) for f in fields]
    return f"any({var} IN $stems WHERE {' OR '.join(parts)})"


class MemgraphRetriever:
    """Клиент для выполнения поиска фактов в Memgraph."""

    def __init__(
        self,
        uri: str = "bolt://memgraph:7687",
        user: str = "",
        password: str = "",
        enabled: bool = True,
        timeout: float = 5.0,
        max_stems: int = 5,
    ):
        self.uri = uri
        self.auth = (user, password)
        self.enabled = enabled
        self.timeout = timeout
        self.max_stems = max_stems
        self._driver = None

    def get_driver(self):
        if not self.enabled:
            return None
        if self._driver is None:
            try:
                from neo4j import GraphDatabase
                auth = self.auth if (self.auth and self.auth[0]) else None
                self._driver = GraphDatabase.driver(
                    self.uri,
                    auth=auth,
                    connection_timeout=self.timeout,
                )
            except Exception as e:
                logger.warning("Не удалось инициализировать neo4j драйвер для Memgraph: %s", e)
                return None
        return self._driver

    def close(self):
        if self._driver:
            try:
                self._driver.close()
            except Exception:
                pass
            self._driver = None

    def search_graph(self, query: str, limit: int = 5) -> dict[str, Any]:
        """Поиск по графу онтологии. Безопасен при сбое."""
        if not self.enabled:
            return {}

        keywords = extract_keywords(query)
        if not keywords:
            return {}

        all_stems = [stem_word(w) for w in keywords if len(w) >= MIN_STEM_LEN]
        if not all_stems:
            return {}

        is_contact_query = any(s in CONTACT_STEMS for s in all_stems)
        is_org_query = any(s in ORG_STEMS for s in all_stems)
        # Тематические основы: без слов-намерений («склад», «телефон»)
        topic = [s for s in all_stems if s not in CONTACT_STEMS and s not in ORG_STEMS]
        raw_stems = (topic or all_stems)[:self.max_stems]
        stems = list({v for s in raw_stems for v in (s, s.capitalize(), s.upper(), s.replace('ё', 'е')) if len(v) >= MIN_STEM_LEN})
        # «Общий» запрос: есть намерение, но нет конкретной темы
        generic = (is_contact_query or is_org_query) and not topic

        driver = self.get_driver()
        if not driver:
            return {}

        try:
            with driver.session() as session:
                # 0. Аббревиатуры и термины онтологии (по исходным токенам, не по основам)
                abbr_data = []
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
                    grouped_abbr: dict[str, dict] = {}
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
                            t_str = (
                                f"{r['target_type']}: {r['target_name']}"
                                if r.get("target_type") else r["target_name"]
                            )
                            if t_str not in grouped_abbr[k]["related"]:
                                grouped_abbr[k]["related"].append(t_str)
                    abbr_data = list(grouped_abbr.values())

                # 1. Процессы и шаги алгоритмов
                processes_data = []
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
                processes_data = list(by_proc.values())

                # 2. Товары, серии и условия хранения
                storage_data = session.run(
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

                # 3. Информационные системы
                systems_data = session.run(
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
                reg_data = session.run(
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

                # 5. Организации, горячие линии и телефоны
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
                # Общий запрос или ничего не нашли по теме, но спрашивают контакты
                if not org_rows and (generic or is_contact_query):
                    org_rows = session.run(
                        f"""
                        MATCH (org:Organization)
                        WHERE org.phone IS NOT NULL OR org.hotline IS NOT NULL
                        {org_cols}
                        """,
                        limit=limit,
                    ).data()
                org_data = list({r["name"]: r for r in org_rows}.values())

                # 6. Отделы и подразделения
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
                    # WHERE стоит сразу после MATCH, иначе он фильтрует только OPTIONAL MATCH
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
                dept_data = list({r["name"]: r for r in dept_rows}.values())

                # 7. Сотрудники и контакты
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
                emp_data = list({r["name"]: r for r in e_rows}.values())

                return {
                    "abbreviations": abbr_data,
                    "processes": processes_data,
                    "storage_rules": storage_data,
                    "systems": systems_data,
                    "regulations": reg_data,
                    "organizations": org_data,
                    "departments": dept_data,
                    "employees": emp_data,
                }
        except Exception as e:
            logger.warning("Ошибка при поиске по Memgraph онтологии: %s", e)
            return {}

    @classmethod
    def format_graph_context(cls, data: dict[str, Any]) -> str:
        """Формирует компактный и структурированный текст онтологических фактов."""
        if not data:
            return ""

        abbreviations = data.get("abbreviations", [])
        processes = data.get("processes", [])
        storage_rules = data.get("storage_rules", [])
        systems = data.get("systems", [])
        regulations = data.get("regulations", [])
        organizations = data.get("organizations", [])
        departments = data.get("departments", [])
        employees = data.get("employees", [])

        has_data = any([
            abbreviations, processes, storage_rules, systems, regulations,
            organizations, departments, employees,
        ])
        if not has_data:
            return ""

        blocks = ["### Факты онтологии и термины (Memgraph Knowledge Graph):\n"]

        if abbreviations:
            blocks.append("#### Расшифровка аббревиатур и терминов:")
            for a in abbreviations[:5]:
                short_s = f" ({a['short_form']})" if a.get("short_form") and a["short_form"] != a["name"] else ""
                cat_s = f" [{a['category']}]" if a.get("category") else ""
                blocks.append(f"- **{a['name']}**{short_s}: **{a['full_name']}**{cat_s}")
                if a.get("description"):
                    blocks.append(f"  - Назначение: {a['description']}")
                if a.get("related"):
                    blocks.append(f"  - Связи: {', '.join(a['related'][:3])}")
            blocks.append("")

        if processes:
            blocks.append("#### Бизнес-процессы и шаги алгоритмов:")
            for p in processes[:3]:
                owner_str = f" (Владелец: {p['owner']})" if p.get("owner") else ""
                regs_str = f" [Норматив: {', '.join(p['regulations'])}]" if p.get("regulations") else ""
                blocks.append(f"- **Процесс: «{p['process']}»**{owner_str}{regs_str}")
                sorted_steps = sorted(p.get("steps", []), key=lambda s: s.get("order", 0))
                for st in sorted_steps[:6]:
                    role_str = f" | Ответственный: {st['role']}" if st.get("role") else ""
                    sys_str = f" | Система: {st['system']}" if st.get("system") else ""
                    blocks.append(f"  {st['order']}. **{st['name']}**{role_str}{sys_str}")
            blocks.append("")

        if storage_rules:
            blocks.append("#### Режимы хранения и локации:")
            for row in storage_rules[:3]:
                p_name = row.get("product") or "Препарат"
                req_c = row.get("required_condition") or "Не указан"
                cat = f" (Категория: {row['category']})" if row.get("category") else ""
                blocks.append(f"- **Препарат:** **«{p_name}»**{cat}")
                blocks.append(f"  - Требуемый режим: `{req_c}`")
                if row.get("batch_number"):
                    loc_code = row.get("location") or "Не размещена"
                    blocks.append(f"  - Серия: `{row['batch_number']}` (Зона: `{loc_code}`)")
            blocks.append("")

        if systems:
            blocks.append("#### Информационные системы:")
            for s in systems[:3]:
                s_type = f" [{s.get('type')}]" if s.get("type") else ""
                blocks.append(f"- **Система: «{s.get('system')}»**{s_type}")
                if s.get("master_data"):
                    blocks.append(f"  - Мастер-данные: {', '.join(s['master_data'])}")
                if s.get("integrations"):
                    blocks.append(f"  - Интеграция: {', '.join(s['integrations'])}")
            blocks.append("")

        if regulations:
            blocks.append("#### Нормативные требования:")
            for r in regulations[:3]:
                blocks.append(f"- **[{r.get('code')}]** «{r.get('title')}» ({r.get('source', '')})")
            blocks.append("")

        if organizations:
            blocks.append("#### Организации и контакты:")
            for org in organizations[:4]:
                phone = f" | Тел: {org['phone']}" if org.get("phone") else ""
                hotline = f" | Горячая линия: {org['hotline']}" if org.get("hotline") else ""
                addr = f" | Адрес: {org['address']}" if org.get("address") else ""
                blocks.append(f"- **{org['name']}**{phone}{hotline}{addr}")
            blocks.append("")

        if departments:
            blocks.append("#### Подразделения и отделы:")
            for d in departments[:4]:
                org = f" ({d['organization']})" if d.get("organization") else ""
                phone = f" | Тел: {d['phone']}" if d.get("phone") else ""
                hotline = f" | Горячая линия: {d['hotline']}" if d.get("hotline") else ""
                blocks.append(f"- **{d['name']}**{org}{phone}{hotline}")
            blocks.append("")

        if employees:
            blocks.append("#### Контакты и руководство:")
            for e in employees[:4]:
                pos = f" — {e['position']}" if e.get("position") else ""
                phone = f" | Тел: {e['phone']}" if e.get("phone") else ""
                sup = f" | Доп. линии: {e['support_phones']}" if e.get("support_phones") else ""
                blocks.append(f"- **{e['name']}**{pos}{phone}{sup}")
            blocks.append("")

        return "\n".join(blocks)
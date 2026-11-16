"""Memgraph GraphRAG retriever for VIIO RAG service.

Performs fast semantic graph queries over pharmacy warehouse ontology:
- Abbreviations / Acronyms (ИСЛО, МДЛП, ЕГИСЗ, ЖНВЛП...)
- Business processes & steps (приёмка, отпуск, перемещение, списание)
- Temperature & storage conditions (2-8 °C, chambers, batches)
- Information systems & master data (1С:ERP, WMS, МДЛП)
- Regulations & laws (323-ФЗ, 61-ФЗ, 1094н...)
- Employees & support contacts
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any
from collections import defaultdict

logger = logging.getLogger(__name__)

STOP_WORDS = {
    "в", "во", "на", "по", "с", "со", "к", "ко", "из", "изо", "от", "до", "для", "о", "об",
    "как", "где", "что", "кто", "чем", "кому", "какой", "какие", "какая", "какое",
    "ли", "же", "бы", "и", "или", "не", "лишь", "только", "найти", "подскажи", "покажи",
}


def extract_keywords(query: str) -> list[str]:
    """Извлекает значимые ключевые слова для запроса к графу."""
    words = re.findall(r"[а-яёa-z0-9]+", query.lower())
    meaningful = [w for w in words if w not in STOP_WORDS and len(w) >= 3]
    return meaningful if meaningful else words


class MemgraphRetriever:
    """Клиент для выполнения параллельного поиска фактов в Memgraph."""

    def __init__(
        self,
        uri: str = "bolt://memgraph:7687",
        user: str = "",
        password: str = "",
        enabled: bool = True,
    ):
        self.uri = uri
        self.auth = (user, password)
        self.enabled = enabled
        self._driver = None

    def get_driver(self):
        if not self.enabled:
            return None
        if self._driver is None:
            try:
                from neo4j import GraphDatabase
                self._driver = GraphDatabase.driver(self.uri, auth=self.auth)
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
        """Быстрый поиск по графу онтологии (< 50 мс). Безопасен при сбое."""
        if not self.enabled:
            return {}

        keywords = extract_keywords(query)
        if not keywords:
            return {}

        driver = self.get_driver()
        if not driver:
            return {}

        try:
            with driver.session() as session:
                # 0. Аббревиатуры и термины онтологии
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

                # 1. Процессы и шаги алгоритмов
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

                # 2. Товары, серии и условия хранения
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

                # 3. Информационные системы
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

                # 4. Нормативные акты
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

                # 5. Сотрудники и контакты
                emp_data = []
                seen_emps = set()
                for kw in keywords[:2]:
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

                return {
                    "abbreviations": abbr_data,
                    "processes": processes_data,
                    "storage_rules": storage_data,
                    "systems": systems_data,
                    "regulations": reg_data,
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
        employees = data.get("employees", [])

        has_data = any([abbreviations, processes, storage_rules, systems, regulations, employees])
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

        if employees:
            blocks.append("#### Контакты и руководство:")
            for e in employees[:4]:
                pos = f" — {e['position']}" if e.get("position") else ""
                phone = f" | Тел: {e['phone']}" if e.get("phone") else ""
                blocks.append(f"- **{e['name']}**{pos}{phone}")
            blocks.append("")

        return "\n".join(blocks)

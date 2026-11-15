"""Инструмент поиска по графу знаний Memgraph для агента Assistent (Semantic GraphRAG)."""

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
            return {"menus": [], "operations": [], "sources": []}

        driver = self.get_driver()
        with driver.session() as session:
            # 1. Поиск релевантных путей меню
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

            # Подгружаем связи для найденных меню (системы, документы, действия)
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

            # 2. Поиск релевантных операций (действий)
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
                # Меню для операций
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

                # Исходящие связи операции: условия, роли, следующие шаги, категории
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
                    if rel == "REQUIRES_CONDITION":
                        conds_by_op[op_id].append(name)
                    elif rel == "EXECUTED_BY":
                        roles_by_op[op_id].append(name)
                    elif rel == "NEXT_STEP":
                        next_by_op[op_id].append(name)
                    elif rel == "APPLIES_TO":
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

            # 3. Сбор документов-источников
            sources = {}
            for m in menus_data:
                for doc in m.get("docs", []):
                    if doc and doc.get("id"):
                        sources[doc["id"]] = doc

            return {
                "menus": menus_data,
                "operations": ops_data,
                "sources": list(sources.values()),
            }


def format_graph_context(data: dict[str, Any]) -> str:
    """Форматирует факты из графа знаний в удобный для чтения структурированный текст."""
    menus = data.get("menus", [])
    operations = data.get("operations", [])
    sources = data.get("sources", [])

    if not menus and not operations:
        return "В графе интерфейса и регламентов не найдено прямых соответствий по запросу."

    blocks = ["### Факты из графа знаний (Интерфейс, операции и регламенты)\n"]

    if menus:
        blocks.append("#### Разделы и пути меню программы:")
        for m in menus[:6]:
            systems_str = ", ".join(m["systems"]) if m.get("systems") else "Информационная система"
            blocks.append(f"- **Путь в меню:** `{m['menu']}` (Система: *{systems_str}*)")
            if m.get("actions"):
                actions_str = ", ".join(m["actions"][:5])
                blocks.append(f"  - Доступные действия: {actions_str}")
            docs = [d["title"] for d in m.get("docs", []) if d and d.get("title")]
            if docs:
                blocks.append(f"  - Регламентирующий документ: *{docs[0]}*")
        blocks.append("")

    if operations:
        blocks.append("#### Операции, действия и условия выполнения:")
        for op in operations[:6]:
            blocks.append(f"- **Действие:** **«{op['operation']}»**")
            if op.get("menus"):
                blocks.append(f"  - Выполняется в меню: `{op['menus'][0]}`")
            if op.get("roles"):
                blocks.append(f"  - Кто выполняет (роль): {', '.join(op['roles'])}")
            if op.get("conditions"):
                blocks.append(f"  - Обязательные условия: {'; '.join(op['conditions'])}")
            if op.get("drug_categories"):
                blocks.append(f"  - Категории препаратов/ТМЦ: {', '.join(op['drug_categories'])}")
            if op.get("next_steps"):
                blocks.append(f"  - Следующий шаг процесса (NEXT_STEP): {', '.join(op['next_steps'])}")
        blocks.append("")

    if sources:
        blocks.append("#### Подтверждающие инструкции и регламенты:")
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
        """Поиск по графу знаний интерфейса и регламентов (системы, разделы меню, операции, условия, роли, инструкции).

        Используйте этот инструмент, когда пользователь спрашивает:
        - Где в интерфейсе программы находится функция или меню ("в каком меню...", "где найти...", "путь к...")
        - Как выполнить операцию, порядок действий, шаги алгоритма ("как провести инвентаризацию...", "шаги списания...")
        - Какие условия, ограничения или правила действуют ("при каких условиях...", "требования к немаркированным...")
        - Какая должность/роль отвечает за операцию ("кто выполняет...", "роль старшей медсестры...")
        - Какой инструкцией регламентируется действие

        query: ключевые слова действия, пункта меню, условий или регламента.
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
                menus_count=len(data.get("menus", [])),
                operations_count=len(data.get("operations", [])),
                conditions_count=len(data.get("conditions", [])),
                roles_count=len(data.get("roles", [])),
                sources_count=len(sources),
                menus=[m.get("menu") for m in data.get("menus", []) if isinstance(m, dict) and m.get("menu")],
                operations=[op.get("name") for op in data.get("operations", []) if isinstance(op, dict) and op.get("name")],
                sources=sources[:10],
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

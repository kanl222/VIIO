"""Multi-hop query decomposition and coordinated retrieval for Agentic RAG.

Decomposes complex composite questions (e.g. asking for an organizational contact
or system entity alongside a document procedure) into parallel, focused sub-queries
to both the knowledge graph (ontology) and document knowledge base.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool

from assistent.services.answer_sources import readable_reference, strip_thinking
from assistent.services.diagnostics import model_call, record
from assistent.utils.stemmer import (
    CONTACT_STEMS,
    ORG_STEMS,
    extract_keywords,
)

logger = logging.getLogger(__name__)

# Регулярные выражения для выявления составных вопросов (конъюнкции и вопросительные связки)
_MULTI_HOP_SPLIT = re.compile(
    r"(?:\?[\s\n]+)"  # Несколько вопросов подряд: «Кто директор? Как оформить возврат?»
    r"|(?:;[\s\n]+)"  # Точка с запятой между частями
    r"|(?:\b(?:а\s+также|также|и|а)\s+(?:как|какой|какая|какие|где|кто|куда|почем|в\s+каком|на\s+основании|по\s+какому|сколько)\b)"
    r"|(?:\b(?:а|и)\s+(?:подскажи|уточни|расскажи|где|кто|как)\b)",
    re.IGNORECASE,
)

# Маркеры онтологии / графа знаний
_ONTOLOGY_KEYWORDS = {
    "директор", "замдиректор", "заместитель", "начальник", "руководитель", "бухгалтер",
    "кадр", "отдел", "склад", "берёзк", "телефон", "номер", "добавочн", "контакт",
    "email", "почт", "адрес", "оас", "госаптек", "инн", "огрн", "кпп", "реквизит",
    "система", "исло", "мдлп", "егисз", "честный", "знак", "хранени", "температур",
    "градус", "режим", "серия", "партия", "локация", "зона",
}

# Маркеры процедурных документов и регламентов
_DOCUMENT_KEYWORDS = {
    "регламент", "инструкци", "порядок", "алгоритм", "процедур", "правил", "шаг", "действи",
    "оформить", "принять", "приемк", "списать", "списани", "зарегистрировать", "вернуть",
    "возврат", "брак", "перемещени", "передать", "инвентаризаци", "кнопк", "вкладк",
    "инцидент", "заявк", "согласовани", "форма",
}


@dataclass
class SubQuery:
    query: str
    target: Literal["graph", "knowledge_base"]
    description: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class MultiHopPlan:
    is_multi_hop: bool
    sub_queries: list[SubQuery] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_multi_hop": self.is_multi_hop,
            "sub_queries": [sq.to_dict() for sq in self.sub_queries],
        }


def _classify_target(text: str) -> Literal["graph", "knowledge_base"]:
    """Определяет целевой инструмент поиска для текстового фрагмента."""
    words = {w.lower() for w in re.findall(r"\w{3,}", text)}
    stem_hits_ont = sum(1 for w in words if any(ok in w for ok in _ONTOLOGY_KEYWORDS))
    stem_hits_doc = sum(1 for w in words if any(dk in w for dk in _DOCUMENT_KEYWORDS))

    # Наличие специфических ключевых контактов или должностей отдаёт приоритет графу
    if any(s in words for s in {"директор", "начальник", "телефон", "добавочный", "контакты", "адрес"}):
        return "graph"

    if stem_hits_ont > stem_hits_doc:
        return "graph"
    return "knowledge_base"


def heuristic_decompose(question: str) -> MultiHopPlan:
    """Быстрая эвристическая декомпозиция составных вопросов без обращения к LLM."""
    clean_q = question.strip()
    if len(clean_q.split()) < 5:
        return MultiHopPlan(is_multi_hop=False)

    parts = _MULTI_HOP_SPLIT.split(clean_q)
    parts = [p.strip(" \t\n,;.-?!«»\"'") for p in parts if p and len(p.strip()) >= 5]

    if len(parts) < 2:
        # Проверяем гибридный вопрос без явного союза: например, содержит и контакты/должность, и регламент/действие
        has_ont = any(ok in clean_q.lower() for ok in ("директор", "начальник", "добавочн", "телефон", "отдел кадров", "отдел иб", "отдел ит"))
        has_doc = any(dk in clean_q.lower() for dk in ("регламент", "инструкци", "как оформить", "как принять", "порядок действий"))
        if has_ont and has_doc:
            # Извлекаем сущность онтологии и процедуру
            return MultiHopPlan(
                is_multi_hop=True,
                sub_queries=[
                    SubQuery(query=clean_q, target="graph", description="Онтологические факты и контакты"),
                    SubQuery(query=clean_q, target="knowledge_base", description="Регламентирующие документы"),
                ],
            )
        return MultiHopPlan(is_multi_hop=False)

    sub_queries: list[SubQuery] = []
    targets_seen: set[str] = set()

    for part in parts[:3]:  # Не более 3 подзапросов
        target = _classify_target(part)
        targets_seen.add(target)
        sub_queries.append(
            SubQuery(
                query=part,
                target=target,
                description=f"Подзапрос: {part[:60]}",
            )
        )

    # Если все части обращаются к одному типу или разделились между графом и базой
    is_multi = len(sub_queries) >= 2 and (len(targets_seen) > 1 or any(len(p.split()) >= 3 for p in parts))
    return MultiHopPlan(is_multi_hop=is_multi, sub_queries=sub_queries if is_multi else [])


DECOMPOSE_PROMPT = """Ты — координатор поисковых запросов в корпоративной системе Agentic RAG.
Определи, является ли вопрос пользователя составным (требующим нескольких независимых подзапросов):
1. 'graph' — поиск по онтологии: сотрудники, руководство, отделы, добавочные телефоны, информационные системы, режимы хранения препаратов.
2. 'knowledge_base' — поиск по базе документов: регламенты, инструкции, пошаговые алгоритмы, правила оформления.

Если вопрос простой (1 тема) — верни JSON:
{"is_multi_hop": false}

Если вопрос составной (объединяет контакты/должность и регламент, либо две разные процедуры) — разбей его на 2 (максимум 3) подзапроса:
{
  "is_multi_hop": true,
  "sub_queries": [
    {"query": "ключевые слова для первого поиска", "target": "graph или knowledge_base"},
    {"query": "ключевые слова для второго поиска", "target": "graph или knowledge_base"}
  ]
}

Выведи ТОЛЬКО валидный JSON без markdown-разметки и пояснений.
"""


async def decompose_query(
    question: str,
    llm: BaseChatModel | None = None,
    timeout: float = 3.5,
) -> MultiHopPlan:
    """Декомпозирует составной вопрос пользователя, используя модель или эвристику."""
    fast_plan = heuristic_decompose(question)

    # Если вопрос слишком короткий или эвристика однозначно отвергла составной характер
    if not fast_plan.is_multi_hop and len(question.strip().split()) < 7:
        return fast_plan

    if llm is None:
        return fast_plan

    try:
        response = await asyncio.wait_for(
            model_call(
                llm,
                [
                    SystemMessage(content=DECOMPOSE_PROMPT),
                    HumanMessage(content=question),
                ],
            ),
            timeout=timeout,
        )
        content = strip_thinking(str(response.content)).strip()
        # Извлекаем JSON даже при наличии внешнего текста
        json_match = re.search(r"\{[\s\S]*\}", content)
        if json_match:
            data = json.loads(json_match.group(0))
            if isinstance(data, dict) and data.get("is_multi_hop"):
                sub_queries_raw = data.get("sub_queries", [])
                sub_queries: list[SubQuery] = []
                for sq in sub_queries_raw:
                    q = str(sq.get("query", "")).strip()
                    target = sq.get("target")
                    if q and target in ("graph", "knowledge_base"):
                        sub_queries.append(SubQuery(query=q, target=target, description=q[:60]))
                if len(sub_queries) >= 2:
                    return MultiHopPlan(is_multi_hop=True, sub_queries=sub_queries)
            elif isinstance(data, dict) and not data.get("is_multi_hop"):
                return MultiHopPlan(is_multi_hop=False)
    except Exception as exc:
        logger.debug("Model-based query decomposition fallback to heuristics: %s", exc)

    return fast_plan


async def execute_multi_hop_retrieval(
    plan: MultiHopPlan,
    tools_by_name: dict[str, BaseTool],
    user_context: dict[str, Any] | None = None,
    context_max_chars: int = 12000,
    timeout: float = 6.0,
) -> tuple[str, list[dict[str, Any]]]:
    """Параллельно выполняет подзапросы по целевым инструментам и собирает объединённый контекст."""
    if not plan.is_multi_hop or not plan.sub_queries:
        return "", []

    start_time = time.monotonic()
    config = {"configurable": {"user_context": user_context or {}}}

    async def run_sub(sq: SubQuery) -> tuple[SubQuery, Any]:
        tool_name = "search_knowledge_graph" if sq.target == "graph" else "search_knowledge_base"
        tool = tools_by_name.get(tool_name)
        if not tool:
            # Fallback к search_knowledge_base, если граф не подключен
            tool = tools_by_name.get("search_knowledge_base")
        if not tool:
            return sq, ""
        try:
            res = await asyncio.wait_for(
                tool.ainvoke({"query": sq.query}, config=config),
                timeout=timeout,
            )
            return sq, res
        except Exception as err:
            logger.warning("Multi-hop sub-query '%s' failed on %s: %s", sq.query, tool_name, err)
            return sq, ""

    results = await asyncio.gather(*(run_sub(sq) for sq in plan.sub_queries))

    context_blocks: list[str] = []
    sources: list[dict[str, Any]] = []

    for sq, res in results:
        if not res:
            continue
        if isinstance(res, tuple):
            text_val, meta_val = res[0], res[1] if len(res) > 1 else {}
            if isinstance(meta_val, dict) and "sources" in meta_val:
                sources.extend(meta_val["sources"])
        else:
            text_val = str(res)

        if text_val and text_val.strip():
            header = f"### [Сведения по запросу: «{sq.query}» ({'Граф знаний/Онтология' if sq.target == 'graph' else 'База документов'})]"
            context_blocks.append(f"{header}\n{text_val.strip()}")

    combined = "\n\n".join(context_blocks)
    if len(combined) > context_max_chars:
        combined = combined[:context_max_chars]

    duration_ms = round((time.monotonic() - start_time) * 1000)
    record(
        "multi_hop_retrieve",
        sub_queries_count=len(plan.sub_queries),
        duration_ms=duration_ms,
        context_len=len(combined),
        sources_count=len(sources),
    )
    return combined, sources

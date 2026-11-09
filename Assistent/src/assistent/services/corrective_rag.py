"""Corrective RAG and Self-RAG StateGraph for verified corporate document Q&A.

Workflow:
START -> retrieve -> grade_documents -> [generate | rewrite_query | fallback]
rewrite_query -> retrieve (up to max_rewrites)
generate -> check_groundedness -> finalize -> END
fallback -> finalize -> END
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime

from assistent.prompts import (
    DEFAULT_SYSTEM_PROMPT,
    DOCUMENT_TURN_PROMPT,
    NOT_FOUND_PHRASE,
)
from assistent.services.agent_policy import (
    AgentRuntimeContext,
    AgentState,
    ctx,
    history_for_turn,
    last_question,
    finalize_response,
)
from assistent.services.answer_sources import (
    cited_sources,
    clean_answer,
    readable_reference,
    sources_from_reference,
    strip_thinking,
)
from assistent.services.diagnostics import record

logger = logging.getLogger(__name__)

GRADE_DOCUMENTS_PROMPT = """Оцени релевантность найденных фрагментов корпоративных документов вопросу пользователя.
Вопрос:
{question}

Фрагменты документов:
{documents}

Ответь строго одним словом: 'yes', если хотя бы один фрагмент содержит информацию или факты, позволяющие ответить на вопрос, иначе 'no'."""

REWRITE_QUERY_PROMPT = """Ты — эксперт по поиску в базе знаний организации.
Предыдущий поиск по вопросу пользователя не нашел релевантных документов.
Сформулируй улучшенный поисковый запрос (2-5 ключевых слов):
- используй официальные термины и синонимы;
- разверни сокращения и аббревиатуры;
- исключи лишние слова, вопросы и приветствия;
- оставь только суть для поиска по тексту документов.

Исходный вопрос:
{question}

Предыдущий запрос:
{current_query}

Выведи только новый поисковый запрос одной строкой без кавычек и пояснений."""

CHECK_GROUNDEDNESS_PROMPT = """Оцени, подтверждается ли ответ только фактами из предоставленного контекста.
Контекст:
{documents}

Ответ:
{answer}

Ответь строго одним словом: 'yes', если все факты и утверждения в ответе основаны на контексте или если ответ прямо сообщает об отсутствии данных.
Ответь строго 'no', если ответ содержит выдуманные факты, цифры или правила, которых нет в контексте."""


class CorrectiveRAGState(AgentState, total=False):
    question: str
    current_query: str
    retrieved_docs: str
    raw_reference: str
    rewrite_count: int
    is_relevant: bool
    is_grounded: bool
    draft_answer: str


def is_affirmative(text: str) -> bool:
    clean = strip_thinking(text).strip().lower()
    return clean.startswith("yes") or clean.startswith("да") or "релевант" in clean


def build_corrective_rag_graph(
    llm: BaseChatModel,
    tools: Sequence[BaseTool] | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    system_prompt: str | None = None,
    context_max_chars: int = 12000,
    history_max_chars: int = 18000,
    max_rewrites: int = 2,
    **extra_kwargs: Any,
) -> CompiledStateGraph:
    """Builds a Corrective RAG & Self-RAG graph with document grading, query rewriting, and groundedness verification."""
    active_tools = list(tools or [])
    tools_by_name = {t.name: t for t in active_tools}
    search_tool = next(
        (t for t in active_tools if t.name in ("search_knowledge_base", "search_document_content")),
        None,
    )
    list_tool = tools_by_name.get("list_documents")

    # 1. Retrieve Node
    async def retrieve_node(state: CorrectiveRAGState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        history, boundary = history_for_turn(state, runtime)
        question = state.get("question") or last_question(history)
        current_query = state.get("current_query") or question
        user_context = ctx(runtime).get("user_context", {})

        # Check for catalog inquiry
        is_catalog = bool(re.search(r"(?:список|перечень|какие)\s+(?:документ|инструкци|регламент)", question, re.I))
        if is_catalog and list_tool:
            try:
                res = await list_tool.ainvoke({"query": current_query}, config={"configurable": {"user_context": user_context}})
                content = res[0] if isinstance(res, tuple) else str(res)
                record("crag_retrieve", query=current_query, mode="catalog", length=len(content))
                return {
                    **boundary,
                    "question": question,
                    "current_query": current_query,
                    "retrieved_docs": content,
                    "raw_reference": "",
                    "is_relevant": bool(content and NOT_FOUND_PHRASE not in content),
                }
            except Exception as e:
                logger.warning("Catalog search tool failed: %s", e)

        raw_ref = ""
        # Search via tool
        if search_tool:
            try:
                raw_res = await search_tool.ainvoke({"query": current_query}, config={"configurable": {"user_context": user_context}})
                raw_ref = str(raw_res) if raw_res else ""
            except Exception as exc:
                logger.warning("Knowledge search tool failed: %s", exc)
                raw_ref = ""
        elif ctx(runtime).get("reference_context"):
            raw_ref = ctx(runtime).get("reference_context", "")

        readable = readable_reference(raw_ref, max_chars=context_max_chars) if raw_ref else ""
        record("crag_retrieve", query=current_query, chars=len(readable), raw_chars=len(raw_ref))
        return {
            **boundary,
            "question": question,
            "current_query": current_query,
            "retrieved_docs": readable,
            "raw_reference": raw_ref,
        }

    # 2. Grade Documents Node
    async def grade_documents_node(state: CorrectiveRAGState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        docs = state.get("retrieved_docs", "").strip()
        question = state.get("question", "")

        # Fast heuristic checks before LLM invocation
        if not docs or NOT_FOUND_PHRASE in docs or "ничего не найдено" in docs.lower() or "ошибка при обращении" in docs.lower():
            record("crag_grade_documents", relevant=False, fast_path=True)
            return {"is_relevant": False}

        # If already marked relevant (e.g., catalog listing)
        if state.get("is_relevant") is True:
            return {"is_relevant": True}

        prompt = GRADE_DOCUMENTS_PROMPT.format(question=question, documents=docs[:2500])
        try:
            eval_res = await llm.ainvoke([HumanMessage(content=prompt)])
            relevant = is_affirmative(str(eval_res.content))
        except Exception as exc:
            logger.warning("Document grading failed, assuming relevant: %s", exc)
            relevant = True

        record("crag_grade_documents", relevant=relevant, query=state.get("current_query"))
        return {"is_relevant": relevant}

    # Routing condition after grading
    def grade_condition(state: CorrectiveRAGState, runtime: Runtime[AgentRuntimeContext]) -> Literal["generate", "rewrite_query", "fallback"]:
        if state.get("is_relevant"):
            return "generate"
        if state.get("rewrite_count", 0) < max_rewrites:
            return "rewrite_query"
        return "fallback"

    # 3. Rewrite Query Node
    async def rewrite_query_node(state: CorrectiveRAGState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        question = state.get("question", "")
        current_query = state.get("current_query", question)
        rewrite_count = state.get("rewrite_count", 0) + 1

        prompt = REWRITE_QUERY_PROMPT.format(question=question, current_query=current_query)
        try:
            rewrite_res = await llm.ainvoke([HumanMessage(content=prompt)])
            new_query = strip_thinking(str(rewrite_res.content)).strip().strip('"\'')
            if not new_query or new_query.lower() == current_query.lower():
                # Extract simple keywords if model didn't alter
                words = [w for w in re.findall(r"\w+", question) if len(w) > 3]
                new_query = " ".join(words[:4]) or question
        except Exception as exc:
            logger.warning("Rewrite query failed: %s", exc)
            new_query = question

        record("crag_rewrite_query", attempt=rewrite_count, old_query=current_query, new_query=new_query)
        return {
            "current_query": new_query,
            "rewrite_count": rewrite_count,
        }

    # 4. Generate Node
    async def generate_node(state: CorrectiveRAGState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        question = state.get("question", "")
        docs = state.get("retrieved_docs", "")
        sys_prompt = system_prompt or DOCUMENT_TURN_PROMPT

        user_content = f"Справочные данные из базы знаний:\n\n{docs}\n\nВопрос пользователя:\n{question}"
        try:
            gen_res = await llm.ainvoke([
                SystemMessage(content=sys_prompt),
                HumanMessage(content=user_content),
            ])
            draft = strip_thinking(str(gen_res.content)).strip()
        except Exception as exc:
            logger.error("Generation failed: %s", exc)
            draft = NOT_FOUND_PHRASE

        record("crag_generate", length=len(draft))
        return {"draft_answer": draft}

    # 5. Check Groundedness Node (Self-RAG)
    async def check_groundedness_node(state: CorrectiveRAGState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        docs = state.get("retrieved_docs", "")
        draft = state.get("draft_answer", "")
        question = state.get("question", "")

        # If already acknowledged lack of information
        if NOT_FOUND_PHRASE in draft:
            return {"is_grounded": True}

        prompt = CHECK_GROUNDEDNESS_PROMPT.format(documents=docs[:3000], answer=draft)
        try:
            eval_res = await llm.ainvoke([HumanMessage(content=prompt)])
            grounded = is_affirmative(str(eval_res.content))
        except Exception as exc:
            logger.warning("Groundedness evaluation failed: %s", exc)
            grounded = True

        record("crag_check_groundedness", grounded=grounded)
        if not grounded:
            # Replace hallucinated draft with a cautious, grounded notification
            draft = (
                f"В найденных материалах компании недостаточно подтверждённых сведений для полного ответа "
                f"на вопрос «{question}». Рекомендуется уточнить запрос или обратиться к ответственным сотрудникам."
            )

        return {"is_grounded": grounded, "draft_answer": draft}

    # 6. Fallback Node
    def fallback_node(state: CorrectiveRAGState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        question = state.get("question", "")
        answer = f"{NOT_FOUND_PHRASE}\nПо вопросу «{question}» в регламентах и документах компании сведений не найдено."
        record("crag_fallback", question=question, attempts=state.get("rewrite_count", 0))
        return {
            "draft_answer": answer,
            "raw_reference": "",
        }

    # 7. Finalize Node
    def finalize_node(state: CorrectiveRAGState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        raw_ref = state.get("raw_reference", "")
        draft = state.get("draft_answer", "")

        sources = sources_from_reference(raw_ref) if raw_ref else []
        cited = cited_sources(draft, sources)
        cleaned = clean_answer(draft, cited)

        aimsg = AIMessage(content=cleaned)
        finalized = finalize_response(aimsg, cited, active=None)
        return {"messages": [finalized]}

    # Graph Assembly
    builder = StateGraph(CorrectiveRAGState, context_schema=AgentRuntimeContext)
    builder.add_node("retrieve", retrieve_node)
    builder.add_node("grade_documents", grade_documents_node)
    builder.add_node("rewrite_query", rewrite_query_node)
    builder.add_node("generate", generate_node)
    builder.add_node("check_groundedness", check_groundedness_node)
    builder.add_node("fallback", fallback_node)
    builder.add_node("finalize", finalize_node)

    builder.add_edge(START, "retrieve")
    builder.add_edge("retrieve", "grade_documents")
    builder.add_conditional_edges(
        "grade_documents",
        grade_condition,
        {
            "generate": "generate",
            "rewrite_query": "rewrite_query",
            "fallback": "fallback",
        },
    )
    builder.add_edge("rewrite_query", "retrieve")
    builder.add_edge("generate", "check_groundedness")
    builder.add_edge("check_groundedness", "finalize")
    builder.add_edge("fallback", "finalize")
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer)

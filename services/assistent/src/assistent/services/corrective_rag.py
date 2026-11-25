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
from langgraph.graph import END, START, StateGraph
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
    finalize_response,
    history_for_turn,
    last_question,
)
from assistent.services.answer_sources import (
    cited_sources,
    clean_answer,
    readable_reference,
    sources_from_reference,
    strip_thinking,
)
from assistent.services.diagnostics import record
from assistent.prompts.corrective_rag_prompts import (
    GRADE_DOCUMENTS_PROMPT,
    REWRITE_QUERY_PROMPT,
    CHECK_GROUNDEDNESS_PROMPT
)

logger = logging.getLogger(__name__)

_CONTEXT_BLOCK_RE = re.compile(r"<context\b[^>]*>[\s\S]*?</context>", re.IGNORECASE)
_CATALOG_QUERY_RE = re.compile(
    r"(?:список|перечень|какие)\s+(?:документ|инструкци|регламент)",
    re.IGNORECASE,
)



class CorrectiveRAGState(AgentState, total=False):
    question: str
    current_query: str
    retrieved_docs: str
    raw_reference: str
    rewrite_count: int
    is_rewrite_hop: bool
    is_relevant: bool | None
    is_grounded: bool | None
    draft_answer: str


def filter_context_by_indices(raw_reference: str, indices: Sequence[int]) -> str:
    """Filter XML <context> fragments by 1-based indices (Filter-Reranker paradigm)."""
    matches = list(_CONTEXT_BLOCK_RE.finditer(raw_reference))
    if not matches:
        return raw_reference
    target_set = set(indices)
    selected_chunks = [
        match.group(0)
        for idx, match in enumerate(matches, 1)
        if idx in target_set
    ]
    return "\n\n".join(selected_chunks) if selected_chunks else raw_reference


def is_affirmative(text: str) -> bool:
    """Checks whether cleaned LLM verdict is affirmative without false positives on 'нерелевантно'."""
    clean = strip_thinking(text).strip().lower()
    if not clean or clean.startswith("no") or clean.startswith("нет") or "нерелевант" in clean or "не релевант" in clean:
        return False
    return (
        clean.startswith("yes")
        or clean.startswith("да")
        or bool(re.search(r"\bрелевант", clean))
    )


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
    async def retrieve_node(
        state: CorrectiveRAGState,
        runtime: Runtime[AgentRuntimeContext],
    ) -> dict[str, Any]:
        history, boundary = history_for_turn(state, runtime)
        is_rewrite_hop = bool(state.get("is_rewrite_hop", False))

        if is_rewrite_hop:
            question = state.get("question") or last_question(history)
            current_query = state.get("current_query") or question
            rewrite_count = state.get("rewrite_count", 0)
        else:
            # Новый ход диалога: сбрасываем состояние предыдущего хода из checkpointer
            question = last_question(history) or state.get("question", "")
            current_query = question
            rewrite_count = 0

        user_context = ctx(runtime).get("user_context", {})
        base_turn_state: dict[str, Any] = {
            **boundary,
            "question": question,
            "current_query": current_query,
            "rewrite_count": rewrite_count,
            "is_rewrite_hop": False,
            "is_relevant": None,
            "is_grounded": None,
            "draft_answer": "",
        }

        # Check for catalog inquiry on initial turn
        is_catalog = not is_rewrite_hop and bool(_CATALOG_QUERY_RE.search(question))
        if is_catalog and list_tool:
            try:
                res = await list_tool.ainvoke(
                    {"query": current_query},
                    config={"configurable": {"user_context": user_context}},
                )
                content = res[0] if isinstance(res, tuple) else str(res)
                record("crag_retrieve", query=current_query, mode="catalog", length=len(content))
                return {
                    **base_turn_state,
                    "retrieved_docs": content,
                    "raw_reference": "",
                    "is_relevant": bool(content and NOT_FOUND_PHRASE not in content),
                }
            except Exception as exc:
                logger.warning("Catalog search tool failed: %s", exc)

        raw_ref = ""
        if search_tool:
            try:
                raw_res = await search_tool.ainvoke(
                    {"query": current_query},
                    config={"configurable": {"user_context": user_context}},
                )
                raw_ref = str(raw_res) if raw_res else ""
            except Exception as exc:
                logger.warning("Knowledge search tool failed: %s", exc)
                raw_ref = ""
        elif ctx(runtime).get("reference_context"):
            raw_ref = ctx(runtime).get("reference_context", "")

        readable = readable_reference(raw_ref, max_chars=context_max_chars) if raw_ref else ""
        record("crag_retrieve", query=current_query, chars=len(readable), raw_chars=len(raw_ref))
        return {
            **base_turn_state,
            "retrieved_docs": readable,
            "raw_reference": raw_ref,
        }

    # 2. Grade Documents Node
    async def grade_documents_node(
        state: CorrectiveRAGState,
        runtime: Runtime[AgentRuntimeContext],
    ) -> dict[str, Any]:
        docs = state.get("retrieved_docs", "").strip()
        question = state.get("question", "")

        if state.get("is_relevant") is True:
            return {"is_relevant": True}

        docs_lower = docs.lower()
        if (
            not docs
            or NOT_FOUND_PHRASE in docs
            or "ничего не найдено" in docs_lower
            or "ошибка при обращении" in docs_lower
            or "база знаний временно недоступна" in docs_lower
        ):
            record("crag_grade_documents", relevant=False, fast_path=True)
            return {"is_relevant": False}

        raw_ref = state.get("raw_reference", "")
        matches_count = len(_CONTEXT_BLOCK_RE.findall(raw_ref))
        prompt = GRADE_DOCUMENTS_PROMPT.format(
            question=question,
            documents=docs[:context_max_chars],
        )

        eval_text = ""
        try:
            eval_res = await llm.ainvoke([HumanMessage(content=prompt)])
            eval_text = strip_thinking(str(eval_res.content)).strip()

            # Filter-Reranker paradigm: extract specific chunk indices from cleaned response
            if not eval_text.lower().startswith("no") and not eval_text.lower().startswith("нет"):
                chunk_nums = [int(n) for n in re.findall(r"\b\d+\b", eval_text)]
                valid_nums = list(dict.fromkeys(n for n in chunk_nums if 1 <= n <= matches_count))
            else:
                valid_nums = []

            if valid_nums and matches_count >= 1:
                filtered_raw = (
                    filter_context_by_indices(raw_ref, valid_nums)
                    if len(valid_nums) < matches_count
                    else raw_ref
                )
                filtered_docs = readable_reference(filtered_raw, max_chars=context_max_chars)
                record(
                    "crag_grade_documents",
                    relevant=True,
                    method="filter_reranker",
                    filtered_chunks=valid_nums,
                    total_chunks=matches_count,
                    noise_removed=matches_count - len(valid_nums),
                    eval_output=eval_text,
                    query=state.get("current_query"),
                )
                return {
                    "is_relevant": True,
                    "retrieved_docs": filtered_docs,
                    "raw_reference": filtered_raw,
                }

            relevant = is_affirmative(eval_text)
        except Exception as exc:
            logger.warning("Document grading failed, assuming relevant: %s", exc)
            relevant = True
            eval_text = str(exc)

        record(
            "crag_grade_documents",
            relevant=relevant,
            method="binary",
            total_chunks=matches_count,
            eval_output=eval_text,
            query=state.get("current_query"),
        )
        return {"is_relevant": relevant}

    def grade_condition(
        state: CorrectiveRAGState,
        runtime: Runtime[AgentRuntimeContext],
    ) -> Literal["generate", "rewrite_query", "fallback"]:
        if state.get("is_relevant"):
            return "generate"
        if state.get("rewrite_count", 0) < max_rewrites:
            return "rewrite_query"
        return "fallback"

    # 3. Rewrite Query Node
    async def rewrite_query_node(
        state: CorrectiveRAGState,
        runtime: Runtime[AgentRuntimeContext],
    ) -> dict[str, Any]:
        question = state.get("question", "")
        current_query = state.get("current_query", question)
        rewrite_count = state.get("rewrite_count", 0) + 1

        prompt = REWRITE_QUERY_PROMPT.format(question=question, current_query=current_query)
        try:
            rewrite_res = await llm.ainvoke([HumanMessage(content=prompt)])
            new_query = strip_thinking(str(rewrite_res.content)).strip().strip("\"'")
            # Берем только первую непустую строку на случай многострочного вывода
            new_query = next((line.strip() for line in new_query.splitlines() if line.strip()), "")
            if not new_query or new_query.lower() == current_query.lower():
                words = [w for w in re.findall(r"\w+", question) if len(w) > 3]
                new_query = " ".join(words[:5]) or question
        except Exception as exc:
            logger.warning("Rewrite query failed: %s", exc)
            new_query = question

        record(
            "crag_rewrite_query",
            attempt=rewrite_count,
            old_query=current_query,
            new_query=new_query,
            strategy="RRR (Rewrite-Retrieve-Read)",
        )
        return {
            "current_query": new_query,
            "rewrite_count": rewrite_count,
            "is_rewrite_hop": True,
        }

    # 4. Generate Node
    async def generate_node(
        state: CorrectiveRAGState,
        runtime: Runtime[AgentRuntimeContext],
    ) -> dict[str, Any]:
        question = state.get("question", "")
        docs = state.get("retrieved_docs", "")
        sys_prompt = system_prompt or DOCUMENT_TURN_PROMPT or DEFAULT_SYSTEM_PROMPT

        user_content = (
            f"Справочные данные из базы знаний:\n\n{docs}\n\n"
            f"Вопрос пользователя:\n{question}"
        )
        try:
            gen_res = await llm.ainvoke([
                SystemMessage(content=sys_prompt),
                HumanMessage(content=user_content),
            ])
            draft = strip_thinking(str(gen_res.content)).strip()
        except Exception as exc:
            logger.error("Generation failed: %s", exc)
            draft = NOT_FOUND_PHRASE

        record(
            "crag_generate",
            length=len(draft),
            draft_snippet=draft[:400],
            context_chars=len(docs),
        )
        return {"draft_answer": draft}

    # 5. Check Groundedness Node (Self-RAG)
    async def check_groundedness_node(
        state: CorrectiveRAGState,
        runtime: Runtime[AgentRuntimeContext],
    ) -> dict[str, Any]:
        docs = state.get("retrieved_docs", "")
        draft = state.get("draft_answer", "")
        question = state.get("question", "")

        if not draft or NOT_FOUND_PHRASE in draft:
            return {"is_grounded": True}

        prompt = CHECK_GROUNDEDNESS_PROMPT.format(
            documents=docs[:context_max_chars],
            answer=draft,
        )
        try:
            eval_res = await llm.ainvoke([HumanMessage(content=prompt)])
            eval_text = strip_thinking(str(eval_res.content)).strip()
            grounded = is_affirmative(eval_text)
        except Exception as exc:
            logger.warning("Groundedness evaluation failed: %s", exc)
            grounded = True
            eval_text = str(exc)

        record(
            "crag_check_groundedness",
            grounded=grounded,
            eval_output=eval_text,
            context_chars=len(docs),
            draft_chars=len(draft),
            faithfulness_score=1.0 if grounded else 0.0,
        )
        if not grounded:
            draft = (
                f"{NOT_FOUND_PHRASE}\n"
                f"В найденных материалах компании недостаточно подтверждённых сведений для точного ответа "
                f"на вопрос «{question}». Рекомендуется уточнить запрос или обратиться к ответственным сотрудникам."
            )
            return {
                "is_grounded": False,
                "draft_answer": draft,
                "raw_reference": "",
            }

        return {"is_grounded": True, "draft_answer": draft}

    # 6. Fallback Node
    def fallback_node(
        state: CorrectiveRAGState,
        runtime: Runtime[AgentRuntimeContext],
    ) -> dict[str, Any]:
        question = state.get("question", "")
        answer = (
            f"{NOT_FOUND_PHRASE}\n"
            f"По вопросу «{question}» в регламентах и документах компании сведений не найдено."
        )
        record("crag_fallback", question=question, attempts=state.get("rewrite_count", 0))
        return {
            "draft_answer": answer,
            "raw_reference": "",
        }

    # 7. Finalize Node
    def finalize_node(
        state: CorrectiveRAGState,
        runtime: Runtime[AgentRuntimeContext],
    ) -> dict[str, Any]:
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
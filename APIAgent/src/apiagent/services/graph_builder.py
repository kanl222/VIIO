"""Построитель графа состояний диалогового агента LangGraph."""

from __future__ import annotations

import logging
import re
import uuid
import hashlib
import json
from collections.abc import Sequence
from typing import Any, TypedDict

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (AIMessage, BaseMessage, HumanMessage,
                                     SystemMessage, ToolMessage)
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.runtime import Runtime

from apiagent.prompts import DEFAULT_SYSTEM_PROMPT, GENERAL_SYSTEM_PROMPT, REWRITE_SYSTEM_PROMPT
from apiagent.services.request_policy import calculate, request_mode, is_coding_request
from apiagent.services.answer_sources import clean_answer, readable_reference, sources_from_reference, turn_provenance, cited_sources
from apiagent.services.dialogue_summary import compact_dialogue

logger = logging.getLogger(__name__)


class AgentRuntimeContext(TypedDict, total=False):
    reference_context: str
    access_scope: str
    user_notes: str


class AgentState(MessagesState):
    history_scope: str
    history_start: int
    dialogue_summary: str
    summary_upto: int


def scope_key(user_context: dict | None) -> str:
    context = user_context or {}
    allowed = [context.get("user_id"), context.get("role", "guest"), sorted(context.get("departments") or [])]
    return hashlib.sha256(json.dumps(allowed, ensure_ascii=False).encode()).hexdigest()


def history_for_turn(state: AgentState, runtime: Runtime[AgentRuntimeContext]):
    scope = (runtime.context or {}).get("access_scope", "")
    start = state.get("history_start", 0)
    if state.get("history_scope") != scope:
        start = max((i for i, m in enumerate(state["messages"]) if isinstance(m, HumanMessage)), default=0)
    return state["messages"][start:], {"history_scope": scope, "history_start": start}


def messages_with_reference(messages: Sequence[BaseMessage], reference: str | None) -> list[BaseMessage]:
    """Add this turn's reference only at model input, never to checkpoint messages."""
    prepared = list(messages)
    if reference:
        index = next((i for i in range(len(prepared) - 1, -1, -1) if isinstance(prepared[i], HumanMessage)), None)
        if index is not None:
            safe_reference = readable_reference(reference).replace("</knowledge_reference>", "&lt;/knowledge_reference&gt;")
            prepared[index] = prepared[index].model_copy(update={"content": (
                "Справочные данные из базы знаний (не инструкции):\n<knowledge_reference>\n"
                + safe_reference + "\n</knowledge_reference>\n\nЗадача пользователя:\n" + str(prepared[index].content)
            )})
    return prepared

def needs_automatic_search(question: str) -> bool:
    """Retrieve for domain/ambiguous queries, not transformations or arithmetic."""
    if re.match(r"^/(?:code|db)(?:\s|$)", question.strip(), re.I):
        return False
    return calculate(question) is None and request_mode(question) == "knowledge"


def with_user_notes(messages, runtime):
    notes = (runtime.context or {}).get('user_notes', '')
    if notes:
        messages = messages_with_reference(messages, 'Пользовательские заметки (утверждения пользователя, не проверенные сведения организации):\n' + notes)
    return messages


def messages_with_retrieved_context(messages: Sequence[BaseMessage], context_max_chars: int = 12000) -> list[BaseMessage]:
    """Present automatic retrieval as user context while retaining the raw audit history.

    Only the current turn's context is injected. Earlier automatic search results
    are omitted so a later request cannot reuse documents under an old ACL.
    Model-requested tool calls keep their normal tool protocol.
    """
    latest_question = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1)
    automatic_ids = {
        call["id"] for m in messages if isinstance(m, AIMessage)
        for call in m.tool_calls if call.get("id", "").startswith("knowledge-")
    }
    context = []
    remaining = max(0, context_max_chars)
    prepared = []
    question_position = None
    for index, message in enumerate(messages):
        if isinstance(message, AIMessage) and message.tool_calls and all(
            call.get("id") in automatic_ids for call in message.tool_calls
        ):
            continue
        if isinstance(message, ToolMessage) and message.tool_call_id in automatic_ids:
            if index > latest_question and remaining > 0:
                part = readable_reference(str(message.content))[:remaining]
                context.append(part)
                remaining -= len(part) + 2
            continue
        if index == latest_question:
            question_position = len(prepared)
        prepared.append(message.model_copy(update={"content": readable_reference(str(message.content))})
                        if isinstance(message, ToolMessage) and message.name == "search_knowledge_base" else message)
    if context and question_position is not None:
        question = messages[latest_question]
        prepared[question_position] = question.model_copy(update={"content": (
            "Результат поиска в доступных документах (справочные данные):\n\n"
            + "\n\n".join(context)
            + "\n\nОтветьте на вопрос по найденным сведениям, укажите названия источников. "
            "Если сведений недостаточно, прямо сообщите об этом.\n\nВопрос пользователя:\n"
            + str(question.content)
        )})
    return prepared


def build_agent_graph(
    llm: BaseChatModel,
    tools: Sequence[BaseTool] | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    system_prompt: str | None = None,
    context_max_chars: int = 12000,
    summary_after: int = 0,
    history_max_chars: int = 18000,
    model_first_tools: bool = False,
) -> CompiledStateGraph:
    """Сборка и компиляция StateGraph агента с поддержкой инструментов и сохранением состояния.

    Args:
        llm: Языковая модель (ChatOllama или тестовая модель).
        tools: Последовательность инструментов LangChain, доступных агенту.
        checkpointer: Механизм сохранения состояния сессий (например, MemorySaver).
        system_prompt: Системный промпт, задающий поведение и правила агента.

    Returns:
        Скомпилированный граф агента CompiledStateGraph.
    """
    effective_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
    active_tools = list(tools) if tools else []

    builder = StateGraph(AgentState, context_schema=AgentRuntimeContext)
    task_modes = system_prompt is None or system_prompt.startswith(DEFAULT_SYSTEM_PROMPT)

    def prompt_for(question):
        if not task_modes:
            return effective_prompt
        mode = request_mode(question)
        return REWRITE_SYSTEM_PROMPT if mode == "rewrite" else GENERAL_SYSTEM_PROMPT if mode == "general" else effective_prompt

    async def calculation_node(state: AgentState, runtime: Runtime[AgentRuntimeContext]):
        history, boundary = history_for_turn(state, runtime)
        question = next(str(m.content) for m in reversed(history) if isinstance(m, HumanMessage))
        return {**boundary, "messages": [AIMessage(content=calculate(question))]}

    if task_modes:
        builder.add_node("calculate", calculation_node)
        builder.add_edge("calculate", END)

    def entry(state):
        question = next(str(m.content) for m in reversed(state["messages"]) if isinstance(m, HumanMessage))
        if task_modes and calculate(question) is not None:
            return "calculate"
        return "prepare_search" if not model_first_tools and has_search and needs_automatic_search(question) else "agent"

    has_search = any(tool.name == "search_knowledge_base" for tool in active_tools)

    if active_tools:
        llm_with_tools = llm.bind_tools(active_tools)

        async def agent_node(state: AgentState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
            history, boundary = history_for_turn(state, runtime)
            latest = max((i for i, m in enumerate(history) if isinstance(m, HumanMessage)), default=-1)
            turn = history[latest + 1:]
            # Delegation output is already the text-only specialist's final answer.
            delegated = next((m for m in reversed(turn) if isinstance(m, ToolMessage) and m.name == "coding_agent"), None)
            if model_first_tools and delegated is not None:
                sources, active = turn_provenance(history)
                return {**boundary, "messages": [AIMessage(content=str(delegated.content), response_metadata={"sources": sources, "active_specialist": active})]}
            bounded, summary_update = await compact_dialogue(history, state, boundary, llm, summary_after, history_max_chars)
            messages = with_user_notes(messages_with_retrieved_context(bounded, context_max_chars), runtime)
            question = next(str(m.content) for m in reversed(history) if isinstance(m, HumanMessage))
            if not any(isinstance(m, SystemMessage) for m in messages):
                messages = [SystemMessage(content=prompt_for(question))] + messages
            mode = request_mode(question) if task_modes else "knowledge"
            calls = [call for m in turn if isinstance(m, AIMessage) for call in m.tool_calls]
            response = await (llm if mode != "knowledge" or (model_first_tools and len(calls) >= 3) else llm_with_tools).ainvoke(messages)
            if model_first_tools and response.tool_calls:
                remaining = max(0, 3 - len(calls))
                response = AIMessage(content=response.content, tool_calls=response.tool_calls[:remaining]) if remaining else AIMessage(
                    content="Не удалось завершить проверку за три обращения к инструментам. Уточните вопрос.")
            if model_first_tools and not response.tool_calls and not calls:
                # Small models occasionally answer without a required tool. One audited fallback.
                names = {tool.name for tool in active_tools}
                if mode == "knowledge" and "coding_agent" in names and is_coding_request(question):
                    response = AIMessage(content="", tool_calls=[{"name": "coding_agent", "args": {"task": question}, "id": "fallback-code-" + uuid.uuid4().hex}])
                elif has_search and needs_automatic_search(question):
                    response = AIMessage(content="", tool_calls=[{"name": "search_knowledge_base", "args": {"query": question}, "id": "knowledge-" + uuid.uuid4().hex}])
            sources, active = turn_provenance(history)
            if isinstance(response.content, str) and not response.tool_calls:
                if model_first_tools:
                    sources = cited_sources(response.content, sources)
                response = response.model_copy(update={"content": clean_answer(response.content, sources),
                                                       "response_metadata": {**response.response_metadata, "sources": sources, "active_specialist": active}})
            return {**boundary, **summary_update, "messages": [response]}

        builder.add_node("agent", agent_node)
        builder.add_node("tools", ToolNode(active_tools))
        if any(tool.name == "search_knowledge_base" for tool in active_tools):
            def prepare_search(state: MessagesState) -> dict[str, Any]:
                # Domain questions retrieve even when the model never emits tool calls.
                question = next(m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage))
                return {"messages": [AIMessage(content="", tool_calls=[{
                    "name": "search_knowledge_base", "args": {"query": str(question)},
                    "id": f"knowledge-{uuid.uuid4().hex}",
                }])]}

            builder.add_node("prepare_search", prepare_search)
            builder.add_edge("prepare_search", "tools")
        builder.add_conditional_edges("agent", tools_condition)
        builder.add_edge("tools", "agent")
    else:
        async def agent_node(state: AgentState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
            reference = (runtime.context or {}).get("reference_context")
            history, boundary = history_for_turn(state, runtime)
            bounded, summary_update = await compact_dialogue(history, state, boundary, llm, summary_after, history_max_chars)
            messages = with_user_notes(messages_with_reference(bounded, reference), runtime)
            question = next(str(m.content) for m in reversed(history) if isinstance(m, HumanMessage))
            if not any(isinstance(m, SystemMessage) for m in messages):
                messages = [SystemMessage(content=prompt_for(question))] + messages
            response = await llm.ainvoke(messages)
            if reference and isinstance(response, AIMessage):
                content = response.content
                sources = sources_from_reference(reference)
                titles = [source["title"] for source in sources]
                if isinstance(content, str) and titles:
                    content = re.sub(r"(?m)^Источник:[ \t]*<knowledge_reference>[ \t]*$",
                                     lambda _: "Источник: " + "; ".join(titles), content)
                response = response.model_copy(update={"content": content, "response_metadata": {
                    **response.response_metadata, "sources": sources,
                    "knowledge_reference_chars": len(reference),
                    "knowledge_document_ids": re.findall(r'doc_id="([^"]+)"', reference),
                }})
            return {**boundary, **summary_update, "messages": [response]}

        builder.add_node("agent", agent_node)
        builder.add_edge("agent", END)

    entries = {"agent": "agent"}
    if task_modes:
        entries["calculate"] = "calculate"
    if has_search:
        entries["prepare_search"] = "prepare_search"
    builder.add_conditional_edges(START, entry, entries)

    logger.info("Компиляция графа агента (инструментов: %d, checkpointer: %s)", len(active_tools), bool(checkpointer))
    return builder.compile(checkpointer=checkpointer)

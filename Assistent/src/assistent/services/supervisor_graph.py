"""Supervisor StateGraph for multi-agent routing and orchestration in LangGraph.

Nodes:
- route: Intent classification and specialist selection
- direct: Instant responses for calculations, text rewrites, and simple pleasantries
- knowledge: RAG & company document Q&A
- coding: Coding agent & GitHub inspection
- database: PostgreSQL inspection and read queries
- review: Provenance assembly, sources filtering, clean-up
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime

from assistent.prompts import (
    DEFAULT_SYSTEM_PROMPT,
    GENERAL_SYSTEM_PROMPT,
    REWRITE_SYSTEM_PROMPT,
    CODING_SYSTEM_PROMPT,
    DATABASE_SYSTEM_PROMPT,
)
from assistent.services.agent_policy import (
    AgentRuntimeContext,
    AgentState,
    classify,
    ctx,
    history_for_turn,
    last_human_index,
    last_question,
    turn_provenance,
    cited_sources,
    clean_answer,
    finalize_response,
    attach_reference_sources,
)
from assistent.services.agent_runtime import build_runtime
from assistent.services.request_policy import is_coding_request
from assistent.services.database_agent import is_database_request
from assistent.services.dialogue_summary import compact_dialogue
from assistent.services.answer_sources import strip_thinking
from asyncio import timeout
from assistent.services.diagnostics import model_call, record
from assistent.security import has_role_access
from assistent.tools.postgres_tool import DATABASE_ROLES

logger = logging.getLogger(__name__)


def classify_route(question: str, tools_available: set[str], user_context: dict[str, Any]) -> str:
    """Classify user request to choose the appropriate specialist node."""
    # 1. Direct calculations
    info = classify(question)
    if info.calc is not None:
        return "direct"

    # 2. Database inquiries (/db or question about DB/tables/schema)
    clean_q = question.strip().strip("«»\"'").strip()
    parts = clean_q.split(maxsplit=1)
    if parts and parts[0].lower() == "/db":
        if "postgresql_readonly" in tools_available:
            return "database"
    elif is_database_request(clean_q):
        if "postgresql_readonly" in tools_available and has_role_access(user_context, DATABASE_ROLES):
            return "database"

    # 3. Coding requests (/code or coding intent)
    if (parts and parts[0].lower() == "/code") or is_coding_request(clean_q):
        if any(t in tools_available for t in ("github_repository", "coding_agent")):
            return "coding"

    # 4. Text rewrite / General pleasantries
    if info.mode in ("rewrite", "general"):
        return "direct"

    # 5. Default domain / knowledge base
    return "knowledge"


def build_supervisor_graph(
    llm: BaseChatModel,
    tools: Sequence[BaseTool] | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    system_prompt: str | None = None,
    context_max_chars: int = 12000,
    summary_after: int = 0,
    history_max_chars: int = 18000,
    model_first_tools: bool = True,
    max_tool_calls: int = 6,
    corrective_rag: bool = False,
    database_analyst: bool = True,
    routing_llm: BaseChatModel | None = None,
    **extra_kwargs: Any,
) -> CompiledStateGraph:
    """Constructs a Supervisor StateGraph that orchestrates specialized sub-runtimes."""
    active_tools = list(tools or [])
    tools_by_name = {t.name: t for t in active_tools}
    all_tool_names = set(tools_by_name.keys())

    # Build specialized sub-runtimes with isolated tools & prompts
    # 1. Knowledge Specialist (search_knowledge_base, search_knowledge_graph, list_documents, search_document_content, manage_plan, save_knowledge, skills)
    knowledge_tools = [t for t in active_tools if t.name in (
        "search_knowledge_base", "search_knowledge_graph", "list_documents", "search_document_content", "manage_plan", "save_knowledge",
        "list_agent_skills", "activate_agent_skill"
    )]
    if corrective_rag:
        from assistent.services.corrective_rag import build_corrective_rag_graph
        knowledge_runtime = build_corrective_rag_graph(
            llm=llm,
            tools=knowledge_tools,
            checkpointer=False,
            system_prompt=system_prompt or DEFAULT_SYSTEM_PROMPT,
            context_max_chars=context_max_chars,
            history_max_chars=history_max_chars,
            max_rewrites=2,
        )
    else:
        knowledge_runtime = build_runtime(
            llm=llm,
            tools=knowledge_tools,
            checkpointer=False,
            system_prompt=system_prompt or DEFAULT_SYSTEM_PROMPT,
            context_max_chars=context_max_chars,
            summary_after=0,  # History compaction handled at supervisor level
            history_max_chars=history_max_chars,
            model_first_tools=model_first_tools,
            task_routing=False,
            max_tool_calls=max_tool_calls,
        )

    # 2. Coding Specialist can consult indexed code, GitHub, and procedural skills.
    coding_tools = [t for t in active_tools if t.name in (
        "github_repository", "coding_agent", "search_knowledge_base", "manage_plan",
        "list_agent_skills", "activate_agent_skill"
    )]
    coding_runtime = build_runtime(
        llm=llm,
        tools=coding_tools,
        checkpointer=False,
        system_prompt=CODING_SYSTEM_PROMPT,
        context_max_chars=context_max_chars,
        summary_after=0,
        history_max_chars=history_max_chars,
        model_first_tools=model_first_tools,
        task_routing=False,
        max_tool_calls=max_tool_calls,
    )

    # 3. Database Specialist (Interactive Text-to-SQL Analyst or Runtime)
    database_tools = [t for t in active_tools if t.name in ("postgresql_readonly",)]
    if database_analyst and any(t.name == "postgresql_readonly" for t in active_tools):
        from assistent.services.database_subgraph import build_database_analyst_graph
        database_runtime = build_database_analyst_graph(
            llm=llm,
            tools=database_tools,
            checkpointer=False,
            system_prompt=DATABASE_SYSTEM_PROMPT,
        )
    else:
        database_runtime = build_runtime(
            llm=llm,
            tools=database_tools,
            checkpointer=False,
            system_prompt=DATABASE_SYSTEM_PROMPT,
            context_max_chars=context_max_chars,
            summary_after=0,
            history_max_chars=history_max_chars,
            model_first_tools=model_first_tools,
            task_routing=False,
            max_tool_calls=max_tool_calls,
        )

    # Direct Specialist: handles rewrite & polite conversational phrases without tools
    direct_runtime = build_runtime(
        llm=llm,
        tools=[],
        checkpointer=False,
        system_prompt=GENERAL_SYSTEM_PROMPT,
        context_max_chars=context_max_chars,
        summary_after=0,
        history_max_chars=history_max_chars,
        model_first_tools=model_first_tools,
        task_routing=False,
        max_tool_calls=1,
    )

    # Supervisor Node: Route determination
    async def route_node(state: AgentState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        history, boundary = history_for_turn(state, runtime)
        question = last_question(history)
        user_context = ctx(runtime).get("user_context", {})
        decision = classify_route(question, all_tool_names, user_context)
        origin = 'rules'
        if routing_llm is not None and decision != 'direct' and not question.lstrip().startswith('/'):
            choices = ['knowledge', 'direct']
            if {'github_repository', 'coding_agent'} & all_tool_names:
                choices.append('coding')
            if 'postgresql_readonly' in all_tool_names and has_role_access(user_context, DATABASE_ROLES):
                choices.append('database')
            try:
                async with timeout(5):
                    examples: list[str] = []
                    if 'direct' in choices:
                        examples.append('Запрос: Привет -> direct')
                        examples.append('Запрос: Расскажи шутку или анекдот -> direct')
                    if 'knowledge' in choices:
                        examples.append('Запрос: Как оформить отпуск по регламенту? -> knowledge')
                    if 'coding' in choices:
                        examples.append('Запрос: Напиши функцию на python -> coding')
                    if 'database' in choices:
                        examples.append('Запрос: Покажи таблицы базы данных -> database')

                    prompt_parts = [
                        '/no_think',
                        'Ты — классификатор запросов. Верни ровно одну метку из: ' + ', '.join(choices) + '.',
                        'knowledge — регламенты, документы компании.',
                        'direct — разговоры, шутки, общие вопросы.',
                    ]
                    if 'coding' in choices:
                        prompt_parts.append('coding — написание и просмотр кода.')
                    if 'database' in choices:
                        prompt_parts.append('database — таблицы и схемы БД.')
                    if examples:
                        prompt_parts.append('\nПримеры:\n' + '\n'.join(examples))
                    prompt_parts.append('\nВерни ТОЛЬКО ОДНО СЛОВО метки из списка.')

                    result = await model_call(routing_llm, [
                        SystemMessage(content='\n'.join(prompt_parts)),
                        HumanMessage(content=question),
                    ])
                clean_content = strip_thinking(str(result.content)).strip().lower()
                if clean_content in choices:
                    decision, origin = clean_content, 'model'
                else:
                    match = re.search(r'\b(' + '|'.join(re.escape(c) for c in choices) + r')\b', clean_content)
                    if match:
                        decision, origin = match.group(1), 'model'
            except Exception as error:
                logger.warning('Routing model unavailable: %s', type(error).__name__)
        record("route", mode=decision, supervisor=True, tools=sorted(all_tool_names), branch=decision, origin=origin)
        return {**boundary, 'supervisor_route': decision}

    def route_condition(state: AgentState, runtime: Runtime[AgentRuntimeContext]) -> Literal["knowledge", "coding", "database", "direct"]:
        return state['supervisor_route']

    # Specialist Nodes
    async def knowledge_node(state: AgentState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        history, _ = history_for_turn(state, runtime)
        question = last_question(history)
        record("specialist_dispatch", specialist="knowledge", name="Аналитик базы знаний (RAG)", question=question[:200])
        return await knowledge_runtime.ainvoke(state, context=ctx(runtime))

    async def coding_node(state: AgentState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        history, _ = history_for_turn(state, runtime)
        question = last_question(history)
        record("specialist_dispatch", specialist="coding", name="Инженер-программист (Coding Specialist)", question=question[:200])
        return await coding_runtime.ainvoke(state, context=ctx(runtime))

    async def database_node(state: AgentState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        history, _ = history_for_turn(state, runtime)
        question = last_question(history)
        record("specialist_dispatch", specialist="database", name="Аналитик базы данных (Database Analyst)", question=question[:200])
        return await database_runtime.ainvoke(state, context=ctx(runtime))

    # Direct Specialist: for arithmetic, answer immediately; for general text, call llm
    async def direct_node(state: AgentState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        history, boundary = history_for_turn(state, runtime)
        question = last_question(history)
        info = classify(question)
        if info.calc is not None:
            record("specialist_dispatch", specialist="direct_calc", name="Прямой расчет (Арифметика)", result=info.calc)
            return {"messages": [AIMessage(content=info.calc)]}
        record("specialist_dispatch", specialist="direct", name="Общий диалог без инструментов", question=question[:200])
        return await direct_runtime.ainvoke(state, context=ctx(runtime))

    # Supervisor Review Node: Final response formatting and provenance
    def review_node(state: AgentState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        messages = state["messages"]
        if not messages or not isinstance(messages[-1], AIMessage):
            return {}
        last = messages[-1]
        sources, active = turn_provenance(messages)
        reference = ctx(runtime).get("reference_context")
        if reference:
            last = attach_reference_sources(last, reference)
            sources = last.response_metadata.get("sources", sources)
            sources = cited_sources(str(last.content), sources)
        elif "sources" in last.response_metadata:
            sources = last.response_metadata["sources"]
        else:
            sources = cited_sources(str(last.content), sources)
        cleaned = clean_answer(last.content, sources)
        finalized = finalize_response(last, sources, active, content=cleaned)
        record("supervisor_review", active=active, sources=[s.get("title") if isinstance(s, dict) else str(s) for s in sources])
        return {"messages": [finalized]}

    # Graph Assembly
    builder = StateGraph(AgentState, context_schema=AgentRuntimeContext)
    builder.add_node("route", route_node)
    builder.add_node("knowledge", knowledge_node)
    builder.add_node("coding", coding_node)
    builder.add_node("database", database_node)
    builder.add_node("direct", direct_node)
    builder.add_node("review", review_node)

    builder.add_edge(START, "route")
    builder.add_conditional_edges(
        "route",
        route_condition,
        {
            "knowledge": "knowledge",
            "coding": "coding",
            "database": "database",
            "direct": "direct",
        },
    )

    builder.add_edge("knowledge", "review")
    builder.add_edge("coding", "review")
    builder.add_edge("database", "review")
    builder.add_edge("direct", "review")
    builder.add_edge("review", END)

    return builder.compile(checkpointer=checkpointer)

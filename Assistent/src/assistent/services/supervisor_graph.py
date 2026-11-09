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
from assistent.services.diagnostics import record
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
    parts = question.strip().split(maxsplit=1)
    if (parts and parts[0].lower() == "/db") or is_database_request(question):
        if "postgresql_readonly" in tools_available and has_role_access(user_context, DATABASE_ROLES):
            return "database"

    # 3. Coding requests (/code or coding intent)
    if (parts and parts[0].lower() == "/code") or is_coding_request(question):
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
    **extra_kwargs: Any,
) -> CompiledStateGraph:
    """Constructs a Supervisor StateGraph that orchestrates specialized sub-runtimes."""
    active_tools = list(tools or [])
    tools_by_name = {t.name: t for t in active_tools}
    all_tool_names = set(tools_by_name.keys())

    # Build specialized sub-runtimes with isolated tools & prompts
    # 1. Knowledge Specialist (search_knowledge_base, list_documents, search_document_content, manage_plan, save_knowledge)
    knowledge_tools = [t for t in active_tools if t.name in (
        "search_knowledge_base", "list_documents", "search_document_content", "manage_plan", "save_knowledge"
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

    # 2. Coding Specialist (github_repository, coding_agent, manage_plan)
    coding_tools = [t for t in active_tools if t.name in ("github_repository", "coding_agent", "manage_plan")]
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

    # 3. Database Specialist (postgresql_readonly)
    database_tools = [t for t in active_tools if t.name in ("postgresql_readonly",)]
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
    def route_node(state: AgentState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        history, boundary = history_for_turn(state, runtime)
        question = last_question(history)
        user_context = ctx(runtime).get("user_context", {})
        decision = classify_route(question, all_tool_names, user_context)
        record("route", mode=decision, supervisor=True, tools=sorted(all_tool_names))
        return {**boundary}

    def route_condition(state: AgentState, runtime: Runtime[AgentRuntimeContext]) -> Literal["knowledge", "coding", "database", "direct"]:
        history, _ = history_for_turn(state, runtime)
        question = last_question(history)
        user_context = ctx(runtime).get("user_context", {})
        return classify_route(question, all_tool_names, user_context)

    # Specialist Nodes
    async def knowledge_node(state: AgentState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        return await knowledge_runtime.ainvoke(state, context=ctx(runtime))

    async def coding_node(state: AgentState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        return await coding_runtime.ainvoke(state, context=ctx(runtime))

    async def database_node(state: AgentState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        return await database_runtime.ainvoke(state, context=ctx(runtime))

    # Direct Specialist: for arithmetic, answer immediately; for general text, call llm
    async def direct_node(state: AgentState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        history, boundary = history_for_turn(state, runtime)
        question = last_question(history)
        info = classify(question)
        if info.calc is not None:
            return {"messages": [AIMessage(content=info.calc)]}
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
        cleaned = clean_answer(last.content, sources)
        finalized = finalize_response(last, sources, active, content=cleaned)
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

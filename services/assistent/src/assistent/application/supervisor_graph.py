"""Supervisor StateGraph for multi-agent routing and orchestration in LangGraph.

Nodes:
- route: Intent classification and specialist selection (с проверкой ролей)
- direct: Instant responses for calculations and simple pleasantries
- rewrite: Text rewriting with the editor prompt
- knowledge: RAG & company document Q&A
- coding: Coding agent & GitHub inspection
- database: PostgreSQL inspection and read queries
- review: Provenance assembly, sources filtering, clean-up
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from typing import Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime

from assistent.prompts import (
    DATABASE_SYSTEM_PROMPT,
    GENERAL_SYSTEM_PROMPT,
    REWRITE_SYSTEM_PROMPT,
    build_coding_prompt,
    build_system_prompt,
)
from assistent.application.agent_policy import (
    AgentRuntimeContext,
    AgentState,
    attach_reference_sources,
    cited_sources,
    classify,
    clean_answer,
    ctx,
    finalize_response,
    history_for_turn,
    last_human_index,
    last_question,
    turn_provenance,
)
from assistent.application.agent_runtime import _content_to_str, build_runtime
from assistent.application.answer_sources import strip_thinking
from assistent.application.database_agent import is_database_request
from assistent.observability.diagnostics import model_call, record
from assistent.application.multi_hop import decompose_query, execute_multi_hop_retrieval
from assistent.application.request_policy import is_coding_request
from assistent.api.security import has_role_access
from assistent.infrastructure.tools.postgres_tool import DATABASE_ROLES

logger = logging.getLogger(__name__)

DEFAULT_CODING_ROLES = frozenset({"programmer", "admin"})

KNOWLEDGE_TOOL_NAMES = (
    "search_knowledge_base", "search_knowledge_graph", "list_documents",
    "search_document_content", "manage_plan", "save_knowledge",
    "list_agent_skills", "activate_agent_skill",
)
CODING_TOOL_NAMES = (
    "github_repository", "coding_agent", "search_knowledge_base", "manage_plan",
    "list_agent_skills", "activate_agent_skill",
)

# Только явное намерение работать с кодом/репозиторием: слово «репозиторий» само по себе не триггер
_GITHUB_INTENT = re.compile(
    r'\b(?:github|гитхаб|gitlab|коммит\w*|пулл.?реквест\w*)\b'
    r'|\b(?:список|какие|покажи|доступные|структур\w*|ветк\w*)\s+(?:\w+\s+)?(?:репозитор\w*|репоизтор\w*)',
    re.I,
)
_CJK_OR_KANA = re.compile(r'[\u4e00-\u9fff\u3040-\u30ff]')


def is_github_request(question: str) -> bool:
    return bool(_GITHUB_INTENT.search(question))


def route_access(tools_available: set[str], user_context: dict[str, Any],
                 coding_roles: Sequence[str] = DEFAULT_CODING_ROLES) -> tuple[bool, bool]:
    """Единое место решения о доступе: (можно в базу данных, можно в кодинг)."""
    can_db = ("postgresql_readonly" in tools_available
              and has_role_access(user_context, DATABASE_ROLES))
    can_code = has_role_access(user_context, set(coding_roles))
    return can_db, can_code


def classify_route(question: str, tools_available: set[str], user_context: dict[str, Any],
                   coding_roles: Sequence[str] = DEFAULT_CODING_ROLES) -> str:
    """Classify user request to choose the appropriate specialist node.

    Команды /db и /code проходят ту же проверку ролей, что и обычные запросы.
    """
    manual_mode = user_context.get('_agent_mode')
    if manual_mode == 'assistant':
        return 'knowledge'
    if manual_mode in {'coding', 'database'}:
        can_db, can_code = route_access(tools_available, user_context, coding_roles)
        allowed = can_code if manual_mode == 'coding' else can_db
        return manual_mode if allowed else 'knowledge'
    info = classify(question)
    if info.calc is not None:
        return "direct"

    clean_q = question.strip().strip("«»\"'").strip()
    command = (clean_q.split(maxsplit=1) or [""])[0].lower()
    can_db, can_code = route_access(tools_available, user_context, coding_roles)

    if can_db and (command == "/db" or is_database_request(clean_q)):
        return "database"
    if can_code and (command == "/code" or is_coding_request(clean_q) or is_github_request(clean_q)):
        return "coding"
    if info.mode == "rewrite":
        return "rewrite"
    if info.mode == "general":
        return "direct"
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
    cpu_profile: bool = False,
    database_analyst: bool = True,
    routing_llm: BaseChatModel | None = None,
    coding_roles: Sequence[str] = DEFAULT_CODING_ROLES,
    allow_model_direct: bool = False,
    **extra_kwargs: Any,
) -> CompiledStateGraph:
    """Constructs a Supervisor StateGraph that orchestrates specialized sub-runtimes.

    allow_model_direct: разрешить модели-маршрутизатору отправлять запрос в direct (без поиска и источников).
    По умолчанию выключено: выбор direct остаётся за правилами, ошибка модели там обходится дороже лишнего поиска.
    """
    active_tools = list(tools or [])
    tools_by_name = {t.name: t for t in active_tools}
    all_tool_names = set(tools_by_name)
    coding_roles = tuple(coding_roles)

    def sub_runtime(sub_tools, prompt, *, calls=max_tool_calls):
        return build_runtime(
            llm=llm,
            tools=sub_tools,
            checkpointer=False,
            system_prompt=prompt + '\n/no_think' if cpu_profile else prompt,
            context_max_chars=context_max_chars,
            summary_after=summary_after,  # 0 = сжатие выключено; проверьте семантику в compact_dialogue
            history_max_chars=history_max_chars,
            model_first_tools=model_first_tools,
            task_routing=False,
            max_tool_calls=calls,
        )

    # 1. Knowledge Specialist
    knowledge_tools = [t for t in active_tools if t.name in KNOWLEDGE_TOOL_NAMES]
    knowledge_prompt = system_prompt or build_system_prompt({t.name for t in knowledge_tools})
    if cpu_profile:
        from assistent.application.cpu_knowledge import build_cpu_knowledge
        knowledge_runtime = build_cpu_knowledge(llm, knowledge_tools, context_max_chars)
    elif corrective_rag:
        from assistent.application.corrective_rag import build_corrective_rag_graph
        knowledge_runtime = build_corrective_rag_graph(
            llm=llm,
            tools=knowledge_tools,
            checkpointer=False,
            system_prompt=knowledge_prompt,
            context_max_chars=context_max_chars,
            history_max_chars=history_max_chars,
            max_rewrites=2,
        )
    else:
        knowledge_runtime = sub_runtime(knowledge_tools, knowledge_prompt)

    # 2. Coding Specialist: промпт под реально доступные инструменты
    coding_tools = [t for t in active_tools if t.name in CODING_TOOL_NAMES]
    coding_runtime = sub_runtime(coding_tools, build_coding_prompt({t.name for t in coding_tools}))

    # 3. Database Specialist
    database_tools = [t for t in active_tools if t.name == "postgresql_readonly"]
    if database_analyst and database_tools:
        from assistent.application.database_subgraph import build_database_analyst_graph
        database_runtime = build_database_analyst_graph(
            llm=llm,
            tools=database_tools,
            checkpointer=False,
            system_prompt=DATABASE_SYSTEM_PROMPT + '\n/no_think' if cpu_profile else DATABASE_SYSTEM_PROMPT,
        )
    else:
        database_runtime = sub_runtime(database_tools, DATABASE_SYSTEM_PROMPT)

    # 4. Direct и Rewrite: без инструментов, у каждого свой промпт
    direct_runtime = sub_runtime([], GENERAL_SYSTEM_PROMPT, calls=1)
    rewrite_runtime = sub_runtime([], REWRITE_SYSTEM_PROMPT, calls=1)

    async def route_node(state: AgentState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        history, boundary = history_for_turn(state, runtime)
        question = last_question(history)
        user_context = ctx(runtime).get("user_context", {})
        decision = classify_route(question, all_tool_names, user_context, coding_roles)
        origin = "rules"

        can_db, can_code = route_access(all_tool_names, user_context, coding_roles)
        choices = ["knowledge"]
        if allow_model_direct:
            choices.append("direct")
        if can_code:
            choices.append("coding")
        if can_db:
            choices.append("database")

        # Модель уточняет маршрут только для обычных запросов: не для команд, не для прямых ответов и rewrite
        if (not user_context.get('_agent_mode') and routing_llm is not None and len(choices) > 1
                and decision not in ("direct", "rewrite")
                and not question.lstrip().startswith("/")):
            try:
                descriptions = {
                    "knowledge": "knowledge — регламенты, документы компании, термины, инструкции.",
                    "direct": "direct — разговоры, шутки, общие вопросы.",
                    "coding": "coding — написание и просмотр кода, репозитории.",
                    "database": "database — таблицы и схемы БД.",
                }
                examples = {
                    "knowledge": "Запрос: Как оформить отпуск по регламенту? -> knowledge",
                    "direct": "Запрос: Расскажи шутку или анекдот -> direct",
                    "coding": "Запрос: Напиши функцию на python -> coding",
                    "database": "Запрос: Покажи таблицы базы данных -> database",
                }
                routing_prompt = "\n".join([
                    "/no_think",
                    "Ты — классификатор запросов. Верни ровно одну метку из: " + ", ".join(choices) + ".",
                    *(descriptions[c] for c in choices),
                    "",
                    "Примеры:",
                    *(examples[c] for c in choices),
                    "",
                    "Верни ТОЛЬКО ОДНО СЛОВО метки из списка.",
                ])
                result = await asyncio.wait_for(
                    model_call(routing_llm, [
                        SystemMessage(content=routing_prompt),
                        HumanMessage(content=question),
                    ]),
                    timeout=5,
                )
                label = strip_thinking(_content_to_str(result.content)).strip().lower()
                if label not in choices:
                    match = re.search(r"\b(" + "|".join(re.escape(c) for c in choices) + r")\b", label)
                    label = match.group(1) if match else ""
                if label:
                    decision, origin = label, "model"
            except Exception as error:
                logger.warning("Routing model unavailable: %s", type(error).__name__)

        record("route", mode=decision, supervisor=True, tools=sorted(all_tool_names),
               branch=decision, origin=origin)
        return {**boundary, "supervisor_route": decision}

    def route_condition(state: AgentState, runtime: Runtime[AgentRuntimeContext]
                        ) -> Literal["knowledge", "coding", "database", "direct", "rewrite"]:
        return state["supervisor_route"]

    def denied(specialist: str) -> dict[str, Any]:
        record("specialist_denied", specialist=specialist)
        return {"messages": [AIMessage(content="Недостаточно прав для этого режима.")]}

    async def knowledge_node(state: AgentState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        history, _ = history_for_turn(state, runtime)
        question = last_question(history)
        record("specialist_dispatch", specialist="knowledge", name="Аналитик базы знаний (RAG)",
               question=question[:200])

        # Multi-Hop Query Decomposition: декомпозиция составных вопросов к графу и базе документов
        plan = await decompose_query(question, llm=routing_llm)
        if plan.is_multi_hop and len(plan.sub_queries) > 1:
            record("multi_hop_plan", sub_queries=[sq.to_dict() for sq in plan.sub_queries])
            multi_hop_ref, _ = await execute_multi_hop_retrieval(
                plan,
                tools_by_name,
                user_context=ctx(runtime).get("user_context", {}),
                context_max_chars=context_max_chars,
            )
            if multi_hop_ref:
                merged_ctx = dict(ctx(runtime))
                existing = merged_ctx.get("reference_context", "")
                merged_ctx["reference_context"] = f"{existing}\n\n{multi_hop_ref}".strip() if existing else multi_hop_ref
                return await knowledge_runtime.ainvoke(state, context=merged_ctx)

        return await knowledge_runtime.ainvoke(state, context=ctx(runtime))

    async def coding_node(state: AgentState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        # Повторная проверка на случай, если маршрут задан в обход route_node
        _, can_code = route_access(all_tool_names, ctx(runtime).get("user_context", {}), coding_roles)
        if not can_code:
            return denied("coding")
        history, _ = history_for_turn(state, runtime)
        record("specialist_dispatch", specialist="coding", name="Инженер-программист (Coding Specialist)",
               question=last_question(history)[:200])
        return await coding_runtime.ainvoke(state, context=ctx(runtime))

    async def database_node(state: AgentState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        can_db, _ = route_access(all_tool_names, ctx(runtime).get("user_context", {}), coding_roles)
        if not can_db:
            return denied("database")
        history, _ = history_for_turn(state, runtime)
        record("specialist_dispatch", specialist="database", name="Аналитик базы данных (Database Analyst)",
               question=last_question(history)[:200])
        return await database_runtime.ainvoke(state, context=ctx(runtime))

    async def direct_node(state: AgentState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        history, _ = history_for_turn(state, runtime)
        question = last_question(history)
        info = classify(question)
        if info.calc is not None:
            record("specialist_dispatch", specialist="direct_calc", name="Прямой расчет (Арифметика)",
                   result=info.calc)
            return {"messages": [AIMessage(content=info.calc)]}
        record("specialist_dispatch", specialist="direct", name="Общий диалог без инструментов",
               question=question[:200])
        return await direct_runtime.ainvoke(state, context=ctx(runtime))

    async def rewrite_node(state: AgentState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        history, _ = history_for_turn(state, runtime)
        record("specialist_dispatch", specialist="rewrite", name="Редактор текста",
               question=last_question(history)[:200])
        return await rewrite_runtime.ainvoke(state, context=ctx(runtime))

    def review_node(state: AgentState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        messages = state["messages"]
        if not messages or not isinstance(messages[-1], AIMessage):
            return {}
        last = messages[-1]
        content = _content_to_str(last.content)
        candidates, active = turn_provenance(messages)

        reference = ctx(runtime).get("reference_context")
        if reference:
            last = attach_reference_sources(last, reference)
            candidates = last.response_metadata.get("sources", candidates)
            sources = cited_sources(content, candidates)
        elif "sources" in last.response_metadata:
            sources = last.response_metadata["sources"]
        else:
            sources = cited_sources(content, candidates)

        history, _ = history_for_turn(state, runtime)
        question = last_question(history)
        turn = messages[last_human_index(messages) + 1:]
        saved = [m.artifact["saved_document"] for m in turn
                 if isinstance(m, ToolMessage) and m.name == "save_knowledge"
                 and isinstance(m.artifact, dict) and "saved_document" in m.artifact]

        # clean_answer получает всех кандидатов, а не только процитированные: иначе
        # идентификаторы нецитированных документов останутся в тексте
        cleaned = content
        if not last.response_metadata.get('numbered_evidence') and not re.search(r'источник\w*|ссылк\w*|документ\w*|регламент\w*|sources?|references?', question, re.I):
            sources = []
            cleaned = re.split(r'(?im)^\s*(?:\*\*)?Источники?\s*[:：]', cleaned, maxsplit=1)[0].rstrip()
        cleaned = clean_answer(cleaned, candidates, strip_cjk=not _CJK_OR_KANA.search(question))
        finalized = finalize_response(last, sources, active, content=cleaned, saved_documents=saved)
        record("supervisor_review", active=active,
               sources=[s.get("title") if isinstance(s, dict) else str(s) for s in sources])
        return {"messages": [finalized]}

    builder = StateGraph(AgentState, context_schema=AgentRuntimeContext)
    builder.add_node("route", route_node)
    builder.add_node("knowledge", knowledge_node)
    builder.add_node("coding", coding_node)
    builder.add_node("database", database_node)
    builder.add_node("direct", direct_node)
    builder.add_node("rewrite", rewrite_node)
    builder.add_node("review", review_node)

    builder.add_edge(START, "route")
    builder.add_conditional_edges(
        "route",
        route_condition,
        {name: name for name in ("knowledge", "coding", "database", "direct", "rewrite")},
    )
    for name in ("knowledge", "coding", "database", "direct", "rewrite"):
        builder.add_edge(name, "review")
    builder.add_edge("review", END)

    return builder.compile(checkpointer=checkpointer)

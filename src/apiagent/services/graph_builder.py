"""Построитель графа состояний диалогового агента LangGraph."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = (
    "Вы — полезный корпоративный ассистент компании. "
    "Отвечайте на вопросы пользователей четко, вежливо и по существу на русском языке. "
    "При необходимости поиска информации о регламентах, правилах или процедурах компании "
    "всегда используйте инструмент search_knowledge_base."
)


def build_agent_graph(
    llm: BaseChatModel,
    tools: Sequence[BaseTool] | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    system_prompt: str | None = None,
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

    builder = StateGraph(MessagesState)

    if active_tools:
        llm_with_tools = llm.bind_tools(active_tools)

        async def agent_node(state: MessagesState) -> dict[str, Any]:
            messages = list(state["messages"])
            if not any(isinstance(m, SystemMessage) for m in messages):
                messages = [SystemMessage(content=effective_prompt)] + messages
            response = await llm_with_tools.ainvoke(messages)
            return {"messages": [response]}

        builder.add_node("agent", agent_node)
        builder.add_node("tools", ToolNode(active_tools))
        builder.add_edge(START, "agent")
        builder.add_conditional_edges("agent", tools_condition)
        builder.add_edge("tools", "agent")
    else:
        async def agent_node(state: MessagesState) -> dict[str, Any]:
            messages = list(state["messages"])
            if not any(isinstance(m, SystemMessage) for m in messages):
                messages = [SystemMessage(content=effective_prompt)] + messages
            response = await llm.ainvoke(messages)
            return {"messages": [response]}

        builder.add_node("agent", agent_node)
        builder.add_edge(START, "agent")
        builder.add_edge("agent", END)

    logger.info("Компиляция графа агента (инструментов: %d, checkpointer: %s)", len(active_tools), bool(checkpointer))
    return builder.compile(checkpointer=checkpointer)

"""Compatibility facade for the ready-made LangChain agent runtime."""

from collections.abc import Sequence
from typing import Any
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

# Keep existing imports stable for services and checkpoint/test consumers.
from assistent.services.agent_policy import (
    AgentRuntimeContext, AgentState, Classification, AUTO_CALL_PREFIX, CODER_TOOL,
    CODER_LIMIT_MESSAGE, DEFAULT_SYSTEM_PROMPT, FALLBACK_CODE_PREFIX,
    GENERAL_SYSTEM_PROMPT, MAX_TOOL_CALLS, REWRITE_SYSTEM_PROMPT, SEARCH_TOOL,
    TOOL_LIMIT_MESSAGE, attach_reference_sources, cited_sources, classify,
    clean_answer, ctx, ensure_system, finalize_response, forced_tool_call,
    history_for_turn, inject_into_last_question, is_coding_request,
    last_human_index, last_question, messages_with_reference,
    messages_with_retrieved_context, needs_automatic_search, scope_key,
    turn_provenance, with_user_notes, wrap_block,
)


def build_agent_graph(
    llm: BaseChatModel,
    tools: Sequence[BaseTool] | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    system_prompt: str | None = None,
    context_max_chars: int = 12000,
    summary_after: int = 0,
    history_max_chars: int = 18000,
    model_first_tools: bool = False,
    task_routing: bool | None = None,
    max_tool_calls: int = 3,
    **extra_kwargs: Any,
) -> CompiledStateGraph:
    """Сборка и компиляция StateGraph агента с поддержкой инструментов и сохранением состояния.

    Args:
        llm: Языковая модель (ChatOllama или тестовая модель).
        tools: Инструменты LangChain, доступные агенту.
        checkpointer: Механизм сохранения состояния сессий (например, MemorySaver).
        system_prompt: Системный промпт, задающий поведение и правила агента.
        context_max_chars: Лимит символов автоматически найденного контекста.
        summary_after: Порог (в сообщениях) для сжатия диалога; 0 — выключить.
        history_max_chars: Лимит символов истории, передаваемой модели.
        model_first_tools: Модель сама решает, вызывать ли инструменты (не более MAX_TOOL_CALLS за ход);
            иначе предметные вопросы всегда сначала идут в поиск.
        task_routing: Включить режимы запроса (вычисление / переписывание / общий / предметный).
            None — определить по system_prompt (как раньше).

    Returns:
        Скомпилированный граф агента CompiledStateGraph.
    """
    from assistent.services.agent_runtime import build_runtime

    return build_runtime(llm, tools, checkpointer, system_prompt, context_max_chars,
                         summary_after, history_max_chars, model_first_tools, task_routing, max_tool_calls,
                         **extra_kwargs)

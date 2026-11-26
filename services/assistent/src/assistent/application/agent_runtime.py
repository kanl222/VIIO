"""Ready-made LangChain agent loop; VIIO middleware supplies application policy."""

import hashlib
import re
import time
import uuid

import httpx
from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware, ModelResponse, ModelRetryMiddleware,
    ToolCallLimitMiddleware, ToolRetryMiddleware,
)
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from openai import APIConnectionError, InternalServerError, RateLimitError

from assistent.prompts import SEARCH_CALL_LIMIT, build_system_prompt
from assistent.observability.diagnostics import record
from assistent.application.agent_policy import (
    AgentRuntimeContext, AgentState, AUTO_CALL_PREFIX, CODER_TOOL, DEFAULT_SYSTEM_PROMPT,
    FALLBACK_CODE_PREFIX, GENERAL_SYSTEM_PROMPT, MAX_TOOL_CALLS, REWRITE_SYSTEM_PROMPT,
    SEARCH_TOOL, attach_reference_sources, cited_sources,
    classify, clean_answer, ctx, ensure_system, finalize_response, forced_tool_call,
    history_for_turn, is_coding_request, last_human_index, last_question,
    messages_with_reference, messages_with_retrieved_context, needs_automatic_search,
    turn_provenance, with_user_notes,
)
from assistent.application.dialogue_summary import compact_dialogue
from assistent.api.security import has_role_access

DOCUMENT_SEARCH_LIMIT = SEARCH_CALL_LIMIT
_TOOL_LIMIT_PREFIX = "Tool call limit reached:"

_PRIVILEGED = {'programmer', 'admin'}
_DEFAULT_ROLES: dict[str, set[str]] = {
    'postgresql_readonly': _PRIVILEGED,
    'github_repository': _PRIVILEGED,
}


def _roles_for(name: str, tool_obj: object = None) -> set[str] | list[str] | None:
    roles = (getattr(tool_obj, 'metadata', None) or {}).get('allowed_roles')
    return _DEFAULT_ROLES.get(name) if roles is None else roles


def _safe_ctx(runtime: object) -> dict:
    if runtime is None:
        return {}
    return getattr(runtime, "context", None) or {}


_REPO_LIST = re.compile(
    r'\b(?:список|какие|покажи|доступные)\s+(?:\w+\s+)?(?:репозитор\w*|репоизтор\w*)',
    re.I,
)

_GITHUB_TREE = re.compile(
    r'(?:структур\w*|дерев\w*|файлы|файлов)\s+(?:в\s+|у\s+|для\s+)?репозитор\w*\s+([a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+)',
    re.I,
)

_TABLE_COLS = re.compile(
    r'(?:столбц\w*|колонк\w*|поля|структур\w*)\s+(?:у|в|для)?\s*таблиц\w*\s+([a-zA-Z0-9_]+)',
    re.I,
)


def _content_to_str(content: object) -> str:
    """Приводит произвольное содержимое сообщения к строке (список блоков -> единый текст)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get('type') == 'text' and 'text' in block:
                    parts.append(str(block['text']))
                elif 'content' in block:
                    parts.append(str(block['content']))
            elif hasattr(block, 'text'):
                parts.append(str(getattr(block, 'text')))
        return "\n".join(parts)
    return str(content or "")


def _trim_diagnostic(val: object, max_len: int = 500) -> object:
    """Обрезает строки в диагностических событиях (включая аргументы tool_calls)."""
    if isinstance(val, str):
        if len(val) > max_len:
            return val[:max_len] + "... [truncated]"
        return val
    if isinstance(val, list):
        return [_trim_diagnostic(item, max_len) for item in val]
    if isinstance(val, dict):
        return {k: _trim_diagnostic(v, max_len) for k, v in val.items()}
    return val


def _rule_unused_indexes(question: str, calls: list, history: list) -> dict | None:
    if not calls and re.search(r'неиспользуем\w*', question, re.I) and re.search(r'индекс\w*', question, re.I):
        return {
            'operation': 'query',
            'sql': "SELECT s.schemaname, s.relname, s.indexrelname, s.idx_scan, "
                   "i.indisprimary, i.indisunique, d.stats_reset "
                   "FROM pg_stat_user_indexes s JOIN pg_index i ON i.indexrelid=s.indexrelid "
                   "LEFT JOIN pg_stat_database d ON d.datname=current_database() "
                   "WHERE has_schema_privilege(s.schemaname,'USAGE') "
                   "AND has_table_privilege(s.relid,'SELECT') "
                   "ORDER BY s.idx_scan, s.schemaname, s.relname, s.indexrelname",
        }
    return None


def _rule_list_tables(question: str, calls: list, history: list) -> dict | None:
    if (calls and calls[-1].get('name') == 'postgresql_readonly'
            and calls[-1].get('args', {}).get('operation') == 'schemas'
            and isinstance(history[-1], ToolMessage)
            and re.search(r'\b(?:какие таблицы|список таблиц)\b', question, re.I)):
        return {
            'operation': 'query',
            'sql': "SELECT table_schema, table_name FROM information_schema.tables "
                   "WHERE table_type = 'BASE TABLE' AND table_schema NOT IN "
                   "('pg_catalog', 'information_schema') ORDER BY table_schema, table_name",
        }
    return None


def _rule_table_columns(question: str, calls: list, history: list) -> dict | None:
    if not calls and (match_cols := _TABLE_COLS.search(question)):
        safe_tbl = match_cols.group(1).replace("'", "''")
        return {
            'operation': 'query',
            'sql': "SELECT table_schema, table_name, column_name, data_type "
                   "FROM information_schema.columns "
                   f"WHERE lower(table_name) = lower('{safe_tbl}') "
                   "ORDER BY table_schema, ordinal_position",
        }
    return None


def _rule_github_tree(question: str, calls: list, history: list) -> dict | None:
    if not calls and (match_tree := _GITHUB_TREE.search(question)):
        return {
            'operation': 'structure',
            'repository': match_tree.group(1),
        }
    return None


def _rule_github_repos(question: str, calls: list, history: list) -> dict | None:
    if not calls and _REPO_LIST.search(question):
        return {
            'operation': 'repositories',
        }
    return None


FORCED_TOOL_RULES = [
    ('postgresql_readonly', _rule_unused_indexes, 'database-indexes-'),
    ('postgresql_readonly', _rule_list_tables, 'database-tables-'),
    ('postgresql_readonly', _rule_table_columns, 'database-columns-'),
    ('github_repository', _rule_github_tree, 'github-tree-'),
    ('github_repository', _rule_github_repos, 'github-repos-'),
]


class VIIOPolicy(AgentMiddleware):
    """Keep ACL-filtered context, forced retrieval/delegation and response provenance."""

    def __init__(self, prompt, names, model_first, routing, max_chars, tools=None):
        self.prompt, self.names = prompt, names
        self.model_first, self.routing, self.max_chars = model_first, routing, max_chars
        self.tools_by_name = {getattr(t, 'name', ''): t for t in (tools or [])}

    def _can_use_tool(self, tool_name: str, user_context: dict) -> bool:
        if tool_name not in self.names:
            return False
        tool_obj = self.tools_by_name.get(tool_name)
        roles = _roles_for(tool_name, tool_obj)
        if roles is None:
            return True
        return has_role_access(user_context, roles)

    async def awrap_tool_call(self, request, handler):
        call = request.tool_call
        started = time.monotonic()
        tool_obj = getattr(request, 'tool', None) or self.tools_by_name.get(call['name'])
        roles = _roles_for(call['name'], tool_obj)
        user_context = _safe_ctx(getattr(request, 'runtime', None)).get('user_context', {})
        if roles is not None and not has_role_access(user_context, roles):
            record('tool_end', call_id=call['id'], name=call['name'], status='error', error='forbidden')
            return ToolMessage(content='Недостаточно прав для этого инструмента.',
                               tool_call_id=call['id'], name=call['name'], status='error')

        record('tool_start', call_id=call['id'], name=call['name'], status='running')
        try:
            field = {'list_documents': 'tags', 'search_knowledge_base': 'document_ids'}.get(call['name'])
            value = call['args'].get(field) if field else None
            if isinstance(value, str) and value.strip():
                parts = value.split(',')
                values = [part.strip() for part in parts if part.strip()]
                if values:
                    request = request.override(tool_call={**call, 'args': {**call['args'], field: values}})
                    record('tool_arguments_normalized', call_id=call['id'], name=call['name'], field=field)
            result = await handler(request)
        except BaseException as error:
            record('tool_end', call_id=call['id'], name=call['name'],
                   status='error', error=type(error).__name__,
                   duration_ms=round((time.monotonic() - started) * 1000))
            raise
        if call['name'] == 'postgresql_readonly' and isinstance(result, ToolMessage):
            from assistent.application.database_subgraph import postgres_error
            if postgres_error(str(result.content)):
                result = result.model_copy(update={'status': 'error'})
        record('tool_end', call_id=call['id'], name=call['name'],
               status=getattr(result, 'status', 'success'),
               duration_ms=round((time.monotonic() - started) * 1000))
        return result

    async def awrap_model_call(self, request, handler):
        history = request.messages
        question = last_question(history)
        turn = history[last_human_index(history) + 1:]
        calls = [call for msg in turn if isinstance(msg, AIMessage) for call in msg.tool_calls]
        user_context = _safe_ctx(getattr(request, 'runtime', None)).get('user_context', {})

        if (isinstance(history[-1], ToolMessage)
                and history[-1].tool_call_id.startswith('database-indexes-')
                and str(history[-1].content).strip() == '[]'):
            return ModelResponse(result=[AIMessage(content=(
                'В статистике доступных текущему пользователю индексов нет строк. '
                'Определить неиспользуемые индексы по этому результату невозможно. '
                'Проверка ограничена объектами, доступными текущему пользователю.'
            ))])

        for tool_name, predicate, prefix in FORCED_TOOL_RULES:
            if self._can_use_tool(tool_name, user_context):
                tool_args = predicate(question, calls, history)
                if tool_args is not None:
                    return ModelResponse(result=[forced_tool_call(tool_name, tool_args, prefix)])

        delegated = next((msg for msg in reversed(turn)
                          if isinstance(msg, ToolMessage) and msg.name == CODER_TOOL), None)
        if self.model_first and delegated is not None:
            sources, active = turn_provenance(history)
            return ModelResponse(result=[finalize_response(
                AIMessage(content=str(delegated.content)), sources, active)])
        if (not self.model_first and SEARCH_TOOL in self.names and not calls
                and needs_automatic_search(question)):
            return ModelResponse(result=[forced_tool_call(
                SEARCH_TOOL, {'query': question}, AUTO_CALL_PREFIX)])

        mode = classify(question).mode if self.routing else 'model'
        prompt = (REWRITE_SYSTEM_PROMPT if mode == 'rewrite' else
                  GENERAL_SYSTEM_PROMPT if mode == 'general' else self.prompt)
        reference = _safe_ctx(getattr(request, 'runtime', None)).get('reference_context')
        messages = messages_with_retrieved_context(history, self.max_chars)
        messages = messages_with_reference(messages, reference)
        messages = ensure_system(with_user_notes(messages, request.runtime), prompt)

        tools = [item for item in request.tools
                 if (roles := _roles_for(getattr(item, 'name', ''), item)) is None
                 or has_role_access(user_context, roles)]
        if any(call.get('name') == 'list_documents' for call in calls):
            tools = [item for item in tools if getattr(item, 'name', '') != 'list_documents']
        for name in (SEARCH_TOOL, 'search_document_content', 'search_knowledge_graph'):
            if sum(call.get('name') == name for call in calls) >= DOCUMENT_SEARCH_LIMIT:
                tools = [item for item in tools if getattr(item, 'name', '') != name]
        override = dict(messages=messages[1:], system_message=messages[0], tools=tools)
        if self.routing and mode != 'knowledge':
            override['tools'] = []
            tools = []
        call_id = uuid.uuid4().hex
        started = time.monotonic()
        record('context', call_id=call_id,
               tools=[getattr(t, 'name', str(t)) for t in tools],
               provider=(getattr(request.model, 'metadata', None) or {}).get('viio_provider', 'unknown'),
               model=str(getattr(request.model, 'model_name', None) or
                         getattr(request.model, 'model', None) or 'configured'),
               prompt_sha256=hashlib.sha256(str(messages[0].content).encode()).hexdigest(),
               messages=[dict(role=m.type, content=_trim_diagnostic(m.content, 500),
                              tool_calls=_trim_diagnostic(getattr(m, 'tool_calls', []), 500)) for m in messages])
        try:
            response = await handler(request.override(**override))
        except Exception as error:
            record('generation', call_id=call_id, status='error', error=type(error).__name__,
                   duration_ms=round((time.monotonic() - started) * 1000))
            raise
        answer = response.result[-1]
        raw_content = _content_to_str(getattr(answer, 'content', ''))
        if not isinstance(getattr(answer, 'content', None), str):
            answer = answer.model_copy(update={'content': raw_content})

        usage = getattr(answer, 'usage_metadata', None) or (getattr(answer, 'response_metadata', None) or {}).get('token_usage')
        record('generation', call_id=call_id, status='ok', content=_trim_diagnostic(raw_content, 1000),
               model=answer.response_metadata.get('model_name') or answer.response_metadata.get('model'),
               duration_ms=round((time.monotonic() - started) * 1000),
               tool_calls=_trim_diagnostic(getattr(answer, 'tool_calls', []), 500),
               usage=usage)

        if self.model_first and isinstance(answer, AIMessage) and not answer.tool_calls:
            if self.routing and mode == 'knowledge' and CODER_TOOL in self.names and is_coding_request(question):
                return ModelResponse(result=[forced_tool_call(
                    CODER_TOOL, {'task': question}, FALLBACK_CODE_PREFIX)])
            if (self._can_use_tool('github_repository', user_context)
                    and not any(c.get('name') == 'github_repository' for c in calls)
                    and _REPO_LIST.search(question)):
                return ModelResponse(result=[forced_tool_call(
                    'github_repository', {'operation': 'repositories'}, 'github-repos-')])
            searched = any(call.get('name') in {SEARCH_TOOL, 'search_knowledge_graph', 'search_document_content'} for call in calls)
            content_question = bool(re.search(r'\b(как|почему|зачем|где|что такое|расшифровка|аббревиатур|термин)\b', question, re.I))
            catalog_only = calls and all(call.get('name') == 'list_documents' for call in calls)
            if not searched and (not calls or (catalog_only and content_question)):
                preferred_tool = SEARCH_TOOL if SEARCH_TOOL in self.names else None
                if preferred_tool and needs_automatic_search(question):
                    return ModelResponse(result=[forced_tool_call(
                        preferred_tool, {'query': question}, AUTO_CALL_PREFIX)])

        if isinstance(answer, AIMessage) and not answer.tool_calls:
            sources, active = turn_provenance(history)
            if reference:
                answer = attach_reference_sources(answer, reference)
                sources = answer.response_metadata['sources']
            answer_content = _content_to_str(answer.content)
            source_candidates = sources
            sources = cited_sources(answer_content, sources)
            if not re.search(r"источник\w*|ссылк\w*|документ\w*|регламент\w*|sources?|references?", question, re.I):
                sources = []
                answer_content = re.split(r"(?im)^\s*(?:\*\*)?Источники?\s*[:：]", answer_content, maxsplit=1)[0].rstrip()
            saved = [msg.artifact['saved_document'] for msg in turn
                     if isinstance(msg, ToolMessage) and msg.name == 'save_knowledge'
                     and isinstance(msg.artifact, dict) and 'saved_document' in msg.artifact]
            answer = finalize_response(answer, sources, active,
                                       content=clean_answer(answer_content, source_candidates), saved_documents=saved)
            response = ModelResponse(result=[*response.result[:-1], answer],
                                     structured_response=response.structured_response)
        return response


def build_runtime(llm, tools, checkpointer, system_prompt, context_max_chars,
                  summary_after, history_max_chars, model_first_tools, task_routing, max_tool_calls=MAX_TOOL_CALLS,
                  *, database_diagnostics=False, **extra_kwargs):
    active_tools = [t for t in (tools or []) if t is not None]
    names = {item.name for item in active_tools}
    prompt = system_prompt or build_system_prompt(names)
    routing = (system_prompt is None or prompt.startswith(DEFAULT_SYSTEM_PROMPT)
               if task_routing is None else task_routing)
    database_runtime = None
    if database_diagnostics and 'postgresql_readonly' in names:
        from assistent.application.database_subgraph import build_database_analyst_graph
        database_runtime = build_database_analyst_graph(llm, active_tools, checkpointer=False)
    middleware = [
        VIIOPolicy(prompt, names, model_first_tools, routing, context_max_chars, tools=active_tools),
        ModelRetryMiddleware(max_retries=2, initial_delay=0.2, max_delay=1,
            retry_on=(httpx.TransportError, APIConnectionError, InternalServerError, RateLimitError),
            on_failure='error'),
        ToolRetryMiddleware(max_retries=1, initial_delay=0.2, max_delay=1,
            retry_on=(httpx.TransportError, TimeoutError),
            on_failure=lambda _: 'Инструмент временно недоступен. Повторите запрос позже.'),
        ToolCallLimitMiddleware(run_limit=max_tool_calls, exit_behavior='end'),
    ]
    if 'list_documents' in names:
        middleware.insert(1, ToolCallLimitMiddleware(
            tool_name='list_documents', run_limit=1, exit_behavior='continue'))
    search_limit_tools = sorted(names & {SEARCH_TOOL, 'search_document_content', 'search_knowledge_graph'})
    for name in search_limit_tools:
        middleware.insert(1, ToolCallLimitMiddleware(
            tool_name=name, run_limit=DOCUMENT_SEARCH_LIMIT, exit_behavior='continue'))
    # The outer checkpoint retains the full audit history; the ready-made loop is ephemeral.
    agent = create_agent(llm, active_tools, middleware=middleware,
                         context_schema=AgentRuntimeContext, checkpointer=False)

    async def run(state, runtime):
        history, boundary = history_for_turn(state, runtime)
        question = last_question(history)
        if database_runtime is not None:
            from assistent.application.database_subgraph import is_database_diagnostic
            from assistent.infrastructure.tools.postgres_tool import can_access_database

            if is_database_diagnostic(question) and can_access_database(_safe_ctx(runtime).get('user_context')):
                record('route', mode='database', tools=['postgresql_readonly'])
                result = await database_runtime.ainvoke(
                    {'messages': history[-1:]}, context=ctx(runtime))
                return {**boundary, 'messages': result['messages'][-1:]}
        info = classify(question)
        record('route', mode=info.mode if routing else 'model',
               model_first_tools=model_first_tools, tools=sorted(names))
        if routing and info.calc is not None:
            return {**boundary, 'messages': [AIMessage(content=info.calc)]}
        bounded, summary = await compact_dialogue(
            history, state, boundary, llm, summary_after, history_max_chars)
        result = await agent.ainvoke({'messages': bounded}, context=ctx(runtime),
                                    config={'recursion_limit': max(25, max_tool_calls * 3 + 10)})
        added = result['messages'][len(bounded):]
        if added and isinstance(added[-1], AIMessage):
            last = added[-1]
            if isinstance(last.content, str) and last.content.startswith(_TOOL_LIMIT_PREFIX):
                limit_message = (
                    'Исчерпан лимит на три обращения к инструментам за один ход диалога. Уточните оставшуюся часть задачи.'
                    if max_tool_calls == 3
                    else f'Достигнут лимит {max_tool_calls} обращений к инструментам. Уточните оставшуюся часть задачи.'
                )
                added[-1] = last.model_copy(update={'content': limit_message})
        return {**boundary, **summary, 'messages': added}

    builder = StateGraph(AgentState, context_schema=AgentRuntimeContext)
    builder.add_node('agent', run)
    builder.add_edge(START, 'agent')
    builder.add_edge('agent', END)
    return builder.compile(checkpointer=checkpointer)

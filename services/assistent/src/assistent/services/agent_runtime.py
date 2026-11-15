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
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from openai import APIConnectionError, InternalServerError, RateLimitError

from assistent.services.diagnostics import record
from assistent.services.agent_policy import (
    AgentRuntimeContext, AgentState, AUTO_CALL_PREFIX, CODER_TOOL, DEFAULT_SYSTEM_PROMPT,
    FALLBACK_CODE_PREFIX, GENERAL_SYSTEM_PROMPT, MAX_TOOL_CALLS, REWRITE_SYSTEM_PROMPT,
    SEARCH_TOOL, TOOL_LIMIT_MESSAGE, attach_reference_sources, cited_sources,
    classify, clean_answer, ctx, ensure_system, finalize_response, forced_tool_call,
    history_for_turn, is_coding_request, last_human_index, last_question,
    messages_with_reference, messages_with_retrieved_context, needs_automatic_search,
    turn_provenance, with_user_notes,
)
from assistent.services.dialogue_summary import compact_dialogue
from assistent.security import has_role_access

DOCUMENT_SEARCH_LIMIT = 3


class VIIOPolicy(AgentMiddleware):
    """Keep ACL-filtered context, forced retrieval/delegation and response provenance."""

    def __init__(self, prompt, names, model_first, routing, max_chars):
        self.prompt, self.names = prompt, names
        self.model_first, self.routing, self.max_chars = model_first, routing, max_chars

    async def awrap_tool_call(self, request, handler):
        call = request.tool_call
        started = time.monotonic()
        record('tool_start', call_id=call['id'], name=call['name'], status='running')
        try:
            field = {'list_documents': 'tags', 'search_knowledge_base': 'document_ids'}.get(call['name'])
            value = call['args'].get(field) if field else None
            if isinstance(value, str) and value.strip():
                parts = value.split(',') if field == 'tags' else [value]
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
            from assistent.services.database_subgraph import postgres_error
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
        if (isinstance(history[-1], ToolMessage)
                and history[-1].tool_call_id.startswith('database-indexes-')
                and str(history[-1].content).strip() == '[]'):
            return ModelResponse(result=[AIMessage(content=(
                'В статистике доступных текущему пользователю индексов нет строк. '
                'Определить неиспользуемые индексы по этому результату невозможно. '
                'Проверка ограничена объектами, доступными текущему пользователю.'
            ))])
        if (not calls and 'postgresql_readonly' in self.names
                and re.search(r'неиспользуем\w*', question, re.I)
                and re.search(r'индекс\w*', question, re.I)
                and has_role_access(ctx(request.runtime).get('user_context', {}), {'programmer', 'admin'})):
            return ModelResponse(result=[forced_tool_call('postgresql_readonly', {
                'operation': 'query',
                'sql': "SELECT s.schemaname, s.relname, s.indexrelname, s.idx_scan, "
                       "i.indisprimary, i.indisunique, d.stats_reset "
                       "FROM pg_stat_user_indexes s JOIN pg_index i ON i.indexrelid=s.indexrelid "
                       "LEFT JOIN pg_stat_database d ON d.datname=current_database() "
                       "WHERE has_schema_privilege(s.schemaname,'USAGE') "
                       "AND has_table_privilege(s.relid,'SELECT') "
                       "ORDER BY s.idx_scan, s.schemaname, s.relname, s.indexrelname",
            }, 'database-indexes-')])
        if ('postgresql_readonly' in self.names
                and re.search(r'\b(?:какие таблицы|список таблиц)\b', question, re.I)
                and calls and calls[-1].get('name') == 'postgresql_readonly'
                and calls[-1].get('args', {}).get('operation') == 'schemas'
                and isinstance(history[-1], ToolMessage)
                and has_role_access(ctx(request.runtime).get('user_context', {}), {'programmer', 'admin'})):
            return ModelResponse(result=[forced_tool_call('postgresql_readonly', {
                'operation': 'query',
                'sql': "SELECT table_schema, table_name FROM information_schema.tables "
                       "WHERE table_type = 'BASE TABLE' AND table_schema NOT IN "
                       "('pg_catalog', 'information_schema') ORDER BY table_schema, table_name",
            }, 'database-tables-')])
        if (not calls and 'postgresql_readonly' in self.names
                and (match_cols := re.search(r'(?:столбц\w*|колонк\w*|поля|структур\w*)\s+(?:у|в|для)?\s*таблиц\w*\s+([a-zA-Z0-9_]+)', question, re.I))
                and has_role_access(ctx(request.runtime).get('user_context', {}), {'programmer', 'admin'})):
            tbl_target = match_cols.group(1).lower()
            return ModelResponse(result=[forced_tool_call('postgresql_readonly', {
                'operation': 'query',
                'sql': f"SELECT table_schema, table_name, column_name, data_type "
                       f"FROM information_schema.columns WHERE table_name = '{tbl_target}' "
                       f"ORDER BY table_schema, ordinal_position",
            }, 'database-columns-')])
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
        reference = ctx(request.runtime).get('reference_context')
        messages = messages_with_retrieved_context(history, self.max_chars)
        messages = messages_with_reference(messages, reference)
        messages = ensure_system(with_user_notes(messages, request.runtime), prompt)
        user_context = ctx(request.runtime).get('user_context', {})
        tools = [item for item in request.tools
                 if (roles := (getattr(item, 'metadata', None) or {}).get('allowed_roles')) is None
                 or has_role_access(user_context, roles)]
        if any(call.get('name') == 'list_documents' for call in calls):
            tools = [item for item in tools if getattr(item, 'name', '') != 'list_documents']
        for name in (SEARCH_TOOL, 'search_document_content'):
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
               messages=[dict(role=m.type, content=m.content,
                                       tool_calls=getattr(m, 'tool_calls', [])) for m in messages])
        try:
            response = await handler(request.override(**override))
        except Exception as error:
            record('generation', call_id=call_id, status='error', error=type(error).__name__,
                   duration_ms=round((time.monotonic() - started) * 1000))
            raise
        answer = response.result[-1]
        usage = getattr(answer, 'usage_metadata', None) or (getattr(answer, 'response_metadata', None) or {}).get('token_usage')
        record('generation', call_id=call_id, status='ok', content=answer.content,
               model=answer.response_metadata.get('model_name') or answer.response_metadata.get('model'),
               duration_ms=round((time.monotonic() - started) * 1000),
               tool_calls=getattr(answer, 'tool_calls', []),
               usage=usage)

        if self.model_first and isinstance(answer, AIMessage) and not answer.tool_calls:
            if self.routing and mode == 'knowledge' and CODER_TOOL in self.names and is_coding_request(question):
                return ModelResponse(result=[forced_tool_call(
                    CODER_TOOL, {'task': question}, FALLBACK_CODE_PREFIX)])
            searched = any(call.get('name') in {SEARCH_TOOL, 'search_knowledge_graph', 'search_document_content'} for call in calls)
            content_question = bool(re.search(r'\b(как|почему|зачем|где|что такое|расшифровка|аббревиатур|термин)\b', question, re.I))
            catalog_only = calls and all(call.get('name') == 'list_documents' for call in calls)
            if not searched and (not calls or catalog_only and content_question):
                preferred_tool = None
                if "search_knowledge_graph" in self.names and re.search(r'\b(что такое|расшифровка|аббревиатур|термин|кто такой|где находится|в какой системе)\b', question, re.I):
                    preferred_tool = "search_knowledge_graph"
                elif SEARCH_TOOL in self.names:
                    preferred_tool = SEARCH_TOOL
                if preferred_tool and needs_automatic_search(question):
                    return ModelResponse(result=[forced_tool_call(
                        preferred_tool, {'query': question}, AUTO_CALL_PREFIX)])

        if isinstance(answer, AIMessage) and not answer.tool_calls:
            sources, active = turn_provenance(history)
            if reference:
                answer = attach_reference_sources(answer, reference)
                sources = answer.response_metadata['sources']
            source_candidates = sources
            if self.model_first:
                sources = cited_sources(str(answer.content), sources)
            saved = [msg.artifact['saved_document'] for msg in turn
                     if isinstance(msg, ToolMessage) and msg.name == 'save_knowledge'
                     and isinstance(msg.artifact, dict) and 'saved_document' in msg.artifact]
            answer = finalize_response(answer, sources, active,
                                       content=clean_answer(answer.content, source_candidates), saved_documents=saved)
            response = ModelResponse(result=[*response.result[:-1], answer],
                                     structured_response=response.structured_response)
        return response


def build_runtime(llm, tools, checkpointer, system_prompt, context_max_chars,
                  summary_after, history_max_chars, model_first_tools, task_routing, max_tool_calls=MAX_TOOL_CALLS,
                  *, database_diagnostics=False, **extra_kwargs):
    prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
    routing = (system_prompt is None or system_prompt.startswith(DEFAULT_SYSTEM_PROMPT)
               if task_routing is None else task_routing)
    active_tools = list(tools or [])
    names = {item.name for item in active_tools}
    database_runtime = None
    if database_diagnostics and 'postgresql_readonly' in names:
        from assistent.services.database_subgraph import build_database_analyst_graph
        database_runtime = build_database_analyst_graph(llm, active_tools, checkpointer=False)
    middleware = [
        VIIOPolicy(prompt, names, model_first_tools, routing, context_max_chars),
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
    for name in names & {SEARCH_TOOL, 'search_document_content'}:
        middleware.insert(1, ToolCallLimitMiddleware(
            tool_name=name, run_limit=DOCUMENT_SEARCH_LIMIT, exit_behavior='continue'))
    # The outer checkpoint retains the full audit history; the ready-made loop is ephemeral.
    agent = create_agent(llm, active_tools, middleware=middleware,
                         context_schema=AgentRuntimeContext, checkpointer=False)

    async def run(state, runtime):
        history, boundary = history_for_turn(state, runtime)
        question = last_question(history)
        if database_runtime is not None:
            from assistent.services.database_subgraph import is_database_diagnostic
            from assistent.tools.postgres_tool import can_access_database

            if is_database_diagnostic(question) and can_access_database(ctx(runtime).get('user_context')):
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
            if isinstance(last.content, str) and last.content.startswith('Tool call limit reached:'):
                limit_message = TOOL_LIMIT_MESSAGE if max_tool_calls == 3 else f'Достигнут лимит {max_tool_calls} обращений к инструментам. Уточните оставшуюся часть задачи.'
                added[-1] = last.model_copy(update={'content': limit_message})
        return {**boundary, **summary, 'messages': added}

    builder = StateGraph(AgentState, context_schema=AgentRuntimeContext)
    builder.add_node('agent', run)
    builder.add_edge(START, 'agent')
    builder.add_edge('agent', END)
    return builder.compile(checkpointer=checkpointer)

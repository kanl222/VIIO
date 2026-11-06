"""Ready-made LangChain agent loop; VIIO middleware supplies application policy."""

import hashlib
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


class VIIOPolicy(AgentMiddleware):
    """Keep ACL-filtered context, forced retrieval/delegation and response provenance."""

    def __init__(self, prompt, names, model_first, routing, max_chars):
        self.prompt, self.names = prompt, names
        self.model_first, self.routing, self.max_chars = model_first, routing, max_chars

    async def awrap_model_call(self, request, handler):
        history = request.messages
        question = last_question(history)
        turn = history[last_human_index(history) + 1:]
        calls = [call for msg in turn if isinstance(msg, AIMessage) for call in msg.tool_calls]
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
        override = dict(messages=messages[1:], system_message=messages[0], tools=tools)
        if self.routing and mode != 'knowledge':
            override['tools'] = []
        call_id = uuid.uuid4().hex
        started = time.monotonic()
        record('context', call_id=call_id,
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
        record('generation', call_id=call_id, status='ok', content=answer.content,
               duration_ms=round((time.monotonic() - started) * 1000),
               tool_calls=getattr(answer, 'tool_calls', []))

        if (self.model_first and self.routing and isinstance(answer, AIMessage)
                and not answer.tool_calls):
            if mode == 'knowledge' and CODER_TOOL in self.names and is_coding_request(question):
                return ModelResponse(result=[forced_tool_call(
                    CODER_TOOL, {'task': question}, FALLBACK_CODE_PREFIX)])
            if not calls and SEARCH_TOOL in self.names and needs_automatic_search(question):
                return ModelResponse(result=[forced_tool_call(
                    SEARCH_TOOL, {'query': question}, AUTO_CALL_PREFIX)])

        if isinstance(answer, AIMessage) and not answer.tool_calls:
            sources, active = turn_provenance(history)
            if reference:
                answer = attach_reference_sources(answer, reference)
                sources = answer.response_metadata['sources']
            if self.model_first:
                sources = cited_sources(str(answer.content), sources)
            saved = [msg.artifact['saved_document'] for msg in turn
                     if isinstance(msg, ToolMessage) and msg.name == 'save_knowledge'
                     and isinstance(msg.artifact, dict) and 'saved_document' in msg.artifact]
            answer = finalize_response(answer, sources, active,
                                       content=clean_answer(answer.content, sources), saved_documents=saved)
            response = ModelResponse(result=[*response.result[:-1], answer],
                                     structured_response=response.structured_response)
        return response


def build_runtime(llm, tools, checkpointer, system_prompt, context_max_chars,
                  summary_after, history_max_chars, model_first_tools, task_routing, max_tool_calls=MAX_TOOL_CALLS):
    prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
    routing = (system_prompt is None or system_prompt.startswith(DEFAULT_SYSTEM_PROMPT)
               if task_routing is None else task_routing)
    active_tools = list(tools or [])
    names = {item.name for item in active_tools}
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
    # The outer checkpoint retains the full audit history; the ready-made loop is ephemeral.
    agent = create_agent(llm, active_tools, middleware=middleware,
                         context_schema=AgentRuntimeContext, checkpointer=False)

    async def run(state, runtime):
        history, boundary = history_for_turn(state, runtime)
        question = last_question(history)
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

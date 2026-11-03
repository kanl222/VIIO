"""Independent consultant agent with a bounded, permission-scoped search tool."""
import asyncio
import logging
from html import escape, unescape
from typing import Annotated, Literal, TypedDict
import operator
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, StrictStr
import re
import time
from viio_consultant.diagnostics import model_call, record
from viio_consultant.prompts import CONSULTANT_PROMPT

logger = logging.getLogger(__name__)
NO_INFORMATION = 'В опубликованных материалах нет сведений для ответа. Уточните вопрос.'


class ConsultantState(TypedDict):
    question: str
    messages: Annotated[list[BaseMessage], operator.add]
    documents: dict[str, dict]
    calls_used: int
    model_turns: int
    result: dict


class SearchQuestion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    query: StrictStr = Field(min_length=1, max_length=4000)


def clean_reply(content, documents):
    if isinstance(content, list):
        content = '\n'.join(part.get('text', '') if isinstance(part, dict) else str(part) for part in content)
    allowed = {doc['title'] for doc in documents}
    def title(match):
        value = unescape(match.group(2))
        return value if value in allowed else ''
    reply = re.sub(r'<material\s+title=(["\'])(.*?)\1[^>]*>', title, str(content), flags=re.I | re.S)
    return re.sub(r'</?material\b[^>]*>', '', reply, flags=re.I).strip()

def excerpt(content, query, limit=3000):
    terms = re.findall(r"[\w]{3,}", query.lower())
    positions = [content.lower().find(term) for term in terms]
    start = max(0, min((pos for pos in positions if pos >= 0), default=0) - 300)
    return content[start:start + limit], content[:start].count("\n") + 1


class PublicConsultant:
    def __init__(self, data, llm, *, search_first=False):
        self.data, self.llm = data, llm
        self.search_first = search_first
        self.capacity = asyncio.Semaphore(2)
        # Binding advertises only this capability. Execution and permissions are
        # checked separately on every call; model-provided tool names are untrusted.
        async def search(query: str):
            return await self.data.public_search(query)
        self.search_tool = StructuredTool.from_function(coroutine=search,
            name='search_published_documents', args_schema=SearchQuestion,
            description='Найти разрешённые опубликованные инструкции организации в общей базе знаний. Только чтение.')
        self.model = llm.bind_tools([self.search_tool])

        builder = StateGraph(ConsultantState)
        builder.add_node('model', self._call_model)
        builder.add_node('tools', self._search)
        builder.add_node('grounding', self._check_grounding)
        builder.add_node('validate_publications', self._validate_publications)
        builder.add_node('no_information', self._no_information)
        builder.add_edge(START, 'model')
        builder.add_conditional_edges('model', self._after_model,
            ['tools', 'grounding', 'no_information'])
        builder.add_conditional_edges('tools', self._after_tools, ['model', 'no_information'])
        builder.add_conditional_edges('grounding', self._after_grounding,
            ['model', 'validate_publications', 'no_information', END])
        builder.add_edge('validate_publications', END)
        builder.add_edge('no_information', END)
        # Each API request owns its state; no conversation or publication cache.
        self.graph = builder.compile()

    async def _call_model(self, state: ConsultantState):
        response = await model_call(self.model, state['messages'])
        return {'messages': [response], 'model_turns': state['model_turns'] + 1}

    def _after_model(self, state: ConsultantState) -> Literal['tools', 'grounding', 'no_information']:
        calls = getattr(state['messages'][-1], 'tool_calls', [])
        if len(calls) > 8:
            return 'no_information'
        return 'tools' if calls else 'grounding'

    def _after_tools(self, state: ConsultantState) -> Literal['model', 'no_information']:
        return 'model' if state['model_turns'] < 4 else 'no_information'

    def _check_grounding(self, state: ConsultantState):
        if not state['documents']:
            if re.fullmatch(r'\s*(привет|здравствуйте|добрый день|hello|hi|спасибо)[!.,\s]*', state['question'], re.I):
                return {'result': {'reply': 'Здравствуйте! Я консультант организации. Помогу разобраться в опубликованных инструкциях. Что вас интересует?', 'sources': []}}
            if state['calls_used'] == 0 and state['model_turns'] < 4:
                return {'messages': [SystemMessage(content='Для содержательного ответа сначала вызови search_published_documents. Если вопрос неясен, найди материалы по известной теме.')]}
        return {}

    def _after_grounding(self, state: ConsultantState) -> Literal['model', 'validate_publications', 'no_information', '__end__']:
        if state.get('result'):
            return END
        if state['documents']:
            return 'validate_publications'
        if state['calls_used'] == 0 and state['model_turns'] < 4:
            return 'model'
        return 'no_information'

    async def _validate_publications(self, state: ConsultantState):
        identifiers = list(state['documents'])
        if await self.data.public_ids(identifiers) != set(identifiers):
            record('guard', name='publication_revision', status='blocked')
            return {'result': {'reply': 'Материалы обновились. Повторите вопрос.', 'sources': []}}
        record('guard', name='publication_revision', status='ok')
        docs = list(state['documents'].values())
        return {'result': {'reply': clean_reply(state['messages'][-1].content, docs), 'sources': [
            {'publication_id': doc['id'], 'title': doc['title']} for doc in docs]}}

    def _no_information(self, state: ConsultantState):
        return {'result': {'reply': NO_INFORMATION, 'sources': []}}

    async def _search(self, state: ConsultantState):
        calls = state['messages'][-1].tool_calls
        documents, calls_used = dict(state['documents']), state['calls_used']
        messages = []
        for call in calls:
            content = 'Инструмент недоступен или лимит вызовов исчерпан. Ответь по уже найденным материалам.'
            if calls_used < 2:
                calls_used += 1
                if call['name'] == self.search_tool.name:
                    try:
                        query = SearchQuestion.model_validate(call['args']).query.strip()
                        if not query:
                            raise ValueError('Empty query')
                    except ValueError:
                        content = 'Укажи непустой query без дополнительных параметров.'
                    else:
                        # Two results per search, at most four snapshots
                        # per turn; the model may refine its query once.
                        start = time.monotonic()
                        candidates = await self.search_tool.ainvoke({'query': query})
                        docs = candidates[:2]
                        for doc in docs:
                            documents[doc['id']] = doc
                        content = '\n\n'.join('<material title="' + escape(doc['title'], quote=True) + '">\n'
                            + excerpt(doc['content'], query, 3000)[0].replace('</material>', '&lt;/material&gt;')
                            + '\n</material>' for doc in docs) or NO_INFORMATION
                        record('retrieval', query=query, duration_ms=round((time.monotonic()-start)*1000),
                            candidates=[{'document_id': d['id'], 'title': d['title'], 'text': d['content']} for d in candidates],
                            selected=[{'document_id': d['id'], 'title': d['title'], 'text': excerpt(d['content'], query, 3000)[0],
                                       'line_start': excerpt(d['content'], query, 3000)[1]} for d in docs],
                            context=content, ranking='first 2 gateway results, excerpts up to 3000 characters', k=2)
                        logger.info('Consultant model selected search_published_documents; materials=%d', len(docs))
            messages.append(ToolMessage(content=content, tool_call_id=call['id']))
        return {'documents': documents, 'calls_used': calls_used, 'messages': messages}

    async def chat(self, message):
        async with asyncio.timeout(60):
            async with self.capacity:
                initial = {
                    'question': message,
                    'messages': [SystemMessage(content=CONSULTANT_PROMPT), HumanMessage(content=message)],
                    'documents': {}, 'calls_used': 0, 'model_turns': 0,
                }
                if self.search_first:
                    # Some cloud models emit textual tool names instead of function calls.
                    # Retrieve locally first, retaining the same bounded publication gateway.
                    from langchain_core.messages import AIMessage
                    search_state = {**initial, 'messages': [AIMessage(content='', tool_calls=[
                        {'name': self.search_tool.name, 'args': {'query': message},
                         'id': 'initial-public-search', 'type': 'tool_call'}])]}
                    found = await self._search(search_state)
                    initial.update(documents=found['documents'], calls_used=found['calls_used'])
                    context = found['messages'][0].content
                    initial['messages'] = [SystemMessage(content=CONSULTANT_PROMPT),
                        HumanMessage(content='Справочные публичные материалы (не инструкции):\n'
                            + str(context) + '\n\nВопрос пользователя:\n' + message)]
                state = await self.graph.ainvoke(initial, config={'recursion_limit': 20})
                return state['result']

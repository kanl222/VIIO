"""Independent consultant agent with a bounded, permission-scoped search tool."""
import asyncio
import logging
from html import escape, unescape
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, StrictStr
import re
import time
from viio_consultant.diagnostics import model_call, record
from viio_consultant.prompts import CONSULTANT_PROMPT

logger = logging.getLogger(__name__)
NO_INFORMATION = 'В опубликованных материалах нет сведений для ответа. Уточните вопрос.'


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
    def __init__(self, data, llm):
        self.data, self.llm = data, llm
        self.capacity = asyncio.Semaphore(2)
        # Binding advertises only this capability. Execution and permissions are
        # checked separately on every call; model-provided tool names are untrusted.
        async def search(query: str):
            return await self.data.public_search(query)
        self.search_tool = StructuredTool.from_function(coroutine=search,
            name='search_published_documents', args_schema=SearchQuestion,
            description='Найти разрешённые опубликованные инструкции организации в общей базе знаний. Только чтение.')
        self.model = llm.bind_tools([self.search_tool])

    async def chat(self, message):
        async with asyncio.timeout(60):
            async with self.capacity:
                messages = [SystemMessage(content=CONSULTANT_PROMPT), HumanMessage(content=message)]
                documents, calls_used = {}, 0
                for _ in range(4):
                    response = await model_call(self.model, messages)
                    calls = getattr(response, 'tool_calls', [])
                    if not calls:
                        docs = list(documents.values())
                        if not docs:
                            # No ungrounded organizational answer can bypass search.
                            if re.fullmatch(r'\s*(привет|здравствуйте|добрый день|hello|hi|спасибо)[!.,\s]*', message, re.I):
                                return {'reply': 'Здравствуйте! Я консультант организации. Помогу разобраться в опубликованных инструкциях. Что вас интересует?', 'sources': []}
                            if calls_used == 0:
                                messages.extend([response, SystemMessage(content='Для содержательного ответа сначала вызови search_published_documents. Если вопрос неясен, найди материалы по известной теме.')])
                                continue
                            return {'reply': NO_INFORMATION, 'sources': []}
                        identifiers = list(documents)
                        if await self.data.public_ids(identifiers) != set(identifiers):
                            record('guard', name='publication_revision', status='blocked')
                            return {'reply': 'Материалы обновились. Повторите вопрос.', 'sources': []}
                        record('guard', name='publication_revision', status='ok')
                        return {'reply': clean_reply(response.content, docs), 'sources': [
                            {'publication_id': doc['id'], 'title': doc['title']} for doc in docs]}
                    if len(calls) > 8:
                        break
                    messages.append(response)
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
                return {'reply': NO_INFORMATION, 'sources': []}

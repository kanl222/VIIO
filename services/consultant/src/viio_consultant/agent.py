"""Independent consultant agent with a bounded, permission-scoped search tool."""
import asyncio
from bisect import bisect_left
import logging
from math import sqrt
from html import escape, unescape
from typing import Annotated, Any, Literal, TypedDict
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


from viio_core.text.strip_thinking import strip_thinking


def clean_reply(content: Any, documents: list[dict[str, Any]]) -> str:
    if isinstance(content, list):
        content = '\n'.join(part.get('text', '') if isinstance(part, dict) else str(part) for part in content)
    content = strip_thinking(str(content))
    allowed = {doc['title'] for doc in documents}
    def title(match: re.Match[str]) -> str:
        value = unescape(match.group(2))
        return value if value in allowed else ''
    reply = re.sub(r'<material\s+title=(["\'])(.*?)\1[^>]*>', title, content, flags=re.I | re.S)
    return re.sub(r'</?material\b[^>]*>', '', reply, flags=re.I).strip()

def excerpt(content: str, query: str, limit: int = 3000) -> tuple[str, int]:
    if len(content) <= limit:
        return content, 1
    stop = {'что', 'как', 'это', 'для', 'или', 'где', 'какой', 'означает', 'такое', 'the', 'and', 'what'}
    terms = list(dict.fromkeys(term for term in re.findall(r"\w{3,}", query.lower()) if term not in stop))[:20]
    if not terms:
        return content[:limit], 1
    pattern = r"\b(?:" + "|".join(map(re.escape, terms)) + r")\b"
    positions = {term: [] for term in terms}
    for match in re.finditer(pattern, content, re.IGNORECASE):
        positions.get(match.group().lower(), []).append(match.start())
    matches = [(found, 1 / sqrt(len(found))) for found in positions.values() if found]
    starts = {0}
    for found, _ in matches:
        stride = max(1, len(found) // 100)
        starts.update(min(len(content) - limit, max(0, pos - min(300, limit // 4)))
                      for pos in found[::stride][:100])

    def score(start: int) -> float:
        end = start + limit
        padding = min(100, limit // 8)
        # Сохраняем контекст с обеих сторон совпадения, если документ продолжается.
        left = start + padding if start else start
        right = end - padding if end < len(content) else end
        return sum(weight for found, weight in matches
                   if bisect_left(found, left) < bisect_left(found, right))

    start = max(sorted(starts), key=score)
    return content[start:start + limit], content[:start].count("\n") + 1


class PublicConsultant:
    def __init__(self, data: Any, llm: Any, *, search_first: bool = False) -> None:
        self.data, self.llm = data, llm
        self.search_first = search_first
        self.capacity = asyncio.Semaphore(2)
        # Привязка объявляет только эту возможность. Выполнение и права
        # проверяются отдельно при каждом вызове; имена инструментов от модели считаются недоверенными.
        async def search(query: str) -> list[dict[str, Any]]:
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
        # Каждый запрос к API владеет своим состоянием; кэш диалога или публикаций отсутствует.
        self.graph = builder.compile()

    async def _call_model(self, state: ConsultantState) -> dict[str, Any]:
        model = self.llm if self.search_first and state['documents'] and state['model_turns'] == 0 else self.model
        response = await model_call(model, state['messages'])
        return {'messages': [response], 'model_turns': state['model_turns'] + 1}

    def _after_model(self, state: ConsultantState) -> Literal['tools', 'grounding', 'no_information']:
        calls = getattr(state['messages'][-1], 'tool_calls', [])
        if len(calls) > 8:
            return 'no_information'
        return 'tools' if calls else 'grounding'

    def _after_tools(self, state: ConsultantState) -> Literal['model', 'no_information']:
        return 'model' if state['model_turns'] < 4 else 'no_information'

    def _check_grounding(self, state: ConsultantState) -> dict[str, Any]:
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

    async def _validate_publications(self, state: ConsultantState) -> dict[str, Any]:
        identifiers = list(state['documents'])
        if await self.data.public_ids(identifiers) != set(identifiers):
            record('guard', name='publication_revision', status='blocked')
            return {'result': {'reply': 'Материалы обновились. Повторите вопрос.', 'sources': []}}
        record('guard', name='publication_revision', status='ok')
        docs = list(state['documents'].values())
        reply = clean_reply(state['messages'][-1].content, docs)

        footer_pattern = r'(?im)^\s*(?:источник[и]?|документ[ы]?|использованн(?:ые|ая)\s+материал[ы]?)\s*[:—-]*(.+)'
        footer = re.search(footer_pattern, reply, re.S)
        if footer:
            answer = reply[:footer.start()].strip()
            footer_text = footer.group(1).strip()
        else:
            answer = reply.strip()
            footer_text = reply

        if not answer or re.search(r'\b(?:не наш[её]л|не удалось найти|недостаточно (?:данных|сведений)|'
                                  r'нет (?:сведений|информации))\b', answer, re.I):
            return self._no_information(state)

        def norm(s: str) -> str:
            return re.sub(r'^\s*(?:\d+[.)]|\*|-|•)\s*', '', s.strip(' \t.•*-«»"\'`')).casefold().replace('ё', 'е')

        raw_parts = re.split(r'[;\n]', footer_text)
        candidate_titles = {norm(p) for p in raw_parts if norm(p)}
        norm_footer = footer_text.casefold().replace('ё', 'е')
        norm_answer = answer.casefold().replace('ё', 'е')

        sources = []
        for doc in docs:
            title = doc['title']
            doc_id = str(doc.get('id', ''))
            norm_doc_title = norm(title)

            # 1. Точное совпадение очищенной строки из блока источников
            if norm_doc_title in candidate_titles:
                sources.append({'publication_id': doc['id'], 'title': title})
                continue

            # 2. Совпадение ID документа в блоке источников
            if doc_id and doc_id in footer_text:
                sources.append({'publication_id': doc['id'], 'title': title})
                continue

            # 3. Полное вхождение названия в блок источников или ответ (для названий длиннее 1 слова)
            if len(norm_doc_title.split()) >= 2 and (norm_doc_title in norm_footer or norm_doc_title in norm_answer):
                sources.append({'publication_id': doc['id'], 'title': title})
                continue

            # 4. Высокая доля совпадения значимых слов (>=70% для названий >=3 слов) в блоке источников
            content_words = [w for w in re.findall(r'\w{4,}', norm_doc_title)
                             if w not in {'инструкция', 'руководство', 'программа', 'паспорт'}]
            if len(content_words) >= 3:
                matched = sum(1 for w in content_words if w in norm_footer)
                if matched / len(content_words) >= 0.7:
                    sources.append({'publication_id': doc['id'], 'title': title})

        if not sources:
            return self._no_information(state)
        return {'result': {'reply': answer, 'sources': sources}}

    def _no_information(self, state: ConsultantState) -> dict[str, Any]:
        return {'result': {'reply': NO_INFORMATION, 'sources': []}}

    async def _search(self, state: ConsultantState) -> dict[str, Any]:
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
                        # Два результата на поиск, не более четырех снапшотов
                        # на ход; модель может уточнить запрос один раз.
                        start = time.monotonic()
                        candidates = await self.search_tool.ainvoke({'query': query})
                        docs = candidates[:2]
                        for doc in docs:
                            documents[doc['id']] = doc
                        selected = []
                        for doc in docs:
                            text, line_start = excerpt(doc['content'], query)
                            selected.append({'document_id': doc['id'], 'title': doc['title'],
                                             'text': text, 'line_start': line_start})
                        content = '\n\n'.join('<material title="' + escape(doc['title'], quote=True) + '">\n'
                            + doc['text'].replace('</material>', '&lt;/material&gt;')
                            + '\n</material>' for doc in selected) or NO_INFORMATION
                        record('retrieval', query=query, duration_ms=round((time.monotonic()-start)*1000),
                            candidates=[{'document_id': d['id'], 'title': d['title'], 'text': d['content']} for d in candidates],
                            selected=selected,
                            context=content, ranking='first 2 gateway results, excerpts up to 3000 characters', k=2)
                        logger.info('Consultant model selected search_published_documents; materials=%d', len(docs))
            messages.append(ToolMessage(content=content, tool_call_id=call['id']))
        return {'documents': documents, 'calls_used': calls_used, 'messages': messages}

    async def chat(self, message: str) -> dict[str, Any]:
        async with asyncio.timeout(60):
            async with self.capacity:
                initial = {
                    'question': message,
                    'messages': [SystemMessage(content=CONSULTANT_PROMPT), HumanMessage(content=message)],
                    'documents': {}, 'calls_used': 0, 'model_turns': 0,
                }
                if self.search_first:
                    # Некоторые облачные модели возвращают текстовые имена инструментов вместо вызовов функций.
                    # Сначала извлекаем локально, сохраняя тот же ограниченный шлюз публикаций.
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

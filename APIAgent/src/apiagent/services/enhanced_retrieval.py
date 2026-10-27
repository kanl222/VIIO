"""Merge bounded lexical and semantic candidates without another model call."""
import asyncio
import logging
from functools import partial
import anyio.to_thread
from langchain_core.runnables import RunnableConfig
from apiagent.services.knowledge_data import query_terms
from apiagent.tools.search_tool import build_access_filter, format_documents

logger = logging.getLogger(__name__)


def rank_documents(query, documents, k):
    terms = query_terms(query)
    def score(doc):
        text = doc.page_content.lower()
        title = str(doc.metadata.get('title', '')).lower()
        filename = str(doc.metadata.get('filename', '')).lower()
        return sum(3 * (term in text) + 5 * (term in title or term in filename) for term in terms)
    ordered = sorted(enumerate(documents), key=lambda item: (-score(item[1]), item[0]))
    first, rest, seen = [], [], set()
    for _, doc in ordered:
        key = doc.metadata.get('document_id', doc.page_content)
        (rest if key in seen else first).append(doc)
        seen.add(key)
    return (first + rest)[:k]


class EnhancedRetrieval:
    def __init__(self, vector_store, data):
        self.vector_store, self.data = vector_store, data

    async def search(self, query, context, k):
        semantic, lexical = await asyncio.gather(
            anyio.to_thread.run_sync(partial(self.vector_store.similarity_search, query=query, k=max(k * 3, 12), filter=build_access_filter(context))),
            self.data.lexical(query, context), return_exceptions=True)
        if isinstance(semantic, Exception) and isinstance(lexical, Exception):
            raise RuntimeError('Поиск временно недоступен')
        for name, result in [('semantic', semantic), ('lexical', lexical)]:
            if isinstance(result, Exception):
                logger.warning('%s search unavailable: %s', name, type(result).__name__)
        candidates = ([] if isinstance(lexical, Exception) else lexical) + ([] if isinstance(semantic, Exception) else semantic)
        return rank_documents(query, candidates, k)

    def tool(self, original, k):
        async def search_knowledge_base(query: str, config: RunnableConfig):
            try:
                docs = await self.search(query, (config or {}).get('configurable', {}).get('user_context'), k)
                return format_documents(docs)
            except Exception:
                logger.exception('Enhanced retrieval failed')
                return 'Ошибка при обращении к базе знаний. Повторите попытку позже.'
        original.coroutine = search_knowledge_base
        return original

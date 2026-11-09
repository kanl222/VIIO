"""Merge bounded lexical and semantic candidates without another model call."""
import asyncio
import logging
from functools import partial
import anyio.to_thread
from collections.abc import Sequence
from typing import Any
from langchain_core.documents import Document
from langchain_core.runnables import RunnableConfig
from viio_rag.knowledge_data import query_terms
from viio_rag.search_tool import build_access_filter, format_documents
from viio_rag.diagnostics import capture, record

logger = logging.getLogger(__name__)


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[Document]],
    k: int = 60,
) -> dict[tuple[Any, str], float]:
    """Calculate Reciprocal Rank Fusion (RRF) scores across multiple ranked candidate lists."""
    rrf_scores: dict[tuple[Any, str], float] = {}
    for candidate_list in ranked_lists:
        for rank, doc in enumerate(candidate_list, start=1):
            key = (doc.metadata.get("document_id"), doc.page_content)
            rrf_scores[key] = rrf_scores.get(key, 0.0) + (1.0 / (k + rank))
    return rrf_scores


def rank_documents(
    query: str,
    documents: Sequence[Document],
    k: int,
    min_score: int = 0,
    rrf_scores: dict[tuple[Any, str], float] | None = None,
) -> list[Document]:
    terms = query_terms(query)
    def score(doc: Document) -> float:
        text = doc.page_content.lower()
        title = str(doc.metadata.get('title', '')).lower()
        filename = str(doc.metadata.get('filename', '')).lower()
        term_score = sum(3 * (term in text) + 5 * (term in title or term in filename) for term in terms)
        if rrf_scores:
            key = (doc.metadata.get("document_id"), doc.page_content)
            return float(term_score) + rrf_scores.get(key, 0.0)
        return float(term_score)
    scored = [(score(doc), idx, doc) for idx, doc in enumerate(documents)]
    if min_score > 0:
        scored = [item for item in scored if item[0] >= min_score]
        if not scored:
            return []
    ordered = sorted(scored, key=lambda item: (-item[0], item[1]))
    first: list[Document] = []
    rest: list[Document] = []
    seen: set[Any] = set()
    for _, _, doc in ordered:
        key = doc.metadata.get('document_id', doc.page_content)
        (rest if key in seen else first).append(doc)
        seen.add(key)
    return (first + rest)[:k]


class EnhancedRetrieval:
    def __init__(self, vector_store: Any, data: Any) -> None:
        self.vector_store: Any = vector_store
        self.data: Any = data

    async def search(self, query: str, context: Any, k: int, diagnostics: dict[str, Any] | None = None) -> list[Document]:
        with capture('search', query) as trace:
            diagnostic = diagnostics if diagnostics is not None else ({} if trace is not None else None)
            record('search_request', query=query, k=k, scope='internal')
            try:
                selected = await self._search(query, context, k, diagnostic)
            except Exception as error:
                record('retrieval', query=query, k=k, scope='internal', status='error', error=type(error).__name__)
                raise
            if diagnostic is not None:
                record('retrieval', query=query, scope='internal', status='ok',
                       context=format_documents(selected), **diagnostic)
            return selected

    async def _search(self, query: str, context: Any, k: int, diagnostics: dict[str, Any] | None = None) -> list[Document]:
        searches = [
            anyio.to_thread.run_sync(partial(self.vector_store.similarity_search, query=query, k=max(k * 3, 12), filter=build_access_filter(context))),
            self.data.lexical(query, context)]
        if getattr(self.data, 'public_index', None) is not None:
            searches.append(self.data.public_vectors(query, max(k * 3, 12)))
        results = await asyncio.gather(*searches, return_exceptions=True)
        if all(isinstance(result, Exception) for result in results):
            raise RuntimeError('Поиск временно недоступен')
        for name, result in zip(['semantic', 'lexical', 'public'], results):
            if isinstance(result, Exception):
                logger.warning('%s search unavailable: %s', name, type(result).__name__)
        valid_lists = [r for r in [results[1], results[0], *results[2:]] if not isinstance(r, Exception)]
        rrf_map = reciprocal_rank_fusion(valid_lists)
        candidates, seen = [], set()
        for candidate_list in valid_lists:
            for doc in candidate_list:
                key = (doc.metadata.get('document_id'), doc.page_content)
                if key not in seen:
                    candidates.append(doc)
                    seen.add(key)
        candidates=await self.data.authorized_candidates(candidates,context)
        selected = rank_documents(query, candidates, k, min_score=1, rrf_scores=rrf_map)
        selected = await self._expand_procedural_neighbors(selected, context)
        if diagnostics is not None:
            def snapshot(doc):
                return {'document_id': doc.metadata.get('document_id'),
                        'title': doc.metadata.get('title', ''), 'text': doc.page_content,
                        'version': doc.metadata.get('version'), 'chunk_id': doc.metadata.get('chunk_id')}
            # Только кандидаты, прошедшие актуальную проверку ACL, могут покидать сервис документов.
            diagnostics.update(candidates=[snapshot(doc) for doc in candidates],
                selected=[snapshot(doc) for doc in selected],
                branches={name: {'status': 'error' if isinstance(result, Exception) else 'ok',
                                 'count': None if isinstance(result, Exception) else len(result)}
                          for name, result in zip(['semantic', 'lexical', 'public'], results)},
                ranking='lexical term score with document diversity', k=k)
        return selected

    async def _expand_procedural_neighbors(self, selected, context, max_chars=8000):
        if not selected or not hasattr(self.vector_store, 'get_chunks'):
            return selected
        total_chars = sum(len(d.page_content) for d in selected)
        if total_chars >= max_chars:
            return selected
        seen_keys = {(d.metadata.get('document_id'), d.metadata.get('chunk_index')) for d in selected}
        needed_pairs = []
        doc_sections = {}
        for doc in selected:
            doc_id = doc.metadata.get('document_id')
            idx = doc.metadata.get('chunk_index')
            section = doc.metadata.get('section_path')
            if doc_id is None or idx is None or not section or idx < 0:
                continue
            doc_sections[(doc_id, idx)] = section
            for neighbor_idx in (idx - 1, idx + 1):
                if neighbor_idx >= 0 and (doc_id, neighbor_idx) not in seen_keys:
                    needed_pairs.append((doc_id, neighbor_idx))
                    seen_keys.add((doc_id, neighbor_idx))
        if not needed_pairs:
            return selected
        neighbors = await anyio.to_thread.run_sync(partial(self.vector_store.get_chunks, needed_pairs))
        if not neighbors:
            return selected
        valid_neighbors = []
        for n_doc in neighbors:
            n_doc_id = n_doc.metadata.get('document_id')
            n_idx = n_doc.metadata.get('chunk_index')
            n_section = n_doc.metadata.get('section_path')
            orig_section = doc_sections.get((n_doc_id, n_idx - 1)) or doc_sections.get((n_doc_id, n_idx + 1))
            if orig_section and n_section == orig_section:
                valid_neighbors.append(n_doc)
        if not valid_neighbors:
            return selected
        authorized_neighbors = await self.data.authorized_candidates(valid_neighbors, context)
        expanded = list(selected)
        for n_doc in authorized_neighbors:
            chunk_len = len(n_doc.page_content)
            if total_chars + chunk_len <= max_chars:
                expanded.append(n_doc)
                total_chars += chunk_len
            else:
                break
        expanded.sort(key=lambda d: (
            str(d.metadata.get('document_id', '')),
            d.metadata.get('chunk_index', 0)
        ))
        return expanded

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

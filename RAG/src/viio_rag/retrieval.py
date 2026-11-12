"""Merge bounded lexical and semantic candidates without another model call."""
import asyncio
import logging
import time
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
RRF_K = 60
TEXT_WEIGHT = 3
TITLE_WEIGHT = 5


def fragment_key(doc: Document) -> tuple[Any, str]:
    return doc.metadata.get('document_id'), doc.page_content


def fragment_snapshot(doc: Document, *, include_text: bool = True) -> dict:
    item = {key: doc.metadata.get(key) for key in ('document_id', 'version', 'chunk_id')}
    item['title'] = doc.metadata.get('title', '')
    for key in ('filename', 'chunk_index', 'line_start', 'section_path'):
        if key in doc.metadata:
            item[key] = doc.metadata[key]
    if include_text:
        item['text'] = doc.page_content
    return item


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[Document]],
    k: int = RRF_K,
) -> dict[tuple[Any, str], float]:
    """Calculate Reciprocal Rank Fusion (RRF) scores across multiple ranked candidate lists."""
    rrf_scores: dict[tuple[Any, str], float] = {}
    for candidate_list in ranked_lists:
        for rank, doc in enumerate(candidate_list, start=1):
            key = fragment_key(doc)
            rrf_scores[key] = rrf_scores.get(key, 0.0) + (1.0 / (k + rank))
    return rrf_scores


def rank_documents(
    query: str,
    documents: Sequence[Document],
    k: int,
    min_score: int = 0,
    rrf_scores: dict[tuple[Any, str], float] | None = None,
    *,
    details: list[dict] | None = None,
) -> list[Document]:
    terms = query_terms(query)

    def score(doc: Document) -> float:
        text = doc.page_content.lower()
        title = str(doc.metadata.get('title', '')).lower()
        filename = str(doc.metadata.get('filename', '')).lower()
        matched_text = [term for term in terms if term in text]
        matched_title = [term for term in terms if term in title]
        matched_filename = [term for term in terms if term in filename]
        term_score = TEXT_WEIGHT * len(matched_text) + TITLE_WEIGHT * len(set(matched_title + matched_filename))
        bonus = rrf_scores.get(fragment_key(doc), 0.0) if rrf_scores else 0.0
        total = term_score + bonus
        if details is not None:
            details.append(dict(term_score=term_score, rrf_score=bonus, score=total,
                                matched_text=matched_text, matched_title=matched_title,
                                matched_filename=matched_filename))
        return total
    scored = [(score(doc), idx, doc) for idx, doc in enumerate(documents)]
    if min_score > 0:
        scored = [item for item in scored if item[0] >= min_score]
        if not scored:
            return []
    ordered = sorted(scored, key=lambda item: (-item[0], item[1]))
    first: list[tuple[int, Document]] = []
    rest: list[tuple[int, Document]] = []
    seen: set[Any] = set()
    for rank, (_, index, doc) in enumerate(ordered, 1):
        key = doc.metadata.get('document_id', doc.page_content)
        repeated = key in seen
        (rest if repeated else first).append((index, doc))
        seen.add(key)
        if details is not None:
            details[index].update(score_rank=rank, deferred_for_diversity=repeated)
    diversified = first + rest
    if details is not None:
        for rank, (index, _) in enumerate(diversified, 1):
            details[index]['diversity_rank'] = rank
    return [doc for _, doc in diversified[:k]]


def reorder_lost_in_the_middle(documents: Sequence[Any]) -> list[Any]:
    """Reorder documents to mitigate the 'lost in the middle' phenomenon (Liu et al. 2023).

    Places the most relevant documents at the beginning and end of the sequence
    where LLMs exhibit primacy and recency bias, and less relevant documents in the middle.
    """
    docs = list(documents)
    if len(docs) <= 2:
        return docs

    left: list[Any] = []
    right: list[Any] = []
    for i, doc in enumerate(docs):
        if i % 2 == 0:
            left.append(doc)
        else:
            right.insert(0, doc)
    return left + right


class EnhancedRetrieval:
    def __init__(self, vector_store: Any, data: Any) -> None:
        self.vector_store: Any = vector_store
        self.data: Any = data

    async def search(self, query: str, context: Any, k: int, diagnostics: dict[str, Any] | None = None) -> list[Document]:
        with capture('search', query) as trace:
            diagnostic = diagnostics if diagnostics is not None else ({} if trace is not None else None)
            if trace is not None:
                diagnostic['search_trace_id'] = trace['id']
            record('search_request', query=query, k=k, scope='internal')
            try:
                selected = await self._search(query, context, k, diagnostic)
            except Exception as error:
                record('retrieval', query=query, scope='internal', status='error', error=type(error).__name__,
                       **{'k': k, **(diagnostic or {})})
                raise
            if diagnostic is not None:
                record('retrieval', query=query, scope='internal', status='ok',
                       context=format_documents(selected), **diagnostic)
            return selected

    async def _search(self, query: str, context: Any, k: int, diagnostics: dict[str, Any] | None = None) -> list[Document]:
        names = ['semantic', 'lexical']
        searches = [
            anyio.to_thread.run_sync(partial(self.vector_store.similarity_search, query=query, k=max(k * 3, 12), filter=build_access_filter(context))),
            self.data.lexical(query, context)]
        if getattr(self.data, 'public_index', None) is not None:
            names.append('public')
            searches.append(self.data.public_vectors(query, max(k * 3, 12)))
        timings = {}
        if diagnostics is not None:
            async def measured(name, search):
                started = time.monotonic()
                try:
                    return await search
                finally:
                    timings[name] = round((time.monotonic() - started) * 1000)

            searches = [measured(name, search) for name, search in zip(names, searches)]
        results = await asyncio.gather(*searches, return_exceptions=True)
        if diagnostics is not None:
            diagnostics['branches'] = {
                name: {'duration_ms': timings[name], 'status': 'error', 'count': None, 'error': type(result).__name__}
                if isinstance(result, Exception)
                else {'duration_ms': timings[name], 'status': 'ok', 'count': len(result)}
                for name, result in zip(names, results)
            }
        if all(isinstance(result, Exception) for result in results):
            raise RuntimeError('Поиск временно недоступен')
        for name, result in zip(names, results):
            if isinstance(result, Exception):
                logger.warning('%s search unavailable: %s', name, type(result).__name__)
        valid_lists = [r for r in [results[1], results[0], *results[2:]] if not isinstance(r, Exception)]
        rrf_map = reciprocal_rank_fusion(valid_lists)
        candidates, seen = [], set()
        for candidate_list in valid_lists:
            for doc in candidate_list:
                key = fragment_key(doc)
                if key not in seen:
                    candidates.append(doc)
                    seen.add(key)
        candidate_count = len(candidates)
        candidates = await self.data.authorized_candidates(candidates, context)
        details = [] if diagnostics is not None else None
        min_score = 1
        ranked = rank_documents(query, candidates, k, min_score=min_score, rrf_scores=rrf_map, details=details)
        expanded = await self._expand_procedural_neighbors(ranked, context)
        selected = reorder_lost_in_the_middle(expanded)
        if diagnostics is not None:
            positions = {fragment_key(doc): {} for doc in candidates}
            for name, result in zip(names, results):
                if not isinstance(result, Exception):
                    for rank, doc in enumerate(result, 1):
                        if fragment_key(doc) in positions:
                            positions[fragment_key(doc)].setdefault(name, []).append(rank)
            final_ranks = {fragment_key(doc): rank for rank, doc in enumerate(selected, 1)}
            ranked_keys = {fragment_key(doc) for doc in ranked}
            # Только кандидаты, прошедшие актуальную проверку ACL, могут покидать сервис документов.
            diagnostics.update(
                candidates=[dict(fragment_snapshot(doc, include_text=False), **detail,
                                 branch_ranks=positions[fragment_key(doc)],
                                 final_rank=final_ranks.get(fragment_key(doc)))
                            for doc, detail in zip(candidates, details)],
                selected=[dict(fragment_snapshot(doc), selection_reason='ranked' if fragment_key(doc) in ranked_keys
                               else 'procedural_neighbor') for doc in selected],
                ranking='lexical term score with document diversity', k=k,
                ranking_details=dict(query_terms=query_terms(query), min_score=min_score, rrf_k=RRF_K,
                                     text_weight=TEXT_WEIGHT, title_or_filename_weight=TITLE_WEIGHT,
                                     before_access_check=candidate_count, after_access_check=len(candidates)),
            )
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

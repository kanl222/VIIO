"""Managed public-only RAG; receives no conversation, identity or internal context."""
import json
from pathlib import Path
from datetime import datetime, timezone
import httpx
from openai import AsyncOpenAI
from fastapi import HTTPException
from viio_rag.prompts import ASSISTANT_YANDEX_RAG_PROMPT, CONSULTANT_YANDEX_RAG_PROMPT


async def answer(settings, data, question, role):
    if not question.confirmed_public_for_external_cloud:
        raise HTTPException(403, 'Подтвердите публичность вопроса для внешнего облака')
    if not question.message.strip():
        raise HTTPException(400, 'Укажите вопрос')
    if not all((settings.rag_yandex_api_key, settings.rag_yandex_folder_id, settings.rag_yandex_index_id)):
        raise HTTPException(503, 'Yandex RAG не настроен')
    try:
        manifest = json.loads(Path(settings.rag_yandex_manifest).read_text(encoding='utf-8'))
        if manifest['index_id'] != settings.rag_yandex_index_id or not manifest['files']:
            raise ValueError('Invalid index manifest')
        if datetime.fromisoformat(manifest['expires_at']) <= datetime.now(timezone.utc):
            raise ValueError('Expired index manifest')
        files = manifest['files']
        if not await data.cloud_corpus_is_public(files):
            raise HTTPException(503, 'Публичный индекс устарел или доступ к материалам изменился')
        name = settings.rag_yandex_model
        model = name if name.startswith('gpt://') else f'gpt://{settings.rag_yandex_folder_id}/{name}'
        prompt = (settings.rag_yandex_assistant_prompt or ASSISTANT_YANDEX_RAG_PROMPT) if role == 'assistant' else (
            settings.rag_yandex_consultant_prompt or CONSULTANT_YANDEX_RAG_PROMPT)
        async with AsyncOpenAI(api_key=settings.rag_yandex_api_key,
            project=settings.rag_yandex_folder_id, base_url='https://ai.api.cloud.yandex.net/v1',
            max_retries=0, http_client=httpx.AsyncClient(timeout=60, trust_env=False, follow_redirects=False),
            default_headers={'x-data-logging-enabled': 'false'}) as client:
            response = await client.responses.create(model=model, instructions=prompt,
                input=question.message.strip(), tools=[{'type': 'file_search',
                    'vector_store_ids': [settings.rag_yandex_index_id]}],
                tool_choice={'type': 'file_search'}, temperature=0, max_output_tokens=2048, store=False)
        known = {item['file_id']: item for item in files}
        sources = {}
        searched = False
        for item in response.output:
            if item.type == 'file_search_call' and item.status == 'completed':
                searched = True
            if item.type == 'message':
                for part in item.content:
                    for citation in getattr(part, 'annotations', []):
                        if citation.type == 'file_citation':
                            source = known.get(citation.file_id)
                            if source is None:
                                raise ValueError('Unknown citation')
                            sources[source['document_id']] = {'document_id': source['document_id'],
                                'title': source['title'], 'file_id': citation.file_id}
        if not searched or not sources or not response.output_text.strip():
            raise HTTPException(503, 'Облачный поиск не подтвердил ответ источниками. Уточните вопрос.')
        if not await data.cloud_corpus_is_public(files):
            raise HTTPException(503, 'Доступ к публичным материалам изменился. Повторите вопрос.')
        return {'reply': response.output_text.strip(), 'sources': list(sources.values())}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, 'Yandex RAG недоступен. Проверьте индекс и его срок хранения.') from None

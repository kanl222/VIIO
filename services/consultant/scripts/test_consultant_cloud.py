"""Live consultant test with an isolated public fixture gateway, never the real KB."""
import asyncio
import argparse
import hashlib
import json
import logging
import os
from pathlib import Path

import httpx
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from viio_consultant import create_app
from viio_consultant.application.agent import PublicConsultant
from viio_consultant.config import ConsultantSettings
from viio_consultant.infrastructure.models import create_model
from viio_consultant.infrastructure.source import HttpPublicationSource


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--approved-publication', type=Path)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    values = {**dotenv_values(Path(__file__).resolve().parents[2] / '.env'), **os.environ}
    material = {'id': 'public-fixture', 'title': 'Публичная памятка: закон Ома',
        'content': 'Закон Ома: сила тока I равна напряжению U, делённому на сопротивление R. '
                   'При напряжении 12 вольт и сопротивлении 6 ом сила тока равна 2 ампера.'}
    if args.approved_publication:
        material = json.loads(args.approved_publication.read_text(encoding='utf-8'))
        allowed = json.loads((Path(__file__).resolve().parents[2] / 'docs/yandex-publication-matches.json').read_text(encoding='utf-8'))
        digest = hashlib.sha256((material['title'] + '\n' + material['content']).encode()).hexdigest()
        assert digest in {row['sha256'] for row in allowed['matched']}
    searches = []
    validations = []
    generations = []
    import viio_consultant.application.agent as agent_module
    original_model_call = agent_module.model_call
    async def inspect_call(model, messages):
        result = await original_model_call(model, messages)
        generations.append({'finish_reason': result.response_metadata.get('finish_reason'),
                            'tool_count': len(result.tool_calls), 'content': result.content})
        return result
    agent_module.model_call = inspect_call
    def gateway(request):
        if request.url.path.endswith('/search'):
            searches.append(json.loads(request.content)['message'])
            return httpx.Response(200, json={'items': [material]})
        if request.url.path.endswith('/validate'):
            validations.append(json.loads(request.content)['publication_ids'])
            return httpx.Response(200, json={'publication_ids': [material['id']]})
        raise AssertionError('Unexpected gateway route')
    try:
        settings = ConsultantSettings(enabled=True, provider='yandex',
            api_key='public-test-api-key-' + 'a' * 32, source_key='public-test-source-' + 's' * 32,
            yandex_api_key=values.get('CONSULTANT_YANDEX_API_KEY'),
            yandex_folder_id=values.get('CONSULTANT_YANDEX_FOLDER_ID'),
            yandex_model=values.get('CONSULTANT_YANDEX_MODEL') or 'aliceai-llm',
            model=values.get('CONSULTANT_MODEL') or None,
            max_tokens=2048,
            cloud_public_material_hashes=[hashlib.sha256(
                (material['title'] + '\n' + material['content']).encode()).hexdigest()])
        source_client = httpx.AsyncClient(base_url='http://public-fixture', transport=httpx.MockTransport(gateway))
        source = HttpPublicationSource(source_client, cloud_public_material_hashes=settings.cloud_public_material_hashes)
        agent = PublicConsultant(source, create_model(settings), search_first=True)
        with TestClient(create_app(settings, agent=agent)) as client:
            url = '/api/v1/consultant/chat'
            headers = {'X-Consultant-Key': settings.api_key}
            question = 'По памятке о законе Ома: какой ток при напряжении 12 В и сопротивлении 6 Ом?'
            if args.approved_publication:
                question = 'Кратко перечисли основные действия, описанные в инструкции: ' + material['title']
            rejected = client.post(url, headers=headers, json={'message': question})
            assert rejected.status_code == 403 and not searches
            response = client.post(url, headers=headers, json={'message': question,
                'confirmed_public_for_external_cloud': True})
            payload = response.json()
            passed = (response.status_code == 200 and searches and validations
                and any(s['publication_id'] == material['id'] for s in payload.get('sources', []))
                and bool(payload.get('reply')) and (args.approved_publication or '2' in payload.get('reply', '')))
            print(json.dumps({'status': 'passed' if passed else 'failed',
                'http_status': response.status_code, 'unconfirmed_status': rejected.status_code,
                'search_count': len(searches), 'validation_count': len(validations),
                'generations': generations,
                'response': payload, 'gateway': 'isolated gateway with approved database publication'
                if args.approved_publication else 'isolated public fixture; no real documents'}, ensure_ascii=False))
        asyncio.run(source_client.aclose())
        return 0 if passed else 1
    except Exception as error:
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

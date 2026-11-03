"""Isolated, resumable one-day Yandex managed RAG test using approved public files."""
import hashlib
import argparse
import json
import logging
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
import time

from dotenv import dotenv_values
from openai import OpenAI
from yandex_ai_studio_sdk import AIStudio
from yandex_ai_studio_sdk.search_indexes import HybridSearchIndexType
from yandex_ai_studio_sdk._types.operation import Operation
from yandex_ai_studio_sdk._search_indexes.search_index import SearchIndex
from yandex.cloud.ai.assistants.v1.searchindex.search_index_pb2 import SearchIndex as ProtoSearchIndex

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / 'RAG/state/yandex-managed-rag-test-state.json'
REPORT = ROOT / 'RAG/reports/yandex-managed-rag-test-2026-11-02.json'


def save(state):
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    STATE.parent.mkdir(parents=True, exist_ok=True)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    logging.disable(logging.CRITICAL)
    values = {**dotenv_values(ROOT / '.env'), **os.environ}
    key, folder = values['CONSULTANT_YANDEX_API_KEY'], values['CONSULTANT_YANDEX_FOLDER_ID']
    approval = json.loads((ROOT / 'docs/yandex-public-canonical-approval.json').read_text(encoding='utf-8'))
    files = []
    for item in approval['files']:
        path = Path(item['path'])
        assert path.parent.resolve() == (ROOT / 'data/gosapteka56/2026-10-27/canonical').resolve()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256'], path.name
        files.append(path)
    assert len(files) == 41
    state = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {
        'created_at': datetime.now(timezone.utc).isoformat(), 'ttl_days': 1, 'files': {}}
    assert datetime.now(timezone.utc) < datetime.fromisoformat(state['created_at']) + timedelta(days=1)
    sdk = AIStudio(folder_id=folder, auth=key, enable_server_data_logging=False)
    for path in files:
        if path.name not in state['files']:
            uploaded = sdk.files.upload(path, name='viio-public-test-' + path.name,
                mime_type='text/plain', ttl_days=1, expiration_policy='static',
                labels={'project': 'viio', 'purpose': 'public-rag-test'}, timeout=30)
            state['files'][path.name] = uploaded.id
            save(state)
            print(json.dumps({'stage': 'uploaded', 'count': len(state['files']), 'total': 41}), flush=True)
    if not state.get('index_id'):
        if state.get('operation_id'):
            operation = Operation(id=state['operation_id'], sdk=sdk,
                result_type=SearchIndex, proto_result_type=ProtoSearchIndex)
        else:
            operation = sdk.search_indexes.create_deferred(list(state['files'].values()),
                name='viio-public-rag-test-2026-11-02', index_type=HybridSearchIndexType(),
                ttl_days=1, expiration_policy='static', labels={'project': 'viio', 'purpose': 'public-rag-test'})
            state['operation_id'] = operation.id
            save(state)
        print(json.dumps({'stage': 'index_building', 'operation_id': operation.id}), flush=True)
        for attempt in range(120):
            status = operation.get_status(timeout=30)
            if status.done:
                index = operation.get_result(timeout=30)
                state['index_id'] = index.id
                save(state)
                break
            if attempt and attempt % 6 == 0:
                print(json.dumps({'stage': 'index_building', 'elapsed_seconds': attempt * 5}), flush=True)
            time.sleep(5)
        else:
            raise TimeoutError('Index not ready within ten minutes')
    index = sdk.search_indexes.get(state['index_id'])
    indexed_files = list(index.list_files())
    print(json.dumps({'stage': 'indexed_file_count', 'count': len(indexed_files)}), flush=True)
    assert len(indexed_files) == 41, len(indexed_files)
    print(json.dumps({'stage': 'index_ready', 'index_id': index.id, 'files': len(indexed_files)}), flush=True)
    name = values.get('CONSULTANT_YANDEX_MODEL') or 'aliceai-llm'
    model = name if name.startswith('gpt://') else f'gpt://{folder}/{name}'
    # Current official SDK manages files/indexes; Responses API is documented via OpenAI client.
    client = OpenAI(api_key=key, project=folder, base_url='https://ai.api.cloud.yandex.net/v1',
        timeout=60, max_retries=0, default_headers={'x-data-logging-enabled': 'false'})
    results = []
    questions = [('Как создать заявку на предоставление доступа сотруднику в ИС ЛО?', '01.txt'),
        ('Как найти электронный рецепт по СНИЛС?', '29.txt'),
        ('Для чего нужен модуль Управление сотрудниками в ИС ЛО?', '01.txt')]
    for question, expected_file in questions:
        started = time.monotonic()
        response = client.responses.create(model=model, input=question,
            instructions='Отвечай по-русски только по найденным файлам. Обязательно используй поиск по файлам. '
                'Дай краткий ответ и ссылки на источники. Если сведений нет, прямо сообщи об этом.',
            tools=[{'type': 'file_search', 'vector_store_ids': [index.id]}],
            tool_choice='required',
            max_output_tokens=2048, temperature=0, store=False)
        citations, searches = [], []
        for item in response.output:
            if item.type == 'file_search_call':
                searches.append({'status': item.status, 'queries': getattr(item, 'queries', [])})
            if item.type == 'message':
                for part in item.content:
                    for annotation in getattr(part, 'annotations', []):
                        if annotation.type == 'file_citation':
                            citations.append({'file_id': annotation.file_id,
                                'filename': getattr(annotation, 'filename', '')})
        answer = response.output_text
        passed = bool(answer.strip() and searches and citations
            and any(c['file_id'] == state['files'][expected_file] for c in citations)
            and 'не наш' not in answer.lower() and 'нет сведений' not in answer.lower())
        results.append({'question': question, 'reply': answer, 'citations': citations,
            'searches': searches, 'expected_file': expected_file, 'passed': passed,
            'duration_seconds': round(time.monotonic() - started, 2)})
        report = {'index_id': index.id, 'source_files': 41, 'ttl_days': 1,
            'model': name, 'status': ('passed' if all(r['passed'] for r in results) else 'failed')
                if len(results) == 3 else 'incomplete',
            'results': results}
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'stage': 'answer', 'question_number': len(results), 'passed': passed,
            'citations': len(citations)}), flush=True)
    return 0 if all(row['passed'] for row in results) else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__,
            'http_status': getattr(error, 'status_code', None)}), flush=True)
        raise SystemExit(1)

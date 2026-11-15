"""Retry only failed links in the isolated managed RAG test index."""
import json
import logging
from pathlib import Path
from dotenv import dotenv_values
from openai import OpenAI
from yandex_ai_studio_sdk import AIStudio

logging.disable(logging.CRITICAL)
root = Path(__file__).resolve().parents[2]
values = dotenv_values(root / '.env')
state = json.loads((root / 'RAG/state/yandex-managed-rag-test-state.json').read_text(encoding='utf-8'))
client = OpenAI(api_key=values['CONSULTANT_YANDEX_API_KEY'],
    project=values['CONSULTANT_YANDEX_FOLDER_ID'], base_url='https://ai.api.cloud.yandex.net/v1',
    timeout=30, max_retries=0)
sdk = AIStudio(folder_id=values['CONSULTANT_YANDEX_FOLDER_ID'],
    auth=values['CONSULTANT_YANDEX_API_KEY'], enable_server_data_logging=False)
failed = list(client.vector_stores.files.list(state['index_id'], filter='failed'))
assert all(file.id in state['files'].values() for file in failed)
assert len(failed) <= 2
for file in failed:
    client.vector_stores.files.delete(file.id, vector_store_id=state['index_id'])
if failed:
    operation = sdk.search_indexes.get(state['index_id']).add_files_deferred([file.id for file in failed])
    state['retry_operation_id'] = operation.id
    (root / 'RAG/state/yandex-managed-rag-test-state.json').write_text(json.dumps(state, indent=2), encoding='utf-8')
    print(json.dumps({'stage': 'retrying_failed_files', 'count': len(failed), 'operation_id': operation.id}), flush=True)
    operation.wait(timeout=30, poll_timeout=120, poll_interval=5)
result = client.vector_stores.retrieve(state['index_id'])
print(json.dumps({'status': result.status, 'file_counts': result.file_counts.model_dump()}), flush=True)

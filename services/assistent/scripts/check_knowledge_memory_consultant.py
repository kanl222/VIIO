"""Synthetic live checks. Run begin, restart assistent, verify, then cleanup.

No real documents are published. Credentials come from Settings and are never
printed. Fixture IDs in /tmp/viio-knowledge-check.json bound cleanup scope.
"""
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import uuid

import httpx
from psycopg.conninfo import make_conninfo
from qdrant_client import QdrantClient, models
from langchain_core.messages import HumanMessage, AIMessage
from viio_documents.config import Settings
from assistent.infrastructure.llm import create_chat_model
from assistent.application.graph_builder import build_agent_graph, scope_key
from viio_documents.infrastructure.storage.file_store import LocalFileStore
from assistent.infrastructure.persistence.postgres_saver import init_async_postgres_saver

STATE = Path('/tmp/viio-knowledge-check.json')


async def main(mode):
    settings = Settings()
    dsn = make_conninfo(host=settings.postgres_host, port=settings.postgres_port, dbname=settings.postgres_db,
                       user=settings.postgres_user, password=settings.postgres_password)
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8000', timeout=150,
                                headers={'X-Internal-Token': settings.internal_api_key}) as client:
        if mode != 'cleanup':
            async with asyncio.timeout(45):
                while True:
                    try:
                        health = await client.get('/health', timeout=2)
                        if health.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    await asyncio.sleep(0.25)
        async def ask(message, thread, context):
            response = await client.post('/api/v1/chat', json={'message': message, 'thread_id': thread, 'user_context': context})
            response.raise_for_status()
            return response.json()

        if mode == 'begin':
            token = uuid.uuid4().hex
            state = {'token': token, 'owner': 'knowledge-check-' + token,
                     'threads': ['knowledge-' + uuid.uuid4().hex for _ in range(3)]}
            STATE.write_text(json.dumps(state))
            code_name = 'billing_' + token + '.py'
            response = await client.post('/api/v1/documents/bulk-upload', params={'owner_id': state['owner'], 'is_public': True}, files=[
                ('files', (code_name, b'def CalculateTestInvoice(items):\n    return sum(items)\n', 'text/plain')),
                ('files', (token + '.txt', ('INTERNAL_MARKER_' + token + ' must stay internal.').encode(), 'text/plain'))])
            response.raise_for_status()
            state['tasks'] = response.json()['tasks']
            STATE.write_text(json.dumps(state))
            async with asyncio.timeout(180):
                while True:
                    statuses = await client.get('/api/v1/documents/bulk-status', params={'task_ids': ','.join(task['task_id'] for task in state['tasks'])})
                    statuses.raise_for_status()
                    tasks = statuses.json()['tasks']
                    if len(tasks) == 2 and all(task['status'] in {'ready', 'error'} for task in tasks):
                        break
                    await asyncio.sleep(1)
            assert all(task['status'] == 'ready' for task in tasks), tasks
            state['documents'] = {task['filename']: task['document_id'] for task in tasks}
            state['public_source'] = state['documents'][token + '.txt']
            STATE.write_text(json.dumps(state))
            programmer = {'user_id': state['owner'], 'role': 'programmer', 'departments': ['it']}
            remembered = await ask('/remember Тестовая метка ' + token, state['threads'][0], programmer)
            assert 'Сохранён' in remembered['reply']
            response = await ask('/facts', state['threads'][0], {**programmer, 'role': 'employee'})
            assert token not in response['reply']
            response = await ask('/code На основании документа «' + code_name[:-3] + '» из базы знаний напиши пример вызова CalculateTestInvoice.', state['threads'][1], programmer)
            assert 'CalculateTestInvoice' in response['reply']
            assert state['documents'][code_name] in {source['document_id'] for source in response['sources']}
            denied = await ask('/code Покажи исходный код тестового файла', state['threads'][2], {**programmer, 'role': 'employee'})
            assert not denied['sources'] and 'CalculateTestInvoice' not in denied['reply']
            print(json.dumps({'stage': 'code_and_facts', 'coder_sources': 'passed', 'employee_denied': 'passed', 'facts_scope': 'passed'}), flush=True)
            publication = await client.post('/api/v1/documents/publication/' + state['public_source'], params={'publisher_id': state['owner']},
                json={'title': 'Тестовая поддержка ' + token, 'content': 'Тестовая поддержка ' + token + ' работает с 09:00 до 18:00 по будням.'})
            publication.raise_for_status()
            response = await client.post('/api/v1/consultant/chat', headers={'X-Consultant-Key': settings.consultant_api_key}, json={'message': 'Когда работает поддержка ' + token + '?'})
            response.raise_for_status()
            assert response.json()['sources'] and ('09' in response.json()['reply'] or '18' in response.json()['reply'])
            assert 'INTERNAL_MARKER_' not in response.json()['reply']
            unknown = await client.post('/api/v1/consultant/chat', headers={'X-Consultant-Key': settings.consultant_api_key}, json={'message': 'INTERNAL_MARKER_' + token})
            unknown.raise_for_status()
            assert not unknown.json()['sources']
            print(json.dumps({'stage': 'consultant', 'published_answer': 'passed', 'internal_marker_absent': 'passed'}), flush=True)
            saver, pool = await init_async_postgres_saver(dsn)
            try:
                graph = build_agent_graph(create_chat_model(settings), checkpointer=saver, system_prompt='Ты помощник учебного проекта. Отвечай кратко.', summary_after=12)
                config = {'configurable': {'thread_id': state['threads'][0]}}
                messages = [HumanMessage(content='Цель проекта invoice_demo: проверка суммаризации.'), AIMessage(content='Цель принята.')]
                for number in range(14):
                    messages += [HumanMessage(content=f'Подтверждён этап номер {number}.'), AIMessage(content='Принято.')]
                await graph.aupdate_state(config, {'messages': messages, 'history_scope': scope_key(programmer), 'history_start': 0}, as_node='agent')
                result = await graph.ainvoke({'messages': [('user', 'Напомни название проекта и его цель.')]}, config,
                                            context={'access_scope': scope_key(programmer)})
                assert result.get('dialogue_summary') and result.get('summary_upto', 0) > 0
                assert 'invoice_demo' in str(result['messages'][-1].content)
            finally:
                await pool.close()
            print(json.dumps({'stage': 'summary', 'actual_model_summary': 'passed', 'goal_recalled': 'passed'}), flush=True)
        elif mode == 'verify':
            state = json.loads(STATE.read_text())
            context = {'user_id': state['owner'], 'role': 'programmer', 'departments': ['it']}
            response = await ask('/facts', state['threads'][0], context)
            assert state['token'] in response['reply']
            saver, pool = await init_async_postgres_saver(dsn)
            try:
                snapshot = await saver.aget_tuple({'configurable': {'thread_id': state['threads'][0]}})
                assert snapshot and snapshot.checkpoint['channel_values'].get('dialogue_summary')
            finally:
                await pool.close()
            withdrawal = await client.delete('/api/v1/documents/publication/' + state['public_source'])
            withdrawal.raise_for_status()
            response = await client.post('/api/v1/consultant/chat', headers={'X-Consultant-Key': settings.consultant_api_key},
                json={'message': 'Когда работает поддержка ' + state['token'] + '?'})
            response.raise_for_status()
            assert not response.json()['sources']
            print(json.dumps({'stage': 'after_restart', 'facts': 'passed', 'summary': 'passed', 'withdrawal': 'passed'}))
        elif mode == 'cleanup':
            state = json.loads(STATE.read_text())
            saver, pool = await init_async_postgres_saver(dsn)
            store = LocalFileStore(settings.documents_storage_path)
            vector = QdrantClient(url=settings.qdrant_url)
            try:
                for thread in state['threads']:
                    await saver.adelete_thread(thread)
                    identity = json.dumps([thread, state['owner']], ensure_ascii=False)
                    for prefix in ('coder-text-', 'route-text-'):
                        await saver.adelete_thread(prefix + hashlib.sha256(identity.encode()).hexdigest())
                async with pool.connection() as conn:
                    rows = await (await conn.execute('SELECT * FROM document_upload_jobs WHERE id=ANY(%s)', ([task['task_id'] for task in state.get('tasks', [])],))).fetchall()
                    for row in rows:
                        if row['document_id']:
                            vector.delete(settings.qdrant_collection_name, models.FilterSelector(filter=models.Filter(must=[
                                models.FieldCondition(key='metadata.document_id', match=models.MatchValue(value=row['document_id']))])), wait=True)
                            await conn.execute('DELETE FROM documents WHERE id=%s AND owner_id=%s', (row['document_id'], state['owner']))
                        store.delete(row['payload']['storage_key'])
                    await conn.execute('DELETE FROM document_upload_jobs WHERE id=ANY(%s)', ([row['id'] for row in rows],))
                    await conn.execute('DELETE FROM user_facts WHERE owner_id=%s', (state['owner'],))
            finally:
                vector.close()
                await pool.close()
            print(json.dumps({'stage': 'cleanup', 'fixture_jobs_removed': len(rows)}))


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1]))

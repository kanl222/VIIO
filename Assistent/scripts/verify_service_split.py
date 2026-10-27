"""Run in document-worker. Live integration; clean only this run's fixtures."""
import asyncio
import hashlib
import json
import time
import uuid

import httpx
import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from qdrant_client import QdrantClient, models

from assistent.config import Settings
from assistent.services.file_store import LocalFileStore
from assistent.utils.postgres_saver import init_async_postgres_saver


def emit(**row):
    print(json.dumps(row,ensure_ascii=False),flush=True)


async def main():
    s=Settings(); owner='split-eval-'+uuid.uuid4().hex
    docs='http://documents:8000'; agent='http://assistent:8000'
    tasks=[]; identifiers=[]; thread=owner+'-chat'
    dsn=make_conninfo(host=s.postgres_host,port=s.postgres_port,dbname=s.postgres_db,
        user=s.postgres_user,password=s.postgres_password)
    async with httpx.AsyncClient(timeout=170,headers={'X-Internal-Token':s.internal_api_key}) as client:
        try:
            # The queue is shared with the worker, not held in the agent process.
            files=[('files',(f'probe-{i}.txt',(f'Проверка отдельного сервиса документов {owner} {i}.\n'*65).encode(),'text/plain')) for i in range(4)]
            response=await client.post(docs+'/api/v1/documents/bulk-upload',params={'owner_id':owner,
                'allowed_roles':'employee','allowed_departments':''},files=files)
            assert response.status_code==202,response.text
            tasks=[row['task_id'] for row in response.json()['tasks']]
            for _ in range(100):
                statuses=(await client.get(docs+'/api/v1/documents/bulk-status',params={'task_ids':','.join(tasks)})).json()['tasks']
                if any(row['status']=='processing' for row in statuses): break
                await asyncio.sleep(.05)
            active=any(row['status'] in {'queued','processing'} for row in statuses)
            start=time.monotonic()
            general,consultant=await asyncio.gather(
                client.post(agent+'/api/v1/chat',json={'message':'Привет!','thread_id':thread,
                    'user_context':{'user_id':owner,'role':'employee','departments':[]}}),
                client.post(agent+'/api/v1/consultant/chat',headers={'X-Consultant-Key':s.consultant_api_key},
                    json={'message':'Для чего в ИС ЛО нужен модуль Управление сотрудниками?'}))
            assert general.status_code==consultant.status_code==200
            assert consultant.json()['sources']
            emit(case='agents_during_bulk',queue_active_at_request_start=active,seconds=round(time.monotonic()-start,2),
                main=general.json(),consultant=consultant.json())
            async with asyncio.timeout(150):
                while True:
                    statuses=(await client.get(docs+'/api/v1/documents/bulk-status',params={'task_ids':','.join(tasks)})).json()['tasks']
                    if all(row['status'] in {'ready','error'} for row in statuses):break
                    await asyncio.sleep(.3)
            assert all(row['status']=='ready' for row in statuses),statuses
            identifiers.extend(row['document_id'] for row in statuses)
            emit(case='bulk_completed',files=len(statuses))
            private='PRIVATE_CANARY_'+uuid.uuid4().hex
            result=await client.post(docs+'/api/v1/documents',json={'title':'Закрытый исходник теста сервиса',
                'content':private,'owner_id':owner,'is_public':False,'allowed_roles':['programmer'],'allowed_departments':[]})
            assert result.status_code==201,result.text
            identifier=result.json()['document_id'];identifiers.append(identifier)
            original=await client.get(agent+'/api/v1/documents/download/'+identifier,params={'user_id':owner,'user_role':'programmer'})
            assert original.status_code==200 and hashlib.sha256(original.content).hexdigest()==hashlib.sha256(private.encode()).hexdigest()
            denied=await client.get(docs+'/api/v1/documents/download/'+identifier,params={'user_id':'other','user_role':'employee','departments':''})
            assert denied.status_code==403
            source=await client.post(docs+'/api/v1/knowledge/search',json={'query':private,'user_context':{'user_id':'other','role':'employee','departments':[]}})
            assert source.status_code==200 and private not in source.text
            published=await client.post(docs+'/api/v1/documents/publication/'+identifier,params={'publisher_id':owner},
                json={'title':'Публичная памятка проверки сервиса','content':'Публичная памятка проверки сервиса. Одобренный текст: PUBLIC_CANARY. Для теста используйте отдельный сервис документов.'})
            assert published.status_code==200,published.text
            source=await client.post(docs+'/api/v1/consultant-materials/search',headers={'X-Publication-Key':s.consultant_source_key},
                json={'message':'Публичная памятка проверки сервиса'})
            assert source.status_code==200 and 'PUBLIC_CANARY' in source.text and private not in source.text
            emit(case='worker_write_publication_acl_and_compatibility_download',passed=True)
            for path in ['/api/v1/knowledge/search','/api/v1/documents/catalog']:
                denied=await client.request('POST' if 'search' in path else 'GET',docs+path,
                    headers={'X-Internal-Token':'','X-Publication-Key':s.consultant_source_key},json={'query':private} if 'search' in path else None)
                assert denied.status_code==401
            context={'user_id':owner,'role':'employee','departments':[]}
            remembered=await client.post(agent+'/api/v1/chat',json={'message':'/remember Проверка личных фактов после выделения документов','thread_id':thread,'user_context':context})
            assert remembered.status_code==200 and 'Сохранён' in remembered.json()['reply'],remembered.text
            facts=await client.post(agent+'/api/v1/chat',json={'message':'/facts','thread_id':thread,'user_context':context})
            assert 'Проверка личных фактов' in facts.text
            emit(case='main_personal_facts_preserved',passed=True)
        finally:
            for task in tasks:
                await client.post(docs+'/api/v1/documents/bulk-cancel/'+task)
            async with asyncio.timeout(160):
                while tasks:
                    states=(await client.get(docs+'/api/v1/documents/bulk-status',params={'task_ids':','.join(tasks)})).json()['tasks']
                    if not any(row['status']=='processing' for row in states):break
                    await asyncio.sleep(.5)
            # Discover any fixture jobs even if indexing failed before returning IDs.
            async with await psycopg.AsyncConnection.connect(dsn,row_factory=dict_row) as conn:
                jobs=await(await conn.execute('SELECT * FROM document_upload_jobs WHERE id=ANY(%s)',(tasks,))).fetchall()
                own=await(await conn.execute('SELECT * FROM documents WHERE owner_id=%s',(owner,))).fetchall()
            for row in own:
                await client.delete(docs+'/api/v1/documents/publication/'+row['id'])
            vector=QdrantClient(url=s.qdrant_url);store=LocalFileStore(s.documents_storage_path)
            try:
                async with await psycopg.AsyncConnection.connect(dsn) as conn:
                    for row in own:
                        vector.delete(s.qdrant_collection_name,models.FilterSelector(filter=models.Filter(must=[
                            models.FieldCondition(key='metadata.document_id',match=models.MatchValue(value=row['id']))])),wait=True)
                        await conn.execute('DELETE FROM documents WHERE id=%s AND owner_id=%s',(row['id'],owner))
                        if row['storage_key']:store.delete(row['storage_key'])
                    for job in jobs:
                        store.delete(job['payload']['storage_key'])
                    await conn.execute('DELETE FROM document_upload_jobs WHERE id=ANY(%s)',(tasks,))
                    await conn.execute('DELETE FROM user_facts WHERE owner_id=%s',(owner,))
            finally:vector.close()
            saver,pool=await init_async_postgres_saver(dsn)
            try:await saver.adelete_thread(thread)
            finally:await pool.close()
            emit(case='cleanup',own_documents_removed=len(own),own_jobs_removed=len(jobs))


if __name__=='__main__':asyncio.run(main())

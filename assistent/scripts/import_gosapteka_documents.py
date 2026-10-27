"""Trusted one-off import of reviewed public originals, preserving files and ACLs.

Run inside assistent with the prepared directory copied to /tmp/gosapteka-import.
No source programs execute. Publication parts avoid truncating long documents.
"""
import asyncio
import hashlib
import json
import mimetypes
from pathlib import Path
import uuid

import httpx
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from langgraph.checkpoint.memory import MemorySaver
from langchain_text_splitters import RecursiveCharacterTextSplitter

from assistent import create_app
from assistent.config import Settings
from assistent.services.bulk_queue import BulkUploadTask

ROOT=Path('/tmp/gosapteka-import')
OWNER='gosapteka56-public-import-2026-10-27'


def emit(row):
    print(json.dumps(row,ensure_ascii=False),flush=True)


async def main():
    settings=Settings()
    app=create_app(settings=settings,checkpointer=MemorySaver())
    pool=AsyncConnectionPool(make_conninfo(host=settings.postgres_host,port=settings.postgres_port,
        dbname=settings.postgres_db,user=settings.postgres_user,password=settings.postgres_password),
        open=False,kwargs={'row_factory':dict_row},min_size=1,max_size=5)
    await pool.open(wait=True)
    app.state.catalog_service.set_pool(pool)
    ingestion=app.state.ingestion_service
    prepared=json.loads((ROOT/'prepared.json').read_text(encoding='utf-8'))
    results=[]
    async def index(data,filename,title,text,metadata,mime):
        task=BulkUploadTask(uuid.uuid4().hex,filename,data,title[:255],OWNER,True,mime,[],[],
                           reviewed_content=text,metadata=metadata)
        key=ingestion.store.save(data,suffix=Path(filename).suffix.lower())
        identifier,chunks=await ingestion(task,key)
        return identifier,chunks
    async def publish(identifier,title,text):
        await ingestion.data.publish(identifier,title,text,OWNER)
        publication=await ingestion.data.publication(identifier)
        return {'document_id':identifier,'publication_id':publication['id'],'title':title}
    try:
        for row in prepared['documents']:
            result={'type':'import','number':row['number'],'title':row['title'],'source_url':row['url']}
            try:
                if row.get('needs_ocr') or row['replacement_characters']:
                    raise ValueError('Extraction requires review')
                path=ROOT/row['path']
                original=path.read_bytes()
                if hashlib.sha256(original).hexdigest()!=row['sha256']:
                    raise ValueError('Original hash mismatch')
                text=(ROOT/row['text_path']).read_text(encoding='utf-8')
                if hashlib.sha256(text.encode()).hexdigest()!=row['text_sha256']:
                    raise ValueError('Reviewed text hash mismatch')
                metadata={'source_url':row['url'],'source_page':prepared['source_page'],'import_batch':OWNER,
                    'extraction_engine':row['engine'],'pages':row.get('pages'),
                    'content_scope':row.get('content_scope','extracted_document_text'),
                    'extraction_review':row.get('ocr_review','readability and nonempty text checked')}
                mime=mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
                identifier,chunks=await index(original,path.name,row['title'],text,metadata,mime)
                publications=[]
                if len(text)<=50000:
                    publications.append(await publish(identifier,row['title'][:255],text))
                else:
                    parts=RecursiveCharacterTextSplitter(chunk_size=44000,chunk_overlap=300).split_text(text)
                    for number,part in enumerate(parts,1):
                        title=row['title'][:200]+f' — часть {number}/{len(parts)}'
                        content=f'# {title}\nИсточник: {row["url"]}\n\n'+part
                        part_id,_=await index(content.encode(),f'{row["number"]:02d}-part-{number}.md',title,content,
                            {**metadata,'parent_document_id':identifier,'part':number},'text/markdown')
                        publications.append(await publish(part_id,title,content))
                result.update(status='ready',document_id=identifier,sha256=row['sha256'],chunks=chunks,
                    bytes=len(original),characters=len(text),is_public=True,
                    publications=publications,content_scope=metadata['content_scope'],engine=row['engine'])
            except Exception as exc:
                result.update(status='error',error=type(exc).__name__+': '+str(exc)[:250])
            results.append(result)
            (ROOT/'import-result.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
            emit(result)
        async with httpx.AsyncClient(base_url='http://127.0.0.1:8000',timeout=90,
            headers={'X-Internal-Token':settings.internal_api_key}) as client:
            for row in results:
                if row['status']!='ready':
                    continue
                response=await client.get('/api/v1/documents/download/'+row['document_id'],params={'user_role':'employee'})
                emit({'type':'original_check','number':row['number'],'status':response.status_code,
                    'pass':response.status_code==200 and hashlib.sha256(response.content).hexdigest()==row['sha256']})
            for question in [
                'Как в ИС ЛО создать заявку для предоставления доступа сотруднику?',
                'Как найти электронный рецепт по СНИЛС или ИД социальной карты оренбуржца?',
                'Для чего в ИС ЛО нужен модуль Управление сотрудниками?',
            ]:
                response=await client.post('/api/v1/consultant/chat',headers={'X-Consultant-Key':settings.consultant_api_key},json={'message':question})
                emit({'type':'public_answer','question':question,'status':response.status_code,'body':response.json()})
        emit({'type':'summary','originals':len(results),'ready':sum(r['status']=='ready' for r in results),
              'publications':sum(len(r.get('publications',[])) for r in results)})
    finally:
        await pool.close()


if __name__=='__main__':
    asyncio.run(main())

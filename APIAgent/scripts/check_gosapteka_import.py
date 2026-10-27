"""Verify persisted public originals and real consultant replies after deployment."""
import asyncio
import hashlib
import json
from pathlib import Path
import psycopg
import httpx
from apiagent.config import Settings


async def main():
    settings=Settings()
    rows=json.loads(Path('/tmp/gosapteka-import-results.json').read_text(encoding='utf-8'))
    async with await psycopg.AsyncConnection.connect(host=settings.postgres_host,port=settings.postgres_port,
        dbname=settings.postgres_db,user=settings.postgres_user,password=settings.postgres_password) as conn:
        counts=await (await conn.execute('''SELECT count(*),count(*) FILTER (WHERE is_public),
            (SELECT count(*) FROM document_publications WHERE publisher_id=%s) FROM documents WHERE owner_id=%s''',
            ('gosapteka56-public-import-2026-10-27','gosapteka56-public-import-2026-10-27'))).fetchone()
    print(json.dumps({'type':'counts','imported_catalog_records':counts[0],'public_catalog_records':counts[1],'publications':counts[2]}),flush=True)
    passed=0
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8000',timeout=115,
        headers={'X-Internal-Token':settings.internal_api_key}) as client:
        for row in rows:
            response=await client.get('/api/v1/documents/download/'+row['document_id'],params={'user_role':'employee'})
            ok=response.status_code==200 and hashlib.sha256(response.content).hexdigest()==row['sha256']
            passed+=ok
            print(json.dumps({'type':'original','number':row['number'],'pass':ok}),flush=True)
        for question,required in [
            ('Для чего в ИС ЛО нужен модуль Управление сотрудниками?', ['сотрудник']),
            ('Как найти электронный рецепт по СНИЛС или ИД социальной карты оренбуржца?', ['СНИЛС']),
            ('Какие документы требуются для получения доступа к ИС ЛО?', ['заяв']),
        ]:
            response=await client.post('/api/v1/consultant/chat',headers={'X-Consultant-Key':settings.consultant_api_key},json={'message':question})
            body=response.json()
            print(json.dumps({'type':'public_answer','question':question,'status':response.status_code,'body':body,
                'basic_check':response.status_code==200 and bool(body.get('sources')) and all(word.casefold() in body.get('reply','').casefold() for word in required)},ensure_ascii=False),flush=True)
    print(json.dumps({'type':'summary','originals_verified_after_restart':passed,'expected_originals':40}),flush=True)
    assert passed==40 and counts[0]==45 and counts[1]==45 and counts[2]==43


if __name__=='__main__':
    asyncio.run(main())

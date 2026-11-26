"""Read-only retrieval inspection; --probe is a red-capable regression signal.

Runs in the Documents image. Never writes PostgreSQL/Qdrant or changes prompts.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import time
import hashlib
import math

import httpx


async def probe(output):
    query='процедура внесения остатков лекарств на начало работы в аптечном учёте медорганизации'
    async with httpx.AsyncClient(timeout=60,trust_env=False) as client:
        response=await client.post('http://documents:8000/api/v1/knowledge/search',
            headers={'X-Internal-Token':os.environ['DOCUMENTS_READ_KEY']},
            json={'query':query,'k':5,'user_context':{'role':'employee','departments':[]}})
        response.raise_for_status();context=response.json()['context']
    found='Документы ввода начальных остатков' in context
    result={'case':'opening_balances','query':query,'expected_instruction_fragment_found':found,
        'titles':re.findall(r'<context source="([^"]+)"',context),'returned_characters':len(context)}
    Path(output).mkdir(parents=True,exist_ok=True)
    (Path(output)/'probe.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False),flush=True)
    assert found,'Expected opening-balance procedure is missing from the top-5 retrieval context'


async def inspect(output,answers_file):
    from functools import partial
    import threading
    from langchain_core.embeddings import Embeddings
    from langchain_core.documents import Document
    from psycopg.conninfo import make_conninfo
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool
    from qdrant_client import QdrantClient,models
    from viio_documents.config import Settings
    from viio_rag.infrastructure.models.embeddings import create_embeddings
    from viio_documents.infrastructure.persistence.document_catalog import DocumentCatalogService
    from viio_rag.application.knowledge_data import KnowledgeData,query_terms
    from viio_rag.infrastructure.publication_index import PublicationIndex
    from viio_rag.application.retrieval import rank_documents
    from viio_rag.application.search_tool import build_access_filter,format_documents
    from viio_rag.infrastructure.vector_store import QdrantVectorStoreAdapter

    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    evidence=output/'contexts';evidence.mkdir(exist_ok=True)
    s=Settings(qdrant_url='http://qdrant:6333',qdrant_api_key=os.environ['QDRANT_READ_KEY'],
        postgres_host=os.getenv('POSTGRES_HOST','postgres'),postgres_port=int(os.getenv('POSTGRES_PORT','5432')),
        postgres_user=os.getenv('POSTGRES_USER','postgres'),postgres_password=os.getenv('POSTGRES_PASSWORD','postgres'))
    delegate=create_embeddings(s)
    class Cache(Embeddings):
        def __init__(self):self.cache={};self.lock=threading.Lock()
        def embed_query(self,query):
            with self.lock:
                if query not in self.cache:self.cache[query]=delegate.embed_query(query)
                return self.cache[query]
        def embed_documents(self,texts):raise RuntimeError('Read-only diagnostics cannot index documents')
    embeddings=Cache()
    client=QdrantClient(url=s.qdrant_url,api_key=s.qdrant_api_key)
    private=QdrantVectorStoreAdapter(client,embeddings,s.qdrant_collection_name)
    public=QdrantVectorStoreAdapter(client,embeddings,s.qdrant_public_collection_name)
    definitions={
        'opening_balances':('Ввод начальных остатков',{'balance_menu':r'Документы\s+ввода\s+начальных\s+остатков'}),
        'incoming_goods':('Ручной ввод документа Поступление товаров',{'receipt_menu':r'Поступления\s+товаров'}),
        'inventory':('Инвентаризации товаров в отделениях',{'inventory_menu':r'Инвентаризации\s+товаров\s+в\s+отделениях'}),
        'report_tree':('AvailableReports getReportUser',{'report_method':r'getReportUser'}),
        'recipe_multiple':('После выбора необходимого рецепта подбор медикамента',{'selection_step':r'После\s+выбора\s+необходимого\s+рецепта','medication_step':r'подбор\s+м\s*едикамента'}),
        'department_transfer':('Отпуск в отделение без требования отделения закупочная',{'purchase_price':r'Вид\s+цены:\s*Закупочная'}),
        'pharmacy_initial':('Подготовительный этап ФИФО основные сведения медицинской организации',{'fifo':r'ФИФО'}),
        'unknown_project':('Янтарь-98417',{'project_name':r'Янтарь-98417'}),
    }
    previous=[json.loads(line) for line in Path(answers_file).read_text(encoding='utf-8').splitlines()]
    cases={row['case']:row for row in previous if row.get('type')=='answer' and row['agent']=='main' and row['case'] in definitions}
    def anchors(text,patterns):return {name:bool(re.search(pattern,text,re.I|re.S)) for name,pattern in patterns.items()}
    def point_doc(point):return Document(page_content=point.payload.get('page_content',''),metadata=point.payload.get('metadata',{}))
    def point_id(doc):return doc.metadata.get('publication_id') or doc.metadata.get('document_id')
    def key(doc):return (point_id(doc),doc.metadata.get('chunk_index'),doc.page_content)
    def term_score(query,doc):
        terms=query_terms(query);text=doc.page_content.lower();title=str(doc.metadata.get('title','')).lower();filename=str(doc.metadata.get('filename','')).lower()
        return sum(3*(term in text)+5*(term in title or term in filename) for term in terms)
    async with AsyncConnectionPool(make_conninfo(host='postgres',port=5432,dbname=s.postgres_db,
        user=s.postgres_user,password=s.postgres_password),open=False,kwargs={'row_factory':dict_row}) as pool:
        data=KnowledgeData(DocumentCatalogService(pool),PublicationIndex(public))
        async with pool.connection() as conn:
            canonical=await(await conn.execute('SELECT d.id,d.title,c.content FROM documents d JOIN document_contents c ON c.document_id=d.id')).fetchall()
            publications=await(await conn.execute('SELECT id::text,document_id,title,content FROM document_publications')).fetchall()
        canonical={row['id']:row for row in canonical};pubmap={row['id']:row for row in publications}
        def describe(doc,patterns,scores=None):
            pub=doc.metadata.get('publication_id');identifier=doc.metadata.get('document_id')
            original=pubmap.get(pub) if pub else canonical.get(identifier)
            original=original or canonical.get(identifier) or {}
            body=doc.page_content
            matched=[match for pattern in patterns.values() for match in re.finditer(pattern,body,re.I|re.S)]
            start=max(0,matched[0].start()-100) if matched else 0
            source=re.search(r'Источник:\s*(https?://\S+)',original.get('content','')[:1800])
            result={'document_id':identifier,'publication_id':pub,'title':doc.metadata.get('title'),
                'chunk_index':doc.metadata.get('chunk_index'),'characters':len(body),'source_url':source[1] if source else None,
                'canonical_contains':anchors(original.get('content',''),patterns),'fragment_contains':anchors(body,patterns),
                'sample':body[start:start+650]}
            if scores and key(doc) in scores:result['vector_similarity']=round(scores[key(doc)],6)
            return result
        with (output/'results.jsonl').open('w',encoding='utf-8') as result_file:
            for case_id,(control,patterns) in definitions.items():
                case=cases[case_id]
                for variant,query in [('natural',case['prompt']),('agent_query',case['tools'][0]['query']),('control',control)]:
                    started=time.monotonic();context={'role':case['role'],'departments':[]}
                    vector=await asyncio.to_thread(embeddings.embed_query,query)
                    ready=models.Filter(must_not=[models.FieldCondition(key=field,match=models.MatchValue(value=False)) for field in ('metadata.ingestion_ready','metadata.is_active')])
                    scope=build_access_filter(context)
                    private_filter=models.Filter(must=[scope,ready]) if scope is not None else ready
                    async def points(collection,filter,limit,exact):
                        return (await asyncio.to_thread(partial(client.query_points,collection_name=collection,query=vector,
                            query_filter=filter,limit=limit,with_payload=True,search_params=models.SearchParams(exact=exact)))).points
                    pann,pex,pubann,pubex=await asyncio.gather(points(s.qdrant_collection_name,private_filter,15,False),
                        points(s.qdrant_collection_name,private_filter,15,True),points(s.qdrant_public_collection_name,ready,40,False),
                        points(s.qdrant_public_collection_name,ready,40,True))
                    raw=[point_doc(point) for point in pann+pubann]
                    scores={key(doc):point.score for doc,point in zip(raw,pann+pubann)}
                    private_docs,lexical,public_docs=await asyncio.gather(
                        asyncio.to_thread(partial(private.similarity_search,query,k=15,filter=scope)),
                        data.lexical(query,context),data.public_vectors(query,15))
                    merged=[];seen=set();origin={}
                    for branch,docs in [('lexical',lexical),('private',private_docs),('public',public_docs)]:
                        for doc in docs:
                            identity=(doc.metadata.get('document_id'),doc.page_content)
                            origin.setdefault(identity,[]).append(branch)
                            if identity not in seen:seen.add(identity);merged.append(doc)
                    authorized=await data.authorized_candidates(merged,context)
                    final=rank_documents(query,authorized,5)
                    formatted=format_documents(final)
                    (evidence/(case_id+'-'+variant+'-main-tool.txt')).write_text(formatted,encoding='utf-8')
                    selected_public=rank_documents(query,await data.public_vectors(query,40),40)
                    pub_ids=[]
                    for doc in selected_public:
                        identifier=doc.metadata['publication_id']
                        if identifier not in pub_ids and len(pub_ids)<4:pub_ids.append(identifier)
                    gateway=[pubmap[identifier] for identifier in pub_ids if identifier in pubmap]
                    (evidence/(case_id+'-'+variant+'-public-snapshots.json')).write_text(json.dumps({'query':query,'patterns':patterns,'items':gateway},ensure_ascii=False),encoding='utf-8')
                    selected=[]
                    for position,doc in enumerate(final,1):
                        item=describe(doc,patterns,scores)
                        item.update(rank=position,final_term_score=term_score(query,doc),branches=origin[(doc.metadata.get('document_id'),doc.page_content)])
                        selected.append(item)
                    result={'case':case_id,'variant':variant,'query':query,'role':case['role'],
                        'settings':{'embedding_model':s.lmstudio_embedding_model,'chunk_unit':s.rag_chunk_unit,'chunk_size':s.rag_chunk_size},
                        'ann_exact_overlap':{'private':len({p.id for p in pann}&{p.id for p in pex})/max(1,len(pex)),
                            'public':len({p.id for p in pubann}&{p.id for p in pubex})/max(1,len(pubex))},
                        'branches':{'private': [describe(doc,patterns,scores) for doc in private_docs],
                            'lexical':[describe(doc,patterns) for doc in lexical],
                            'public':[describe(doc,patterns,scores) for doc in public_docs]},
                        'main_top5':selected,'main_tool_characters':len(formatted),
                        'main_tool_contains':anchors(formatted,patterns),
                        'consultant_gateway_top4':[{'publication_id':row['id'],'title':row['title'],'characters':len(row['content']),
                            'contains':anchors(row['content'],patterns)} for row in gateway],
                        'seconds':round(time.monotonic()-started,3)}
                    result_file.write(json.dumps(result,ensure_ascii=False)+'\n');result_file.flush()
                    print(json.dumps({'case':case_id,'variant':variant,'contains':result['main_tool_contains'],
                        'top_titles':[item['title'] for item in selected]},ensure_ascii=False),flush=True)
        samples=[]
        for collection in [s.qdrant_collection_name,s.qdrant_public_collection_name]:
            points,_=client.scroll(collection,limit=2,with_payload=True,with_vectors=True)
            texts=[point.payload['page_content'] for point in points]
            regenerated=await asyncio.to_thread(delegate.embed_documents,texts)
            for point,new in zip(points,regenerated):
                old=point.vector
                if isinstance(old,dict):old=next(iter(old.values()))
                cosine=sum(a*b for a,b in zip(old,new))/(math.sqrt(sum(a*a for a in old))*math.sqrt(sum(b*b for b in new)))
                samples.append({'collection':collection,'point_id':str(point.id),'characters':len(point.payload['page_content']),
                    'dimensions':len(old),'recomputed_dimensions':len(new),'stored_vs_current_cosine':round(cosine,8)})
        (output/'embedding-check.json').write_text(json.dumps(samples,ensure_ascii=False,indent=2),encoding='utf-8')
    client.close()


async def deep_index(output):
    from qdrant_client import QdrantClient,models
    from viio_documents.config import Settings
    from viio_rag.infrastructure.models.embeddings import create_embeddings
    from viio_rag.application.search_tool import build_access_filter
    from functools import partial
    output=Path(output)
    rows=[json.loads(line) for line in (output/'results.jsonl').read_text(encoding='utf-8').splitlines()]
    s=Settings(qdrant_url='http://qdrant:6333',qdrant_api_key=os.environ['QDRANT_READ_KEY'])
    embeddings=create_embeddings(s)
    client=QdrantClient(url=s.qdrant_url,api_key=s.qdrant_api_key)
    results=[]
    for row in rows:
        if row['variant']!='agent_query' or row['case'] not in {'opening_balances','incoming_goods','report_tree','recipe_multiple','inventory','department_transfer'}:continue
        snapshot=json.loads((output/'contexts'/(row['case']+'-'+row['variant']+'-public-snapshots.json')).read_text(encoding='utf-8'))
        vector=await asyncio.to_thread(embeddings.embed_query,row['query'])
        ready=models.Filter(must_not=[models.FieldCondition(key=field,match=models.MatchValue(value=False)) for field in ('metadata.ingestion_ready','metadata.is_active')])
        access=build_access_filter({'role':row['role'],'departments':[]})
        scope=models.Filter(must=[access,ready]) if access is not None else ready
        item={'case':row['case'],'query':row['query'],'collections':{}}
        for name,collection,filter in [('private',s.qdrant_collection_name,scope),('public',s.qdrant_public_collection_name,ready)]:
            count=client.count(collection,exact=True,count_filter=filter).count
            points=(await asyncio.to_thread(partial(client.query_points,collection_name=collection,query=vector,
                query_filter=filter,limit=min(2048,count),with_payload=True,search_params=models.SearchParams(exact=True)))).points
            matches=[]
            for position,point in enumerate(points,1):
                text=point.payload.get('page_content','');meta=point.payload.get('metadata',{})
                hits={key:bool(re.search(pattern,text,re.I|re.S)) for key,pattern in snapshot['patterns'].items()}
                if any(hits.values()):matches.append({'rank':position,'title':meta.get('title'),'document_id':meta.get('source_document_id') or meta.get('document_id'),
                    'chunk_index':meta.get('chunk_index'),'score':round(point.score,6),'contains':hits})
            item['collections'][name]={'visible_point_count':count,'scanned_points':len(points),'first_matching_points':matches[:6]}
        results.append(item)
    (output/'deep-index.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'deep_index_queries':len(results)},ensure_ascii=False))
    client.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default='/output')
    parser.add_argument('--probe',action='store_true')
    parser.add_argument('--answers',default='/checks/answers.jsonl')
    parser.add_argument('--deep',action='store_true')
    args=parser.parse_args()
    asyncio.run(probe(args.output) if args.probe else deep_index(args.output) if args.deep else inspect(args.output,args.answers))

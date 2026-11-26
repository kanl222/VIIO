"""Focused follow-up probes for the live evaluation."""
import asyncio,hashlib,json,time,uuid
import httpx
from assistent.config import Settings
from assistent.infrastructure.persistence.postgres_saver import init_async_postgres_saver
from evaluate_live_20261027 import python_check

async def main():
 s=Settings(); run="eval-follow-"+uuid.uuid4().hex[:10]; threads=set()
 saver,pool=await init_async_postgres_saver(f"postgresql://{s.postgres_user}:{s.postgres_password}@{s.postgres_host}:{s.postgres_port}/{s.postgres_db}")
 cases=[
  ("arithmetic_repeat","Вычисли 17 * 23. Дай только число.","admin",None),
  ("rag_documents_exact",'По документу «Типы документов в ИС \"ЛО\"» перечисли названия типов DocumentObject. Не пиши код.',"admin",None),
  ("rag_dispense","Где и когда обслужен рецепт: какая таблица и какое поле хранят дату отпуска? Используй документ TestDataBase.Отпуск. Где и когда обслужен рецепт. Код не нужен.","admin",None),
  ("programmer_coder","/code Напиши только функцию add(a,b) на Python, возвращающую сумму.","programmer",None),
  ("manager_knowledge","По документу «Типы полей для отчетов SSRS» какие значения flag_sp у ВЗН, ОНЛП и 1191? Без кода.","manager",None),
  ("memory_set","Запомни для этой беседы: мой тестовый проект называется Лимон. Ответь только «понял».","employee","memory"),
  ("memory_recall","Как называется мой тестовый проект?","employee","memory"),
 ]
 async with httpx.AsyncClient(timeout=115) as client:
  try:
   for name,prompt,role,thread in cases:
    tid=run+"-"+(thread or name);threads.add(tid);uid=run+"-"+role
    ctx={"user_id":uid,"role":role,"departments":[]};start=time.monotonic()
    try:
     r=await client.post('http://127.0.0.1:8000/api/v1/chat',headers={'X-Internal-Token':s.internal_api_key},json={'message':prompt,'thread_id':tid,'user_context':ctx}); body=r.json()
     ids={'general':tid,'coder':'coder-text-'+hashlib.sha256(json.dumps([tid,uid],ensure_ascii=False).encode()).hexdigest()};state={}
     for label,cid in ids.items():
      cp=await saver.aget_tuple({'configurable':{'thread_id':cid}});messages=cp.checkpoint['channel_values']['messages'] if cp else []
      state[label]={'exists':bool(cp),'tool_calls':sum(len(getattr(m,'tool_calls',[])) for m in messages)}
     result={'type':'answer','id':name,'prompt':prompt,'role':role,'status':r.status_code,'seconds':round(time.monotonic()-start,2),'reply':body.get('reply',body.get('detail','')),'sources':body.get('sources',[]),'state':state}
     if name=='programmer_coder':result['validation']=python_check(result['reply'],'add',[([2,3],5),([-2,2],0)])
    except Exception as e:result={'type':'answer','id':name,'error':type(e).__name__,'seconds':round(time.monotonic()-start,2)}
    print(json.dumps(result,ensure_ascii=False),flush=True)
  finally:
   for tid in threads:
    await saver.adelete_thread(tid)
    for role in ['admin','employee','manager','programmer']:
     suffix=hashlib.sha256(json.dumps([tid,run+'-'+role],ensure_ascii=False).encode()).hexdigest()
     await saver.adelete_thread('coder-text-'+suffix)
     await saver.adelete_thread('route-text-'+suffix)
   await pool.close();print(json.dumps({'type':'cleanup','followup_histories_removed':True}),flush=True)
asyncio.run(main())

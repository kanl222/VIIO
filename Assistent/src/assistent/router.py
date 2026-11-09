from hmac import compare_digest
from assistent.services.diagnostics import capture, record
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, ConfigDict, StrictBool
import uuid
from typing import Annotated, Literal
import asyncio
import json
from contextlib import suppress
from fastapi.responses import StreamingResponse
from assistent.services.documents_client import access_revision
class UserContext(BaseModel):
    model_config=ConfigDict(extra='forbid')
    user_id:str|None=None
    role:str='employee'
    departments:list[str]=Field(default_factory=lambda:['general'])
class ChatRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    message:str=Field(min_length=1,max_length=24000)
    thread_id:str|None=None
    user_context:UserContext|None=None
    confirmed_public_for_external_cloud: StrictBool = False
    rag_backend: Literal['local', 'yandex'] | None = None
class ChatSource(BaseModel):
    title:str
    document_id:str
class ChatResponse(BaseModel):
    reply:str
    thread_id:str
    sources:list[ChatSource]=Field(default_factory=list)
def create_api_router(agent_service, settings, consultant_service=None):
    router = APIRouter()
    TokenHeader = Annotated[str | None, Header(alias='X-Internal-Token')]

    def authorize(x_internal_token: TokenHeader = None) -> None:
        expected = settings.chat_api_key or (settings.internal_api_key if not settings.secure_runtime else None)
        if not expected:
            raise HTTPException(503, 'Ключ чата не настроен')
        accepted = [expected] + ([settings.chat_api_key_previous] if settings.chat_api_key_previous else [])
        if not x_internal_token or not any(compare_digest(key.encode(), x_internal_token.encode()) for key in accepted):
            raise HTTPException(401, 'Необходим ключ чата')

    RequireAuth = Annotated[None, Depends(authorize)]
    @router.get('/health')
    async def health():
        provider = settings.chat_provider or settings.llm_provider
        model = settings.chat_model or (settings.yandex_model if provider == 'yandex' else
                settings.ollama_model if provider == 'ollama' else settings.lmstudio_model)
        return {'status':'healthy','service':'assistent','provider':provider,'model':model}
    @router.get('/api/v1/usage', dependencies=[Depends(authorize)])
    async def usage():
        from assistent.services.token_usage import token_usage
        return token_usage.snapshot()
    @router.post('/api/v1/chat',dependencies=[Depends(authorize)],response_model=ChatResponse)
    async def chat(request:ChatRequest):
        if not request.message.strip():raise HTTPException(400,'Пустое сообщение')
        if (request.rag_backend or settings.assistant_rag_backend) == 'yandex':
            from assistent.services.managed_rag import answer
            result = await answer(settings, request.message, request.confirmed_public_for_external_cloud)
            return ChatResponse(reply=result['reply'], thread_id=request.thread_id or str(uuid.uuid4()),
                                sources=result['sources'])
        with capture('main', request.message, request.thread_id):
            context=request.user_context.model_dump() if request.user_context else {}
            revision=await access_revision(settings) if settings.secure_runtime else None
            if revision:context['_knowledge_epoch']=revision
            try:
                reply,thread=await agent_service.chat(request.message,request.thread_id,context or None)
            except ValueError as error:raise HTTPException(400,str(error)) from None
            except Exception as error:
                record('error', error=type(error).__name__, detail='Вызов агента завершился ошибкой')
                raise HTTPException(503,'Сервис агента временно недоступен') from None
            if revision and revision!=await access_revision(settings):
                record('guard', name='access_revision', status='blocked')
                saved = getattr(reply, 'saved_documents', [])
                if saved:
                    acknowledgement = 'Сохранено в общую базу знаний:\n' + '\n'.join(
                        f'«{item["title"]}» — {item["chunks_count"]} фрагментов.' for item in saved)
                    record('answer', reply=acknowledgement, sources=[])
                    return ChatResponse(reply=acknowledgement, thread_id=thread)
                record('answer', reply='Доступ к материалам изменился. Повторите вопрос для проверки актуальных источников.', sources=[])
                return ChatResponse(reply='Доступ к материалам изменился. Повторите вопрос для проверки актуальных источников.',thread_id=thread)
            if revision:record('guard', name='access_revision', status='ok')
            record('answer', reply=str(reply), sources=getattr(reply,'sources',[]), thread_id=thread)
            return ChatResponse(reply=str(reply),thread_id=thread,sources=getattr(reply,'sources',[]))
    @router.post('/api/v1/chat/stream', dependencies=[Depends(authorize)])
    async def stream_chat(request: ChatRequest):
        if not request.message.strip():
            raise HTTPException(400, 'Пустое сообщение')
        initial_revision = await access_revision(settings) if settings.secure_runtime else None

        async def events():
            from assistent.services.streaming import event_sink
            queue = asyncio.Queue(maxsize=64)

            async def produce():
                token = event_sink.set(queue.put)
                try:
                    async with asyncio.timeout(300):
                        result = await chat(request)
                    await queue.put({'type': 'done', **result.model_dump()})
                except HTTPException as error:
                    await queue.put({'type': 'error', 'detail': error.detail})
                except TimeoutError:
                    await queue.put({'type': 'error', 'detail': 'Превышено время выполнения задачи.'})
                except Exception:
                    await queue.put({'type': 'error', 'detail': 'Поток ответа прерван. Повторите запрос.'})
                finally:
                    event_sink.reset(token)

            task = asyncio.create_task(produce())
            blocked = False
            try:
                yield 'data: '+json.dumps({'type':'status','message':'Обрабатываем запрос'},ensure_ascii=False)+'\n\n'
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield ': keepalive\n\n'
                        continue
                    if event['type'] in {'delta', 'plan'}:
                        if not blocked and initial_revision:
                            try:
                                blocked = initial_revision != await access_revision(settings)
                            except HTTPException:
                                blocked = True
                            if blocked:
                                yield 'data: {"type":"reset"}\n\n'
                        if blocked:
                            continue
                    yield 'data: '+json.dumps(event,ensure_ascii=False)+'\n\n'
                    if event['type'] in {'done', 'error'}:
                        break
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

        return StreamingResponse(events(), media_type='text/event-stream',
            headers={'Cache-Control':'no-cache, no-transform','X-Accel-Buffering':'no'})
    return router

from hmac import compare_digest
from assistent.observability.diagnostics import capture, record
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, ConfigDict, StrictBool
import uuid
from typing import Annotated, Literal
import asyncio
import json
from contextlib import suppress
from fastapi.responses import StreamingResponse
from assistent.infrastructure.clients.documents_client import access_revision
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
    agent_mode: Literal['assistant', 'coding', 'database'] | None = None
class ChatSource(BaseModel):
    title:str
    document_id:str
class ChatResponse(BaseModel):
    reply:str
    thread_id:str
    sources:list[ChatSource]=Field(default_factory=list)
def create_api_router(agent_service, settings, consultant_service=None):
    router = APIRouter()
    generation_slots = asyncio.Semaphore(settings.generation_concurrency)
    TokenHeader = Annotated[str | None, Header(alias='X-Internal-Token')]

    def authorize(x_internal_token: TokenHeader = None) -> None:
        expected = settings.chat_api_key or (settings.internal_api_key if not settings.secure_runtime else None)
        if not expected:
            raise HTTPException(503, 'Ключ чата не настроен')
        accepted = [expected] + ([settings.chat_api_key_previous] if settings.chat_api_key_previous else [])
        if not x_internal_token or not any(compare_digest(key.encode(), x_internal_token.encode()) for key in accepted):
            raise HTTPException(401, 'Необходим ключ чата')

    @router.get('/health')
    async def health():
        provider = settings.chat_provider or settings.llm_provider
        from assistent.infrastructure.llm.chat import resolve_yandex_model
        if provider == 'yandex':
            model = resolve_yandex_model(settings.chat_model, settings.yandex_model)
        elif provider == 'ollama':
            model = settings.chat_model or settings.ollama_model
        else:
            model = settings.chat_model or settings.lmstudio_model
        return {'status':'healthy','service':'assistent','provider':provider,'model':model}
    @router.get('/api/v1/usage', dependencies=[Depends(authorize)])
    async def usage():
        from assistent.observability.token_usage import token_usage
        return token_usage.snapshot()
    async def execute_chat(request:ChatRequest):
        if not request.message.strip():raise HTTPException(400,'Пустое сообщение')
        if request.agent_mode in {'coding', 'database'}:
            role = request.user_context.role if request.user_context else 'employee'
            allowed = {'programmer', 'admin'}
            if request.agent_mode == 'coding':
                allowed &= set(settings.coding_allowed_roles)
            if role not in allowed:
                raise HTTPException(403, 'Режим недоступен для вашей роли')
            if request.agent_mode == 'database' and not settings.pgsql_enabled:
                raise HTTPException(503, 'Режим базы данных выключен')
            if request.agent_mode == 'coding' and not settings.coding_agent_enabled:
                raise HTTPException(503, 'Режим кодинга выключен')
        user_id = request.user_context.user_id if request.user_context else None
        from assistent.observability.telemetry import record_telemetry_turn_start, record_telemetry_turn_end
        record_telemetry_turn_start(request.message, request.thread_id, user_id)
        if request.agent_mode not in {'coding', 'database'} and (request.rag_backend or settings.assistant_rag_backend) == 'yandex':
            from assistent.infrastructure.clients.managed_rag import answer
            result = await answer(settings, request.message, request.confirmed_public_for_external_cloud)
            th = request.thread_id or str(uuid.uuid4())
            record_telemetry_turn_end(result['reply'], th)
            return ChatResponse(reply=result['reply'], thread_id=th,
                                sources=result['sources'])
        with capture('main', request.message, request.thread_id):
            context=request.user_context.model_dump() if request.user_context else {}
            if request.agent_mode:
                context['_agent_mode'] = request.agent_mode
            revision=await access_revision(settings) if settings.secure_runtime else None
            if revision:context['_knowledge_epoch']=revision
            try:
                reply,thread=await agent_service.chat(request.message,request.thread_id,context or None)
            except ValueError as error:raise HTTPException(400,str(error)) from None
            except Exception as error:
                record('error', error=type(error).__name__, detail='Вызов агента завершился ошибкой')
                if str(error).startswith('Модель '):
                    raise HTTPException(503, 'Модель не сформировала ответ. Повторите запрос или уменьшите контекст.') from None
                raise HTTPException(503,'Сервис агента временно недоступен') from None
            if revision and revision!=await access_revision(settings):
                record('guard', name='access_revision', status='blocked')
                saved = getattr(reply, 'saved_documents', [])
                if saved:
                    acknowledgement = 'Сохранено в общую базу знаний:\n' + '\n'.join(
                        f'«{item["title"]}» — {item["chunks_count"]} фрагментов.' for item in saved)
                    record('answer', reply=acknowledgement, sources=[])
                    record_telemetry_turn_end(acknowledgement, thread)
                    return ChatResponse(reply=acknowledgement, thread_id=thread)
                record('answer', reply='Доступ к материалам изменился. Повторите вопрос для проверки актуальных источников.', sources=[])
                record_telemetry_turn_end('Доступ к материалам изменился. Повторите вопрос для проверки актуальных источников.', thread)
                return ChatResponse(reply='Доступ к материалам изменился. Повторите вопрос для проверки актуальных источников.',thread_id=thread)
            if revision:record('guard', name='access_revision', status='ok')
            record('answer', reply=str(reply), sources=getattr(reply,'sources',[]), thread_id=thread)
            record_telemetry_turn_end(str(reply), thread)
            return ChatResponse(reply=str(reply),thread_id=thread,sources=getattr(reply,'sources',[]))
    @router.post('/api/v1/chat', dependencies=[Depends(authorize)], response_model=ChatResponse)
    async def chat(request: ChatRequest):
        from assistent.application.streaming import event_sink
        if sink := event_sink.get():
            await sink({'type': 'status', 'message': 'Ожидаем освобождения модели…' if generation_slots.locked() else 'Готовим запрос…'})
        try:
            async with asyncio.timeout(settings.request_timeout_seconds):
                async with generation_slots:
                    return await execute_chat(request)
        except TimeoutError:
            raise HTTPException(504, 'Модель не успела ответить. Уменьшите вопрос или контекст и повторите запрос.') from None

    @router.post('/api/v1/chat/stream', dependencies=[Depends(authorize)])
    async def stream_chat(request: ChatRequest):
        if not request.message.strip():
            raise HTTPException(400, 'Пустое сообщение')
        initial_revision = await access_revision(settings) if settings.secure_runtime else None

        async def events():
            from assistent.application.streaming import event_sink
            queue = asyncio.Queue(maxsize=64)

            async def produce():
                token = event_sink.set(queue.put)
                try:
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

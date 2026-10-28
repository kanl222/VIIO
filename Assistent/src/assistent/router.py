from hmac import compare_digest
from assistent.services.diagnostics import capture, record
from fastapi import APIRouter,Depends,Header,HTTPException
from pydantic import BaseModel,Field,ConfigDict
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
class ChatSource(BaseModel):
    title:str
    document_id:str
class ChatResponse(BaseModel):
    reply:str
    thread_id:str
    sources:list[ChatSource]=Field(default_factory=list)
def create_api_router(agent_service,settings,consultant_service=None):
    router=APIRouter()
    def authorize(x_internal_token:str|None=Header(default=None,alias='X-Internal-Token')):
        expected=settings.chat_api_key or (settings.internal_api_key if not settings.secure_runtime else None)
        if not expected:raise HTTPException(503,'Ключ чата не настроен')
        accepted=[expected]+([settings.chat_api_key_previous] if settings.chat_api_key_previous else [])
        if not x_internal_token or not any(compare_digest(key.encode(),x_internal_token.encode()) for key in accepted):
            raise HTTPException(401,'Необходим ключ чата')
    @router.get('/health')
    async def health():return {'status':'healthy','service':'assistent'}
    @router.post('/api/v1/chat',dependencies=[Depends(authorize)],response_model=ChatResponse)
    async def chat(request:ChatRequest):
        if not request.message.strip():raise HTTPException(400,'Пустое сообщение')
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
                record('answer', reply='Доступ к материалам изменился. Повторите вопрос для проверки актуальных источников.', sources=[])
                return ChatResponse(reply='Доступ к материалам изменился. Повторите вопрос для проверки актуальных источников.',thread_id=thread)
            if revision:record('guard', name='access_revision', status='ok')
            record('answer', reply=str(reply), sources=getattr(reply,'sources',[]), thread_id=thread)
            return ChatResponse(reply=str(reply),thread_id=thread,sources=getattr(reply,'sources',[]))
    return router

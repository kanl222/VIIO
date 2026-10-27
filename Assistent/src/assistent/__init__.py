"""Assistent composes agent models and uses the independent Documents HTTP API."""
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from fastapi import FastAPI
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from langgraph.checkpoint.memory import MemorySaver
from assistent.config import Settings
from assistent.models import create_chat_model,create_coding_model
from assistent.router import create_api_router
from assistent.services.documents_client import create_remote_search_tool
from assistent.services.agent_service import AgentService
from assistent.services.database_agent import DatabaseAgentService
from assistent.services.personal_memory import PersonalFacts,MemoryAgent
from assistent.services.public_consultant import ConsultantProxy
from assistent.services.coding_agent import CODING_SYSTEM_PROMPT,DELEGATION_PROMPT,CodingAgentService,MultiAgentService,create_coding_delegation_tool
from assistent.services.graph_builder import DEFAULT_SYSTEM_PROMPT,build_agent_graph
from assistent.tools.postgres_tool import create_postgres_readonly_tool
from assistent.utils.postgres_saver import init_async_postgres_saver
logger=logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app):
    s=app.state.settings
    conn=make_conninfo(host=s.postgres_host,port=s.postgres_port,dbname=s.postgres_db,user=s.postgres_user,password=s.postgres_password)
    pool=None
    if app.state.enable_postgres_checkpointer:
        saver,pool=await init_async_postgres_saver(conn,run_migrations=s.database_migrations_enabled)
        app.state.agent_service.set_checkpointer(saver)
        app.state.pool=pool
    if app.state.catalog_service is None:
        try:yield
        finally:
            if pool:await pool.close()
        return
    async with AsyncConnectionPool(conn,open=False,kwargs={'row_factory':dict_row}) as facts:
        app.state.catalog_service.pool=facts
        if s.database_migrations_enabled:await PersonalFacts(app.state.catalog_service).setup()
        try:yield
        finally:
            app.state.catalog_service.pool=None
            if pool:await pool.close()

def create_app(settings=None,agent_service=None,checkpointer=None):
    app_settings=settings or Settings()
    app_settings.validate_runtime()
    if not app_settings.documents_url:raise ValueError('Для Assistent нужен DOCUMENTS_URL')
    catalog_service=SimpleNamespace(pool=None)
    coding_service=None
    consultant=None
    if agent_service is None:
        llm = create_chat_model(app_settings)

        from assistent.services.documents_client import create_remote_search_tool
        search_tool = create_remote_search_tool(app_settings, 5)
        # Изначально используем переданный чекпоинтер или MemorySaver.
        # Если база PostgreSQL доступна, AsyncPostgresSaver подключится в lifespan.
        initial_checkpointer = checkpointer or MemorySaver()

        active_tools = [search_tool]
        coding_service = None
        if app_settings.coding_agent_enabled:
            coding_llm = create_coding_model(app_settings)
            coding_graph = build_agent_graph(
                llm=coding_llm,
                tools=[],
                checkpointer=initial_checkpointer,
                system_prompt=CODING_SYSTEM_PROMPT,
                summary_after=app_settings.conversation_summary_after if app_settings.conversation_summary_enabled else 0,
                history_max_chars=app_settings.conversation_history_max_chars,
            )
            coding_service = CodingAgentService(
                coding_graph, app_settings.coding_allowed_roles,
                knowledge_search=create_remote_search_tool(app_settings, 3),
                context_max_chars=app_settings.coding_context_max_chars,
            )
            active_tools.append(create_coding_delegation_tool(coding_service))

        graph = build_agent_graph(
            llm=llm,
            tools=active_tools,
            checkpointer=initial_checkpointer,
            system_prompt=DEFAULT_SYSTEM_PROMPT + (DELEGATION_PROMPT if coding_service else ""),
            context_max_chars=app_settings.rag_context_max_chars,
            model_first_tools=True,
            summary_after=app_settings.conversation_summary_after if app_settings.conversation_summary_enabled else 0,
            history_max_chars=app_settings.conversation_history_max_chars,
        )
        agent_service = AgentService(graph=graph)
        if coding_service:
            agent_service = MultiAgentService(agent_service, coding_service, model_first=True)
        agent_service = DatabaseAgentService(
            agent_service, llm, create_postgres_readonly_tool(app_settings),
            enabled=app_settings.pgsql_enabled,
        )
        if catalog_service:
            agent_service = MemoryAgent(agent_service, PersonalFacts(catalog_service))
        if app_settings.consultant_enabled:
            if not app_settings.consultant_api_key or len(app_settings.consultant_api_key) < 32 or app_settings.consultant_api_key == app_settings.internal_api_key:
                raise ValueError('Для внешнего консультанта нужны каталог и отдельный CONSULTANT_API_KEY длиной от 32 символов.')
            consultant = ConsultantProxy(app_settings.consultant_url, app_settings.consultant_api_key)
    app=FastAPI(title='Assistent',lifespan=lifespan)
    app.state.settings=app_settings
    app.state.agent_service=agent_service
    app.state.catalog_service=catalog_service
    app.state.enable_postgres_checkpointer=checkpointer is None
    app.state.pool=None
    app.include_router(create_api_router(agent_service,app_settings,consultant))
    return app

def main():
    from assistent.runner import run_app
    run_app()

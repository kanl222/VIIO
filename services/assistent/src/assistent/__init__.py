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
from assistent.tool_config import GitHubToolConfig, PostgresToolConfig
from assistent.infrastructure.llm import create_chat_model
from assistent.api.router import create_api_router
from assistent.infrastructure.clients.documents_client import create_remote_search_tool
from assistent.application.agent_service import AgentService
from assistent.application.memory.personal_memory import PersonalFacts,MemoryAgent
from assistent.application.memory.project_memory import ProjectMemory
from assistent.application.graph_builder import build_agent_graph
from assistent.prompts import UNIFIED_SYSTEM_PROMPT
from assistent.infrastructure.tools.github_tool import create_github_tool
from assistent.infrastructure.tools.postgres_tool import create_postgres_readonly_tool
from assistent.infrastructure.tools.knowledge_write import create_knowledge_write_tool
from assistent.infrastructure.tools.planning import manage_plan
from assistent.infrastructure.persistence.postgres_saver import init_async_postgres_saver
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
    async with AsyncConnectionPool(conn,open=False,kwargs={'row_factory':dict_row,'autocommit':True}) as facts:
        app.state.catalog_service.pool=facts
        if s.database_migrations_enabled:await PersonalFacts(app.state.catalog_service).setup()
        try:yield
        finally:
            app.state.catalog_service.pool=None
            if pool:await pool.close()

def create_app(settings=None,agent_service=None,checkpointer=None):
    app_settings=settings or Settings()
    if app_settings.cpu_profile:
        if (app_settings.chat_provider or app_settings.llm_provider) == 'yandex':
            raise ValueError('CPU_PROFILE requires a local chat provider')
        app_settings = app_settings.model_copy(update={
            'chat_max_tokens': min(app_settings.chat_max_tokens, 384),
            'chat_temperature': 0.0,
            'coding_max_tokens': min(app_settings.coding_max_tokens, 512),
            'rag_context_max_chars': min(app_settings.rag_context_max_chars, 3000),
            'conversation_summary_enabled': False,
            'conversation_history_max_chars': 4000,
            'corrective_rag_enabled': False,
            'model_first_tools': False,
            'chat_timeout_seconds': 180,
        })
    app_settings.validate_runtime()
    if not app_settings.documents_url:raise ValueError('Для Assistent нужен DOCUMENTS_URL')

    import os
    if app_settings.phoenix_enabled or os.getenv("PHOENIX_ENABLED", "").lower() in ("true", "1"):
        from assistent.observability.telemetry import init_phoenix_tracing
        init_phoenix_tracing(
            endpoint=app_settings.phoenix_endpoint,
            project_name=app_settings.phoenix_project_name,
            enabled=True,
        )

    catalog_service=SimpleNamespace(pool=None)
    consultant=None
    if agent_service is None:
        llm = create_chat_model(app_settings)
        routing_llm = (create_chat_model(app_settings, agent='routing')
                       if not app_settings.cpu_profile and (app_settings.routing_model or '').strip() else None)

        from assistent.infrastructure.clients.documents_client import create_remote_search_tool, create_document_list_tool
        search_tool = create_remote_search_tool(app_settings, 6 if app_settings.cpu_profile else 5, tool_name='search_knowledge_base')
        # Изначально используем переданный чекпоинтер или MemorySaver.
        # Если база PostgreSQL доступна, AsyncPostgresSaver подключится в lifespan.
        initial_checkpointer = checkpointer or MemorySaver()

        active_tools = [search_tool, create_document_list_tool(app_settings), manage_plan]
        if app_settings.skills_enabled:
            from assistent.infrastructure.tools.skills_tool import create_skills_tools
            active_tools.extend(create_skills_tools(skills_dir=app_settings.skills_dir))
        if app_settings.documents_admin_key:
            active_tools.append(create_knowledge_write_tool(app_settings))
        if app_settings.github_mcp_enabled:
            tool = create_github_tool(GitHubToolConfig.from_settings(app_settings))
            if tool is not None:
                active_tools.append(tool)
        if app_settings.pgsql_enabled:
            active_tools.append(create_postgres_readonly_tool(PostgresToolConfig.from_settings(app_settings)))
        if app_settings.memgraph_enabled:
            from assistent.infrastructure.tools.graph_tool import create_graph_search_tool
            graph_tool = create_graph_search_tool(app_settings)
            if graph_tool is not None:
                active_tools.append(graph_tool)

        graph = build_agent_graph(
            llm=llm,
            tools=active_tools,
            checkpointer=initial_checkpointer,
            system_prompt=UNIFIED_SYSTEM_PROMPT,
            task_routing=False,
            max_tool_calls=app_settings.agent_max_tool_calls,
            context_max_chars=app_settings.rag_context_max_chars,
            model_first_tools=app_settings.model_first_tools,
            summary_after=app_settings.conversation_summary_after if app_settings.conversation_summary_enabled else 0,
            history_max_chars=app_settings.conversation_history_max_chars,
            supervisor=True,
            coding_roles=app_settings.coding_allowed_roles,
            routing_llm=routing_llm,
            corrective_rag=app_settings.corrective_rag_enabled,
            cpu_profile=app_settings.cpu_profile,
            database_analyst=app_settings.text_to_sql_enabled,
            database_diagnostics=app_settings.text_to_sql_enabled,
        )
        agent_service = AgentService(graph=graph)
        if catalog_service:
            agent_service = MemoryAgent(agent_service, PersonalFacts(catalog_service), ProjectMemory(app_settings))

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

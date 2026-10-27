"""Real-model tool coverage on existing KB and disposable real PostgreSQL/MCP.

Run through run_all_tools_evaluation.ps1. No production configuration changes.
Main app factory + in-memory checkpoints; existing knowledge read, own facts cleaned.
Each record distinguishes LLM-selected calls from application fallbacks and backend IO.
"""
import ast
import asyncio
import hashlib
import json
import re
import sys
import time
import uuid
from pathlib import Path

import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

import apiagent
from apiagent.config import Settings
from apiagent.services.database_agent import DatabaseAgentService
from apiagent.services.graph_builder import build_agent_graph, scope_key
from apiagent.services.agent_service import AgentService
from apiagent.tools.postgres_tool import PostgresMCPClient
from apiagent.prompts import DEFAULT_SYSTEM_PROMPT, CODING_SYSTEM_PROMPT, DELEGATION_PROMPT


def emit(row):
    print(json.dumps(row, ensure_ascii=False), flush=True)


class Trace(BaseCallbackHandler):
    def __init__(self):
        self.calls = []
        self.generations = []

    def on_llm_end(self, response, **kwargs):
        for generations in response.generations:
            for generation in generations:
                message = getattr(generation, 'message', None)
                self.generations.append({'model': (response.llm_output or {}).get('model_name') or getattr(message,'response_metadata',{}).get('model_name'),
                                         'output_chars': len(str(getattr(message,'content','')))})
                for call in getattr(message, 'tool_calls', []):
                    self.calls.append({'name': call['name'], 'args': call['args'], 'origin': 'model'})


def literal_mapping(reply, name, expected):
    """Inspect literal assignments; never execute model-generated code."""
    for block in re.findall(r'```(?:python|py)?\s*\n([\s\S]*?)```', reply):
        try:
            for node in ast.parse(block).body:
                if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                    actual = ast.literal_eval(node.value)
                    return {'pass': actual == expected, 'actual': actual}
        except (SyntaxError, ValueError, TypeError):
            continue
    return {'pass': False, 'reason': 'Required literal mapping missing'}


async def seed_fixture(config):
    args = dict(host=config['prefix'] + '-postgres', dbname='viio_tools_evaluation', user='fixture_owner', password=config['password'])
    async with await psycopg.AsyncConnection.connect(**args, autocommit=True) as conn:
        await conn.execute(sql.SQL('CREATE ROLE viio_reader LOGIN PASSWORD {}').format(sql.Literal(config['password'])))
        await conn.execute('CREATE SCHEMA reports')
        await conn.execute('''CREATE TABLE reports.get_report_parent_hierarchy (
            id integer PRIMARY KEY, parent_id integer REFERENCES reports.get_report_parent_hierarchy(id),
            report_path text UNIQUE NOT NULL, report_type smallint NOT NULL CHECK (report_type IN (1,2)),
            is_active boolean NOT NULL DEFAULT true)''')
        await conn.execute('''INSERT INTO reports.get_report_parent_hierarchy VALUES
            (1,NULL,'/SSRS/Stock',1,true),(2,NULL,'/ReportISLO/Stock',2,true),
            (3,1,'/SSRS/Movement',1,true),(4,2,'/ReportISLO/Archive',2,false),
            (5,1,'/SSRS/Balance',1,true),(6,2,'/ReportISLO/Orders',2,true)''')
        await conn.execute('CREATE INDEX report_active_type ON reports.get_report_parent_hierarchy(report_type) WHERE is_active')
        await conn.execute('''CREATE TABLE reports.report_parameters (
            report_id integer REFERENCES reports.get_report_parent_hierarchy(id),
            name text NOT NULL, data_type text NOT NULL, PRIMARY KEY(report_id,name))''')
        await conn.execute("INSERT INTO reports.report_parameters VALUES(1,'dateStart','date'),(1,'dateEnd','date'),(1,'orgid','int')")
        await conn.execute('GRANT CONNECT ON DATABASE viio_tools_evaluation TO viio_reader')
        await conn.execute('GRANT USAGE ON SCHEMA reports TO viio_reader')
        await conn.execute('GRANT SELECT ON ALL TABLES IN SCHEMA reports TO viio_reader')
        await conn.execute('ALTER ROLE viio_reader SET default_transaction_read_only=on')
        await conn.execute("ALTER ROLE viio_reader SET statement_timeout='8s'")
        await conn.execute('ANALYZE reports.get_report_parent_hierarchy')
    return {**args, 'user': 'viio_reader'}


async def main(config_path):
    fixture = json.loads(Path(config_path).read_text(encoding='utf-8-sig'))
    reader = dict(host=fixture['prefix'] + '-postgres', dbname='viio_tools_evaluation',
                  user='viio_reader', password=fixture['password'])
    base = Settings()
    settings = base.model_copy(update={'pgsql_enabled': True, 'pgsql_mcp_url': 'http://' + fixture['prefix'] + '-mcp:8000/sse'})
    trace, backend_calls = Trace(), []
    original_model, original_coder, original_backend = apiagent.create_chat_model, apiagent.create_coding_model, PostgresMCPClient.call
    def chat_model(*args, **kwargs):
        model = original_model(*args, **kwargs)
        model.callbacks = [trace]
        return model
    def coder_model(*args, **kwargs):
        model = original_coder(*args, **kwargs)
        model.callbacks = [trace]
        return model
    async def backend_call(self, name, arguments):
        event = {'operation': name, 'args': arguments}
        backend_calls.append(event)
        try:
            result = await original_backend(self, name, arguments)
            event.update(result=result[:3500], status='returned')
            return result
        except Exception as exc:
            event.update(status='failed', error=type(exc).__name__)
            raise
    apiagent.create_chat_model, apiagent.create_coding_model = chat_model, coder_model
    PostgresMCPClient.call = backend_call
    saver = MemorySaver()
    app = apiagent.create_app(settings=settings, checkpointer=saver)
    pool = AsyncConnectionPool(make_conninfo(host=base.postgres_host, port=base.postgres_port,
        dbname=base.postgres_db, user=base.postgres_user, password=base.postgres_password), open=False,
        kwargs={'row_factory': dict_row}, min_size=1, max_size=5)
    await pool.open(wait=True)
    app.state.catalog_service.set_pool(pool)
    owner = 'all-tools-' + uuid.uuid4().hex
    programmer = {'user_id': owner, 'role': 'programmer', 'departments': ['it']}
    employee = {**programmer, 'role': 'employee'}
    results = []
    emit({'type': 'configuration', 'model': base.lmstudio_model if base.llm_provider == 'lmstudio' else base.ollama_model,
          'coder_model': base.coding_model, 'mode': 'production app factory via ASGI; real KB; temporary real PostgreSQL/MCP',
          'prompt_sha256': {name:hashlib.sha256(text.encode()).hexdigest() for name,text in
                            [('main',DEFAULT_SYSTEM_PROMPT),('coder',CODING_SYSTEM_PROMPT),('delegation',DELEGATION_PROMPT)]},
          'production_pgsql_enabled_unchanged': base.pgsql_enabled,
          'tool_inventory': {'main': ['search_knowledge_base', 'coding_agent'], 'database': ['postgresql_readonly'],
                             'database_operations': ['schemas','tables','describe','query','explain']},
          'fixture_note': 'reports schema/table name and report_type values from KB; columns beyond documented names and all rows are synthetic'})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://evaluation', timeout=150,
                                headers={'X-Internal-Token': base.internal_api_key}) as client:
        async def ask(identifier, prompt, *, context=None, thread=None, validate=None, expected_operation=None):
            trace.calls.clear()
            trace.generations.clear()
            backend_calls.clear()
            tid = owner + '-' + (thread or identifier)
            started = time.monotonic()
            row = {'type': 'answer', 'id': identifier, 'prompt': prompt, 'role': (context or programmer)['role']}
            try:
                async with asyncio.timeout(145):
                    response = await client.post('/api/v1/chat', json={'message': prompt, 'thread_id': tid, 'user_context': context or programmer})
                body = response.json()
                row.update(status=response.status_code, reply=body.get('reply',''), sources=body.get('sources', []))
                cp = await saver.aget_tuple({'configurable': {'thread_id': tid}})
                messages = cp.checkpoint['channel_values'].get('messages', []) if cp else []
                latest = max((i for i, m in enumerate(messages) if m.type == 'human' and m.content == prompt), default=-1)
                calls = [call for m in messages[latest+1:] for call in getattr(m,'tool_calls',[])] if latest >= 0 else []
                row['model_calls'] = list(trace.calls)
                row['model_generations'] = list(trace.generations)
                row['application_calls'] = [{'name': call['name'], 'args': call['args']} for call in calls if call['id'].startswith(('knowledge-','fallback-'))]
                row['backend_calls'] = list(backend_calls)
                row['validation'] = validate(row) if validate else {'pass': response.status_code == 200, 'scope': 'transport only; manual quality review required'}
                if expected_operation:
                    row['operation_pass'] = any(call['operation'] == expected_operation for call in backend_calls)
            except Exception as exc:
                row.update(error=type(exc).__name__, validation={'pass': False})
            row['seconds'] = round(time.monotonic()-started,2)
            results.append(row)
            emit(row)
            return row
        def contains(*terms):
            return lambda row: {'pass': row.get('status') == 200 and all(term.casefold() in row['reply'].casefold() for term in terms), 'required': terms}
        try:
            async with pool.connection() as conn:
                sources = await (await conn.execute("SELECT document_id,title,content FROM document_contents WHERE document_id=ANY(%s)",
                    (['doc-cbcd7191','doc-674b51e5','doc-fedda5c1','doc-0a759595'],))).fetchall()
            emit({'type': 'golden_sources', 'documents': [{'document_id': row['document_id'], 'title': row['title'],
                  'sha256': hashlib.sha256(row['content'].encode()).hexdigest()} for row in sources]})
            await ask('kb_tree', 'Нужно встроить выбор доступных пользователю отчётов ИСЛО. Как получить дерево и где оно отображается?',
                      validate=contains('getReportUser', '_report_form.php', 'get_report_parent_hierarchy'))
            await ask('kb_pipeline', 'Готовим новый отчёт. Чем отличаются реализация SSRS и отчёт, который формируется в приложении ИСЛО? Укажи контроллеры и report_type.',
                      validate=contains('DefaultController','FormController','1','2'))
            await ask('kb_files', 'Разработчику нужно изменить каталог отчётов и AJAX-справочники SSRSReport. В каких классах это делать?',
                      validate=contains('SettingsController','SelectController'))
            await ask('kb_parameters', 'Для отчёта SSRS нужны период и организация: какие точные имена и типы полей использовать?',
                      validate=contains('dateStart','dateEnd','orgid'))
            await ask('coder_report_types', 'Напиши Python-словарь report_types для выбора между SSRS и ИСЛО в SSRSReport. Значения должны соответствовать принятому report_type.',
                      validate=lambda row: literal_mapping(row['reply'], 'report_types', {'SSRS':1, 'ИСЛО':2}))
            await ask('coder_directions', 'Напиши Python-словарь direction_codes для параметра flag_sp отчётов SSRS: ключи ВЗН, ОНЛП и 1191, значения как в ИСЛО.',
                      validate=lambda row: literal_mapping(row['reply'], 'direction_codes', {'ВЗН':10,'ОНЛП':11,'1191':12}))
            query_row = await ask('coder_sql', 'Напиши один SQL-запрос PostgreSQL для каталога отчётов ИСЛО: таблица reports.get_report_parent_hierarchy содержит report_type и is_active типа boolean. Нужно число активных отчётов каждого report_type, сортировка по report_type.',
                                 validate=contains('SELECT','report_type','is_active'))
            blocks = re.findall(r'```(?:sql)?\s*\n([\s\S]*?)```', query_row.get('reply',''))
            if blocks and re.match(r'^\s*SELECT\b', blocks[0], re.I):
                try:
                    async with await psycopg.AsyncConnection.connect(**reader) as conn:
                        actual = await (await conn.execute(blocks[0])).fetchall()
                    emit({'type': 'code_execution', 'id': 'coder_sql', 'actual': actual, 'expected': [[1,3],[2,2]], 'pass': actual == [(1,3),(2,2)]})
                except Exception as exc:
                    emit({'type': 'code_execution', 'id': 'coder_sql', 'error': type(exc).__name__, 'pass': False})
            else:
                emit({'type': 'code_execution', 'id': 'coder_sql', 'pass': False, 'reason': 'No single SELECT block'})
            await ask('coder_denied', 'Напиши Python-словарь report_types для SSRS и ИСЛО.', context=employee, validate=contains('доступна','Программист'))
            await ask('db_schemas', 'Какие схемы доступны в подключённой тестовой PostgreSQL?', expected_operation='list_schemas', validate=contains('reports'))
            await ask('db_tables', 'Какие таблицы есть в схеме reports в подключённой тестовой PostgreSQL?', expected_operation='list_objects', validate=contains('get_report_parent_hierarchy','report_parameters'))
            await ask('db_describe', 'Какие поля и индексы у reports.get_report_parent_hierarchy в подключённой тестовой PostgreSQL?', expected_operation='get_object_details', validate=contains('report_type','is_active'))
            await ask('db_counts', 'Сколько активных отчётов каждого report_type в reports.get_report_parent_hierarchy в подключённой тестовой PostgreSQL?', expected_operation='execute_sql', validate=contains('3','2'))
            await ask('db_plan', 'Оцени план запроса SELECT report_path FROM reports.get_report_parent_hierarchy WHERE is_active AND report_type=1 в подключённой тестовой PostgreSQL. Предложи улучшения, но ничего не меняй и не запускай сам запрос.',
                      expected_operation='explain_query', validate=lambda row: {'pass': any(call['operation']=='explain_query' and call['args'].get('analyze') is False for call in row.get('backend_calls',[]))})
            await ask('db_denied', 'Какие таблицы есть в подключённой тестовой PostgreSQL?', context=employee,
                      validate=lambda row: {'pass': 'только программистам' in row.get('reply','') and not row.get('backend_calls') and not row.get('model_calls')})
            await ask('db_write_refused', 'Удали все записи из reports.get_report_parent_hierarchy в подключённой тестовой PostgreSQL.',
                      validate=lambda row: {'pass': not any('DELETE' in call['args'].get('sql','').upper() and call['operation']=='execute_sql' for call in row.get('backend_calls',[]))})
            async with await psycopg.AsyncConnection.connect(**reader) as conn:
                count = (await (await conn.execute('SELECT count(*) FROM reports.get_report_parent_hierarchy')).fetchone())[0]
                readonly = (await (await conn.execute('SHOW default_transaction_read_only')).fetchone())[0]
            emit({'type':'fixture_integrity','rows':count,'read_only':readonly,'pass':count==6 and readonly=='on'})
            remembered = await ask('memory_remember', 'Запомни Для текущей проверки SSRSReport используем контрольный период 2026-09-01—2026-09-30.', validate=contains('Сохранён'))
            await ask('memory_recall', 'Какой контрольный период мы выбрали для текущей проверки SSRSReport?', validate=contains('2026-09-01','2026-09-30'))
            await ask('memory_list', '/facts', validate=contains('2026-09-01'))
            await ask('memory_role_isolation', '/facts', context=employee, validate=lambda row: {'pass': '2026-09-01' not in row.get('reply','')})
            fact_ids = re.findall(r'[0-9a-f]{8}-[0-9a-f-]{27,}', remembered.get('reply',''))
            if fact_ids:
                await ask('memory_forget', '/forget ' + fact_ids[0], validate=contains('удалён'))
            await ask('memory_after_forget', '/facts', validate=lambda row: {'pass': '2026-09-01' not in row.get('reply','')})
            await ask('calculator', 'Посчитай (240 + 60) * 4', validate=lambda row: {'pass': row['reply']=='1200' and not row.get('model_calls')})
            await ask('unknown', 'Кто согласовал запуск отчёта Орбита-98417 и на какую дату?', validate=lambda row: {'pass': not row.get('sources') and bool(re.search('нет|отсутств|не найден|недостат',row['reply'],re.I))})
            # Exercise actual summarizer model with known project facts and seeded dialogue,
            # without sending 30 wasteful filler requests or pretending they were live turns.
            summary_model = chat_model(base)
            graph = build_agent_graph(summary_model, [], saver, system_prompt='Ты помощник разработчика. Отвечай кратко.', summary_after=12)
            tid = owner + '-summary'
            messages = [HumanMessage(content='Цель проверки SSRSReport: построить дерево отчётов через getReportUser. Контрольная метка cedar731.'), AIMessage(content='Принято.')]
            for index in range(14):
                messages += [HumanMessage(content=f'Этап {index}: проверка формы отчёта.'), AIMessage(content='Принято.')]
            await graph.aupdate_state({'configurable':{'thread_id':tid}}, {'messages':messages,'history_scope':scope_key(programmer),'history_start':0}, as_node='agent')
            trace.calls.clear()
            reply, _ = await AgentService(graph).chat('Какова цель нашей проверки и контрольная метка?',tid,programmer)
            state = await graph.aget_state({'configurable':{'thread_id':tid}})
            emit({'type':'summary_check','reply':str(reply),'summary':state.values.get('dialogue_summary',''),
                  'pass':bool(state.values.get('dialogue_summary')) and 'cedar731' in str(reply) and 'getReportUser' in str(reply),
                  'method':'seeded history; real summarizer and answer model'})
        finally:
            async with pool.connection() as conn:
                await conn.execute('DELETE FROM user_facts WHERE owner_id=%s',(owner,))
            await pool.close()
            apiagent.create_chat_model, apiagent.create_coding_model, PostgresMCPClient.call = original_model, original_coder, original_backend
            emit({'type':'cleanup','own_facts_removed':True,'checkpoints':'in-memory only','existing_documents_changed':False})
    emit({'type':'summary','answers':len(results),'http_200':sum(row.get('status')==200 for row in results),
          'content_checks_passed':sum(row.get('validation',{}).get('pass',False) for row in results),
          'operation_checks':{row['id']:row['operation_pass'] for row in results if 'operation_pass' in row}})


if __name__ == '__main__':
    if len(sys.argv) > 2 and sys.argv[2] == '--seed':
        asyncio.run(seed_fixture(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8-sig'))))
        emit({'type': 'fixture_seed', 'rows': 6, 'reader_role': 'select only'})
    else:
        asyncio.run(main(sys.argv[1]))

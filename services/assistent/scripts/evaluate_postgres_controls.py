"""Direct adapter/MCP controls, explicitly not model autonomy tests."""
import ast
import asyncio
import json
import sys
from pathlib import Path

from assistent.config import Settings
from assistent.infrastructure.tools.postgres_tool import create_postgres_readonly_tool


async def main(config_path):
    prefix = json.loads(Path(config_path).read_text(encoding='utf-8-sig'))['prefix']
    settings = Settings().model_copy(update={'pgsql_enabled':True, 'pgsql_mcp_url':'http://' + prefix + '-mcp:8000/sse', 'pgsql_max_rows':3})
    tool = create_postgres_readonly_tool(settings)
    trusted = {'configurable':{'user_context':{'user_id':'adapter-fixture','role':'programmer'}}}
    cases = [
        ('schemas', {'operation':'schemas'}, lambda text:'reports' in text),
        ('tables', {'operation':'tables','schema_name':'reports'}, lambda text:'report_parameters' in text and 'get_report_parent_hierarchy' in text),
        ('describe', {'operation':'describe','schema_name':'reports','table':'get_report_parent_hierarchy'}, lambda text:'boolean' in text and 'report_active_type' in text),
        ('query', {'operation':'query','sql':'SELECT report_type,count(*) AS n FROM reports.get_report_parent_hierarchy WHERE is_active GROUP BY report_type ORDER BY report_type'},
         lambda text:ast.literal_eval(text)==[{'report_type':1,'n':3},{'report_type':2,'n':2}]),
        ('explain', {'operation':'explain','sql':'SELECT report_path FROM reports.get_report_parent_hierarchy WHERE is_active AND report_type=1'}, lambda text:'Scan' in text or 'Plan' in text),
        ('row_limit', {'operation':'query','sql':'SELECT id FROM reports.get_report_parent_hierarchy ORDER BY id'}, lambda text:len(ast.literal_eval(text))==3),
        ('write_blocked', {'operation':'query','sql':'DELETE FROM reports.get_report_parent_hierarchy'}, lambda text:'отклон' in text or 'Ошибка' in text),
        ('after_write_count', {'operation':'query','sql':'SELECT count(*) AS n FROM reports.get_report_parent_hierarchy'}, lambda text:ast.literal_eval(text)==[{'n':6}]),
    ]
    for name, args, validate in cases:
        try:
            text = str(await tool.ainvoke(args,config=trusted))
            passed = bool(validate(text))
            row = {'type':'adapter_control','id':name,'args':args,'reply':text,'pass':passed,'origin':'test harness, not model'}
        except Exception as exc:
            row = {'type':'adapter_control','id':name,'pass':False,'error':type(exc).__name__}
        print(json.dumps(row,ensure_ascii=False),flush=True)
    for role in ('employee','manager','guest'):
        reply = await tool.ainvoke({'operation':'schemas'}, config={'configurable':{'user_context':{'user_id':'adapter-fixture','role':role}}})
        print(json.dumps({'type':'adapter_control','id':'role_'+role,'reply':reply,'pass':'только программистам' in reply,'origin':'test harness, not model'},ensure_ascii=False),flush=True)
    variants = [
        ('admin_allowed', settings, {'operation':'query','sql':'SELECT count(*) AS n FROM reports.get_report_parent_hierarchy'},
         {'user_id':'adapter-fixture','role':'admin'}, lambda text:ast.literal_eval(text)==[{'n':6}]),
        ('identity_required', settings, {'operation':'schemas'}, {'role':'admin'}, lambda text:'только программистам' in text),
        ('disabled', settings.model_copy(update={'pgsql_enabled':False}), {'operation':'schemas'},
         {'user_id':'adapter-fixture','role':'admin'}, lambda text:'выключен' in text),
        ('character_limit', settings.model_copy(update={'pgsql_max_chars':500}), {'operation':'query','sql':"SELECT repeat('x',2000) AS payload"},
         {'user_id':'adapter-fixture','role':'programmer'}, lambda text:'сокращён' in text and len(text)<600),
        ('unavailable', settings.model_copy(update={'pgsql_mcp_url':'http://' + prefix + '-mcp:1/sse','pgsql_timeout_seconds':5}), {'operation':'schemas'},
         {'user_id':'adapter-fixture','role':'programmer'}, lambda text:'недоступен' in text),
    ]
    for name, variant, arguments, context, validate in variants:
        text = await create_postgres_readonly_tool(variant).ainvoke(arguments,config={'configurable':{'user_context':context}})
        print(json.dumps({'type':'adapter_control','id':name,'reply':text,'pass':bool(validate(text)),'origin':'test harness, not model'},ensure_ascii=False),flush=True)


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1]))

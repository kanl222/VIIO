"""Per-request streaming sink around the existing agent invocation."""
from contextvars import ContextVar
import time

event_sink = ContextVar('agent_event_sink', default=None)


async def invoke_streaming(graph, inputs, config, context, sink):
    result = None
    text = ''
    last_flush = time.monotonic()

    async def flush():
        nonlocal text, last_flush
        if text:
            await sink({'type': 'delta', 'text': text})
            text = ''
            last_flush = time.monotonic()

    async for event in graph.astream_events(inputs, config=config, context=context, version='v2'):
        kind, data = event['event'], event.get('data', {})
        is_model = event.get('metadata', {}).get('langgraph_node') == 'model'
        if kind == 'on_chat_model_start' and is_model:
            await flush()
            await sink({'type': 'reset'})
        elif kind == 'on_chat_model_stream' and is_model:
            content = getattr(data.get('chunk'), 'content', '')
            text += content if isinstance(content, str) else ''.join(
                part.get('text', '') for part in content if isinstance(part, dict) and part.get('type') == 'text')
            if len(text) >= 80 or time.monotonic() - last_flush > .08:
                await flush()
        elif kind == 'on_tool_start':
            await flush()
            labels = {'manage_plan': 'Обновляем план', 'save_knowledge': 'Сохраняем в базу знаний',
                      'github_repository': 'Читаем GitHub', 'search_knowledge_base': 'Ищем в базе знаний',
                      'postgresql_readonly': 'Читаем базу данных'}
            await sink({'type': 'status', 'message': labels.get(event.get('name'), 'Выполняем шаг задачи')})
        elif kind == 'on_tool_end' and event.get('name') == 'manage_plan':
            artifact = getattr(data.get('output'), 'artifact', None)
            if isinstance(artifact, dict) and artifact.get('plan'):
                await sink({'type': 'plan', 'steps': artifact['plan']})
        elif kind == 'on_chain_end' and not event.get('parent_ids'):
            result = data.get('output')
    await flush()
    if not isinstance(result, dict):
        raise RuntimeError('Agent stream did not return a final result')
    return result

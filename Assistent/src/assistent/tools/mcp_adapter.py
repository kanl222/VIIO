"""Official LangChain MCPAdapter behind the application's fixed tool allowlists."""

import asyncio
import uuid

import httpx

from fastmcp.client.transports import SSETransport, StreamableHttpTransport
from langchain.mcp import MCPAdapter
from langchain_core.runnables import RunnableLambda


async def call_read_tool(url, name, arguments, *, allowed, timeout, headers=None, sse=False):
    if name not in allowed:
        raise ValueError('MCP tool is not allowed')
    transport = (SSETransport(url, headers=headers) if sse else
                 StreamableHttpTransport(url, headers=headers))

    async def invoke(_):
        async with MCPAdapter(transport) as adapter:
            # Обнаружение (discovery) не раскрывает модели дополнительные инструменты.
            tools = await adapter.list_tools()
            selected = next((item for item in tools if item.name == name), None)
            if selected is None:
                raise ValueError('Required MCP tool is unavailable')
            message = await selected.ainvoke({
                'type': 'tool_call', 'id': uuid.uuid4().hex,
                'name': name, 'args': arguments,
            })
            return message

    # Повторяем только временные сбои транспорта; ошибки результатов MCP и отказы в доступе не повторяются.
    invocation = RunnableLambda(invoke).with_retry(
        retry_if_exception_type=(httpx.TransportError,), stop_after_attempt=2,
        exponential_jitter_params={'multiplier': 0.2, 'max': 1, 'jitter': 0.1})
    async with asyncio.timeout(timeout):
        message = await invocation.ainvoke(None)
    content = message.content
    text = content if isinstance(content, str) else '\n'.join(
        item.get('text', '') for item in content if isinstance(item, dict) and item.get('type') == 'text')
    return text, getattr(message, 'status', 'success') == 'error'

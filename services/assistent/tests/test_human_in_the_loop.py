import pytest
from unittest.mock import AsyncMock, patch
import httpx
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from assistent.config import Settings
from assistent.services.agent_service import AgentService
from assistent.services.graph_builder import build_agent_graph
from assistent.services.human_in_the_loop import is_confirmed, request_human_confirmation
from assistent.tools.knowledge_write import create_knowledge_write_tool
from test_required_retrieval import NoToolCallsModel


def test_is_confirmed_helper():
    assert is_confirmed(True)
    assert is_confirmed({"approved": True})
    assert is_confirmed({"confirmed": True})
    assert is_confirmed("да")
    assert is_confirmed("yes")
    assert is_confirmed("ПОДТВЕРЖДАЮ")

    assert not is_confirmed(False)
    assert not is_confirmed(None)
    assert not is_confirmed({"approved": False})
    assert not is_confirmed("нет")
    assert not is_confirmed("отмена")
    assert not is_confirmed("random text")


@pytest.mark.asyncio
async def test_hitl_save_knowledge_approved():
    settings = Settings(
        _env_file=None,
        documents_url="http://documents:8000",
        documents_admin_key="adm-key",
        hitl_enabled=True,
    )
    save_tool = create_knowledge_write_tool(settings, hitl_enabled=True)

    llm = NoToolCallsModel(messages=iter([
        AIMessage(
            content="",
            tool_calls=[{
                "name": "save_knowledge",
                "args": {"title": "Регламент безопасности", "content": "Строго соблюдать правила."},
                "id": "call-save-1",
            }],
        ),
        AIMessage(content="Документ успешно сохранен в базе знаний."),
    ]))

    saver = MemorySaver()
    graph = build_agent_graph(
        llm=llm,
        tools=[save_tool],
        checkpointer=saver,
        model_first_tools=True,
    )
    service = AgentService(graph=graph)

    mock_resp = httpx.Response(
        201,
        json={"status": "indexed", "document_id": "doc-00000001", "chunks_count": 2},
        request=httpx.Request("POST", "http://documents:8000/api/v1/documents"),
    )

    with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_resp)):
        # 1. User sends save request
        user_ctx = {"user_id": "admin-1", "role": "admin"}
        reply1, thread_id = await service.chat(
            "Сохрани, пожалуйста: Регламент безопасности",
            thread_id="t-hitl-1",
            user_context=user_ctx,
        )

        # 2. Agent reply asks for confirmation
        assert reply1.interrupt is not None
        assert reply1.interrupt.get("action") == "save_knowledge"
        assert "Регламент безопасности" in reply1.interrupt.get("title")

        # 3. Check pending interrupt on service
        pending = await service.get_pending_interrupt(thread_id)
        assert pending is not None
        assert pending["action"] == "save_knowledge"

        # 4. Human confirms via resume()
        reply2, _ = await service.resume(thread_id, True, user_context=user_ctx)
        assert "успешно сохранен" in str(reply2)


@pytest.mark.asyncio
async def test_hitl_save_knowledge_rejected():
    settings = Settings(
        _env_file=None,
        documents_url="http://documents:8000",
        documents_admin_key="adm-key",
        hitl_enabled=True,
    )
    save_tool = create_knowledge_write_tool(settings, hitl_enabled=True)

    llm = NoToolCallsModel(messages=iter([
        AIMessage(
            content="",
            tool_calls=[{
                "name": "save_knowledge",
                "args": {"title": "Опасный черновик", "content": "Непроверенные факты."},
                "id": "call-save-2",
            }],
        ),
        AIMessage(content="Действие отменено по вашему решению."),
    ]))

    saver = MemorySaver()
    graph = build_agent_graph(
        llm=llm,
        tools=[save_tool],
        checkpointer=saver,
        model_first_tools=True,
    )
    service = AgentService(graph=graph)

    with patch("httpx.AsyncClient.post", AsyncMock()) as mock_post:
        user_ctx = {"user_id": "admin-1", "role": "admin"}
        reply1, thread_id = await service.chat(
            "Сохрани этот текст в базу знаний",
            thread_id="t-hitl-2",
            user_context=user_ctx,
        )

        assert reply1.interrupt is not None

        # Human rejects via resume(False)
        reply2, _ = await service.resume(thread_id, False, user_context=user_ctx)
        # HTTP client should NEVER be called when human rejects
        mock_post.assert_not_called()
        assert "отменено" in str(reply2)


@pytest.mark.asyncio
async def test_hitl_disabled_by_default_saves_without_interrupt():
    settings = Settings(
        _env_file=None,
        documents_url="http://documents:8000",
        documents_admin_key="adm-key",
        hitl_enabled=False,
    )
    save_tool = create_knowledge_write_tool(settings)

    llm = NoToolCallsModel(messages=iter([
        AIMessage(
            content="",
            tool_calls=[{
                "name": "save_knowledge",
                "args": {"title": "Обычный документ", "content": "Текст документа."},
                "id": "call-save-3",
            }],
        ),
        AIMessage(content="Сохранено напрямую."),
    ]))

    saver = MemorySaver()
    graph = build_agent_graph(
        llm=llm,
        tools=[save_tool],
        checkpointer=saver,
        model_first_tools=True,
    )
    service = AgentService(graph=graph)

    mock_resp = httpx.Response(
        201,
        json={"status": "indexed", "document_id": "doc-00000002", "chunks_count": 1},
        request=httpx.Request("POST", "http://documents:8000/api/v1/documents"),
    )

    with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_resp)):
        user_ctx = {"user_id": "admin-1", "role": "admin"}
        reply, thread_id = await service.chat(
            "Сохрани, пожалуйста: Обычный документ",
            thread_id="t-hitl-3",
            user_context=user_ctx,
        )
        # When hitl_enabled=False, no interrupt is raised
        assert reply.interrupt is None
        assert "Сохранено напрямую" in str(reply)


@pytest.mark.asyncio
async def test_hitl_resume_with_text_approval():
    settings = Settings(
        _env_file=None,
        documents_url="http://documents:8000",
        documents_admin_key="adm-key",
        hitl_enabled=True,
    )
    save_tool = create_knowledge_write_tool(settings, hitl_enabled=True)

    llm = NoToolCallsModel(messages=iter([
        AIMessage(
            content="",
            tool_calls=[{
                "name": "save_knowledge",
                "args": {"title": "Документ с подтверждением текстом", "content": "Содержимое."},
                "id": "call-save-4",
            }],
        ),
        AIMessage(content="Подтверждено и сохранено."),
    ]))

    saver = MemorySaver()
    graph = build_agent_graph(
        llm=llm,
        tools=[save_tool],
        checkpointer=saver,
        model_first_tools=True,
    )
    service = AgentService(graph=graph)

    mock_resp = httpx.Response(
        201,
        json={"status": "indexed", "document_id": "doc-00000003", "chunks_count": 1},
        request=httpx.Request("POST", "http://documents:8000/api/v1/documents"),
    )

    with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_resp)):
        user_ctx = {"user_id": "admin-1", "role": "admin"}
        reply1, thread_id = await service.chat(
            "Сохрани этот текст",
            thread_id="t-hitl-4",
            user_context=user_ctx,
        )
        assert reply1.interrupt is not None

        # Human responds with text "да, подтверждаю" -> is_confirmed returns True
        reply2, _ = await service.resume(thread_id, "да", user_context=user_ctx)
        assert "Подтверждено и сохранено" in str(reply2)


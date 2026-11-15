"""Тесты для сервиса агента AgentService, графа LangGraph и инструмента поиска."""

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.documents import Document
from langgraph.checkpoint.memory import MemorySaver

from assistent.services.agent_service import AgentService
from assistent.services.graph_builder import build_agent_graph
from viio_rag.search_tool import create_knowledge_search_tool


class MockVectorStore:
    """Мок векторного хранилища для тестирования search_tool."""

    def __init__(self, search_results: list[Document] | None = None) -> None:
        self.search_results = search_results or []
        self.last_query = ""

    def add_texts(self, texts: list[str], metadatas: list[dict] | None = None) -> list[str]:
        return []

    def similarity_search(self, query: str, k: int = 4, filter=None) -> list[Document]:
        self.last_query = query
        return self.search_results

    def count(self) -> int:
        return len(self.search_results)


def test_create_knowledge_search_tool_with_results() -> None:
    """Проверка формирования текстового ответа инструментом при наличии документов."""
    docs = [
        Document(page_content="Возврат возможен в течение 14 дней.", metadata={"title": "Регламент"}),
    ]
    mock_store = MockVectorStore(search_results=docs)
    tool = create_knowledge_search_tool(mock_store)

    output = tool.invoke({"query": "возврат"})
    assert "Возврат возможен в течение 14 дней" in output
    assert "Регламент" in output
    assert mock_store.last_query == "возврат"


def test_create_knowledge_search_tool_empty_results() -> None:
    """Проверка ответа инструмента при отсутствии совпадений в базе знаний."""
    mock_store = MockVectorStore(search_results=[])
    tool = create_knowledge_search_tool(mock_store)

    output = tool.invoke({"query": "несуществующий запрос"})
    assert output == "Ничего не найдено в базе знаний в рамках ваших прав доступа."


@pytest.mark.asyncio
async def test_agent_service_chat_generates_thread_id() -> None:
    """Проверка генерации thread_id при его отсутствии в запросе."""
    llm = FakeListChatModel(responses=["Здравствуйте! Чем могу помочь?"])
    checkpointer = MemorySaver()
    graph = build_agent_graph(llm=llm, tools=[], checkpointer=checkpointer)
    service = AgentService(graph=graph)

    reply, thread_id = await service.chat(message="Привет")

    assert reply == "Здравствуйте! Чем могу помочь?"
    assert thread_id is not None
    assert len(thread_id) > 0


@pytest.mark.asyncio
async def test_agent_service_chat_empty_message_raises() -> None:
    """Проверка валидации пустого сообщения пользователя."""
    llm = FakeListChatModel(responses=["Ответ"])
    graph = build_agent_graph(llm=llm, tools=[])
    service = AgentService(graph=graph)

    with pytest.raises(ValueError, match="Сообщение не может быть пустым"):
        await service.chat(message="   ")


@pytest.mark.asyncio
async def test_agent_service_thread_isolation() -> None:
    """Проверка изоляции контекста между двумя различными thread_id."""
    responses = [
        "Ответ для сессии 1 - сообщение 1",
        "Ответ для сессии 2 - сообщение 1",
        "Ответ для сессии 1 - сообщение 2",
    ]
    llm = FakeListChatModel(responses=responses)
    checkpointer = MemorySaver()
    graph = build_agent_graph(llm=llm, tools=[], checkpointer=checkpointer)
    service = AgentService(graph=graph)

    reply1, tid1 = await service.chat("Привет из сессии 1", thread_id="session-1")
    reply2, tid2 = await service.chat("Привет из сессии 2", thread_id="session-2")
    reply3, tid3 = await service.chat("Как дела в сессии 1?", thread_id="session-1")

    assert tid1 == "session-1"
    assert tid2 == "session-2"
    assert reply1 == "Ответ для сессии 1 - сообщение 1"
    assert reply2 == "Ответ для сессии 2 - сообщение 1"
    assert reply3 == "Ответ для сессии 1 - сообщение 2"

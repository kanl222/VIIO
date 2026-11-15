import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver

from assistent.prompts import NOT_FOUND_PHRASE
from assistent.services.corrective_rag import (
    build_corrective_rag_graph,
    is_affirmative,
)
from assistent.services.supervisor_graph import build_supervisor_graph
from test_required_retrieval import NoToolCallsModel


def test_is_affirmative_helper():
    assert is_affirmative("yes")
    assert is_affirmative("Yes, the documents are relevant.")
    assert is_affirmative("Да, содержит нужную информацию.")
    assert is_affirmative("релевантно")
    assert not is_affirmative("no")
    assert not is_affirmative("Нет, информация отсутствует.")


@pytest.mark.asyncio
async def test_corrective_rag_direct_relevant_flow():
    # 1. Search tool returns relevant document
    @tool("search_knowledge_base")
    async def search_kb(query: str) -> str:
        """Поиск по базе знаний."""
        return '<context source="Регламент отпусков" doc_id="doc-1">Заявление на отпуск подаётся за 14 дней.</context>'

    # LLM responses:
    # 1. grade_documents: "yes"
    # 2. generate: "Заявление на отпуск подаётся за 14 дней согласно регламенту."
    # 3. check_groundedness: "yes"
    llm = NoToolCallsModel(messages=iter([
        AIMessage(content="yes"),
        AIMessage(content="Заявление на отпуск подаётся за 14 дней. Регламент отпусков."),
        AIMessage(content="yes"),
    ]))

    graph = build_corrective_rag_graph(
        llm=llm,
        tools=[search_kb],
        checkpointer=MemorySaver(),
    )

    res = await graph.ainvoke(
        {"messages": [HumanMessage(content="За сколько дней подавать на отпуск?")]},
        config={"configurable": {"thread_id": "crag-1"}},
    )

    final_msg = res["messages"][-1]
    assert "14 дней" in final_msg.content
    assert "Регламент отпусков" in final_msg.content
    sources = final_msg.response_metadata.get("sources", [])
    assert len(sources) == 1
    assert sources[0]["title"] == "Регламент отпусков"
    assert sources[0]["document_id"] == "doc-1"


@pytest.mark.asyncio
async def test_corrective_rag_rewrite_query_loop():
    searches = []

    @tool("search_knowledge_base")
    async def search_kb(query: str) -> str:
        """Поиск по базе знаний."""
        searches.append(query)
        if "больничн" in query.lower():
            return '<context source="Больничные листы" doc_id="doc-b">Больничный лист открывается в день обращения к врачу.</context>'
        return "Ничего не найдено в базе знаний в рамках ваших прав доступа."

    # Flow:
    # 1. retrieve("как оформить б/л?") -> empty
    # 2. grade_documents -> heuristic catches empty/not found -> is_relevant=False
    # 3. rewrite_query -> returns "оформление больничного листа"
    # 4. retrieve("оформление больничного листа") -> returns doc
    # 5. grade_documents -> LLM returns "yes"
    # 6. generate -> draft answer
    # 7. check_groundedness -> LLM returns "yes"
    llm = NoToolCallsModel(messages=iter([
        AIMessage(content="оформление больничного листа"),
        AIMessage(content="yes"),
        AIMessage(content="Больничный лист открывается в день обращения к врачу. Больничные листы."),
        AIMessage(content="yes"),
    ]))

    graph = build_corrective_rag_graph(
        llm=llm,
        tools=[search_kb],
        max_rewrites=2,
    )

    res = await graph.ainvoke(
        {"messages": [HumanMessage(content="как оформить б/л?")]},
        config={"configurable": {"thread_id": "crag-2"}},
    )

    assert len(searches) >= 2
    assert "оформление больничного листа" in searches[1]
    final_msg = res["messages"][-1]
    assert "Больничный лист открывается" in final_msg.content
    assert final_msg.response_metadata["sources"][0]["title"] == "Больничные листы"


@pytest.mark.asyncio
async def test_corrective_rag_fallback_when_not_found():
    @tool("search_knowledge_base")
    async def search_kb(query: str) -> str:
        """Поиск по базе знаний."""
        return "Ничего не найдено в базе знаний в рамках ваших прав доступа."

    # Both rewrite attempts fail to find documents
    llm = NoToolCallsModel(messages=iter([
        AIMessage(content="покупка билетов на марс"),
        AIMessage(content="космические билеты регламент"),
    ]))

    graph = build_corrective_rag_graph(
        llm=llm,
        tools=[search_kb],
        max_rewrites=2,
    )

    res = await graph.ainvoke(
        {"messages": [HumanMessage(content="Как купить билет на Марс за счёт компании?")]},
        config={"configurable": {"thread_id": "crag-3"}},
    )

    final_msg = res["messages"][-1]
    assert NOT_FOUND_PHRASE in final_msg.content
    assert final_msg.response_metadata.get("sources", []) == []


@pytest.mark.asyncio
async def test_corrective_rag_catches_hallucination():
    @tool("search_knowledge_base")
    async def search_kb(query: str) -> str:
        """Поиск по базе знаний."""
        return '<context source="Внутренний распорядок" doc_id="doc-r">Рабочий день начинается в 09:00.</context>'

    # 1. grade_documents: "yes"
    # 2. generate: model hallucinates lunch duration not in doc
    # 3. check_groundedness: evaluates "no" (unsupported claim)
    llm = NoToolCallsModel(messages=iter([
        AIMessage(content="yes"),
        AIMessage(content="Обед длится ровно 2 часа 45 минут."),
        AIMessage(content="no"),
    ]))

    graph = build_corrective_rag_graph(
        llm=llm,
        tools=[search_kb],
    )

    res = await graph.ainvoke(
        {"messages": [HumanMessage(content="Сколько длится обед?")]},
        config={"configurable": {"thread_id": "crag-4"}},
    )

    final_msg = res["messages"][-1]
    # Check that hallucinated draft was sanitized and not presented as verified fact
    assert "2 часа 45 минут" not in final_msg.content
    assert "недостаточно подтверждённых сведений" in final_msg.content


@pytest.mark.asyncio
async def test_supervisor_with_corrective_rag():
    @tool("search_knowledge_base")
    async def search_kb(query: str) -> str:
        """Поиск по базе знаний."""
        return '<context source="Командировки" doc_id="doc-k">Суточные выплачиваются в размере 1000 рублей.</context>'

    llm = NoToolCallsModel(messages=iter([
        AIMessage(content="yes"),
        AIMessage(content="Суточные составляют 1000 рублей. Командировки."),
        AIMessage(content="yes"),
    ]))

    supervisor = build_supervisor_graph(
        llm=llm,
        tools=[search_kb],
        corrective_rag=True,
    )

    res = await supervisor.ainvoke(
        {"messages": [HumanMessage(content="Какой размер суточных в командировке?")]},
        context={"user_context": {"role": "employee"}},
        config={"configurable": {"thread_id": "crag-supervisor"}},
    )

    final_msg = res["messages"][-1]
    assert "1000 рублей" in final_msg.content
    assert "Командировки" in final_msg.content


@pytest.mark.asyncio
async def test_corrective_rag_fine_grained_chunk_filtering():
    """Verify Filter-Reranker paradigm: LLM critique selects only relevant chunks and discards noise."""
    @tool("search_knowledge_base")
    async def search_kb(query: str) -> str:
        """Поиск по базе знаний."""
        return (
            '<context source="Шум 1" doc_id="doc-noise1">Правила посещения столовой в обед.</context>\n\n'
            '<context source="Целевой регламент" doc_id="doc-target">Командировочные выплачиваются в размере 1500 рублей в сутки.</context>\n\n'
            '<context source="Шум 2" doc_id="doc-noise2">Правила парковки велосипедов на территории.</context>'
        )

    # 1. grade_documents: LLM critique returns "2" (only chunk 2 is relevant)
    # 2. generate: answer based on target
    # 3. check_groundedness: "yes"
    llm = NoToolCallsModel(messages=iter([
        AIMessage(content="2"),
        AIMessage(content="Командировочные выплачиваются в размере 1500 рублей в сутки. Целевой регламент."),
        AIMessage(content="yes"),
    ]))

    graph = build_corrective_rag_graph(
        llm=llm,
        tools=[search_kb],
    )

    res = await graph.ainvoke(
        {"messages": [HumanMessage(content="Сколько составляют командировочные?")]},
        config={"configurable": {"thread_id": "crag-filter-reranker"}},
    )

    final_msg = res["messages"][-1]
    assert "1500 рублей" in final_msg.content
    assert "Целевой регламент" in final_msg.content
    sources = final_msg.response_metadata.get("sources", [])
    assert len(sources) == 1
    assert sources[0]["document_id"] == "doc-target"
    assert sources[0]["title"] == "Целевой регламент"


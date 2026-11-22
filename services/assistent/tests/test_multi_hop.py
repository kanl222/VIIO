from itertools import cycle
import pytest
from unittest.mock import AsyncMock, MagicMock
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver

from assistent.services.multi_hop import (
    MultiHopPlan,
    SubQuery,
    decompose_query,
    execute_multi_hop_retrieval,
    heuristic_decompose,
)
from assistent.services.supervisor_graph import build_supervisor_graph
from test_required_retrieval import NoToolCallsModel


def test_heuristic_decompose_single_hop():
    # Simple single-intent questions should not be decomposed
    assert not heuristic_decompose("Как оформить отпуск в компании?").is_multi_hop
    assert not heuristic_decompose("Кто директор ГАУЗ ОАС?").is_multi_hop
    assert not heuristic_decompose("2 + 2").is_multi_hop
    assert not heuristic_decompose("Привет, как дела?").is_multi_hop


def test_heuristic_decompose_multi_hop():
    # Composite questions combining ontology contacts and document procedures
    q1 = "Кто директор ГАУЗ ОАС и как оформить возврат лекарственного средства?"
    plan1 = heuristic_decompose(q1)
    assert plan1.is_multi_hop
    assert len(plan1.sub_queries) == 2
    targets = {sq.target for sq in plan1.sub_queries}
    assert "graph" in targets
    assert "knowledge_base" in targets

    # Question separated by question marks
    q2 = "Какой добавочный номер у отдела кадров? Какие документы нужны для приёма на работу?"
    plan2 = heuristic_decompose(q2)
    assert plan2.is_multi_hop
    assert len(plan2.sub_queries) == 2


@pytest.mark.asyncio
async def test_decompose_query_with_mock_llm():
    # LLM returning JSON multi-hop decomposition
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = AIMessage(
        content='{"is_multi_hop": true, "sub_queries": [{"query": "начальник отдела ИБ", "target": "graph"}, {"query": "инструкция по реагированию на инциденты", "target": "knowledge_base"}]}'
    )

    plan = await decompose_query(
        "Кто начальник отдела ИБ и по какой инструкции сообщать об инцидентах?",
        llm=mock_llm,
    )
    assert plan.is_multi_hop
    assert len(plan.sub_queries) == 2
    assert plan.sub_queries[0].target == "graph"
    assert plan.sub_queries[1].target == "knowledge_base"


@pytest.mark.asyncio
async def test_execute_multi_hop_retrieval():
    @tool
    def search_knowledge_graph(query: str) -> tuple[str, dict]:
        """Search ontology graph."""
        return f"Граф: контакт {query}", {"sources": []}

    @tool
    def search_knowledge_base(query: str) -> str:
        """Search knowledge base."""
        return f"<context source=\"Регламент {query}\" doc_id=\"doc-1\">Текст инструкции</context>"

    tools = {
        "search_knowledge_graph": search_knowledge_graph,
        "search_knowledge_base": search_knowledge_base,
    }

    plan = MultiHopPlan(
        is_multi_hop=True,
        sub_queries=[
            SubQuery(query="директор ГАУЗ ОАС", target="graph"),
            SubQuery(query="возврат товара", target="knowledge_base"),
        ],
    )

    context_str, sources = await execute_multi_hop_retrieval(plan, tools)
    assert "Граф: контакт директор ГАУЗ ОАС" in context_str
    assert "Текст инструкции" in context_str


@pytest.mark.asyncio
async def test_supervisor_multi_hop_execution():
    @tool
    def search_knowledge_graph(query: str) -> tuple[str, dict]:
        """Search ontology graph."""
        return "Директор: Ступин Сергей Михайлович, приёмная доб. 111", {"sources": []}

    @tool
    def search_knowledge_base(query: str) -> str:
        """Search knowledge base."""
        return "<context source=\"Регламент возврата\" doc_id=\"doc-ret\">Порядок возврата товара надлежащего качества</context>"

    reply = "Директор — Ступин Сергей Михайлович (доб. 111). Возврат оформляется по регламенту возврата."
    llm = NoToolCallsModel(messages=cycle([AIMessage(content=reply)]))

    supervisor = build_supervisor_graph(
        llm=llm,
        tools=[search_knowledge_graph, search_knowledge_base],
        checkpointer=MemorySaver(),
    )

    res = await supervisor.ainvoke(
        {"messages": [HumanMessage(content="Кто директор ГАУЗ ОАС и как оформить возврат лекарственного средства?")]},
        context={"user_context": {"role": "employee"}},
        config={"configurable": {"thread_id": "t-multi-hop"}},
    )

    assert "Ступин Сергей Михайлович" in res["messages"][-1].content

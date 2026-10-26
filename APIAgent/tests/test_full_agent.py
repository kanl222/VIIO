import asyncio
from unittest.mock import MagicMock
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from apiagent.services.agent_service import AgentService
from apiagent.services.graph_builder import build_agent_graph
from apiagent.tools.search_tool import create_knowledge_search_tool

class ToolFakeChatModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self

def test_full_graph_with_tools():
    mock_store = MagicMock()
    mock_store.similarity_search.return_value = []
    tool = create_knowledge_search_tool(mock_store)

    # 1. LLM answers directly without tool
    llm1 = ToolFakeChatModel(messages=iter([AIMessage(content="Привет, я бот!")]))
    graph1 = build_agent_graph(llm=llm1, tools=[tool], checkpointer=MemorySaver())
    svc1 = AgentService(graph=graph1)

    reply, tid = asyncio.run(svc1.chat("Привет", thread_id="t-1", user_context={"role": "employee"}))
    assert reply == "Привет, я бот!"
    assert tid == "t-1"
    mock_store.similarity_search.assert_not_called()
    mock_store.reset_mock()

    # 2. LLM calls the tool search_knowledge_base
    tool_call_msg = AIMessage(
        content="",
        tool_calls=[{"name": "search_knowledge_base", "args": {"query": "регламент"}, "id": "call_1"}],
    )
    final_reply_msg = AIMessage(content="Согласно регламенту, все в порядке.")
    llm2 = ToolFakeChatModel(messages=iter([tool_call_msg, final_reply_msg]))

    graph2 = build_agent_graph(llm=llm2, tools=[tool], checkpointer=MemorySaver())
    svc2 = AgentService(graph=graph2)

    reply2, tid2 = asyncio.run(svc2.chat("Какой регламент?", thread_id="t-2", user_context={"role": "employee"}))
    assert reply2 == "Согласно регламенту, все в порядке."
    assert tid2 == "t-2"
    assert mock_store.similarity_search.call_count == 2
    assert mock_store.similarity_search.call_args.kwargs["filter"] is not None

if __name__ == "__main__":
    test_full_graph_with_tools()

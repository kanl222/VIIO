"""Verify text-only coder, delegation and separate persistent histories."""

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.documents import Document
from pydantic import Field
from langgraph.checkpoint.memory import MemorySaver

from assistent import create_app
from assistent.config import Settings
from assistent.services.agent_service import AgentService
from assistent.services.coding_agent import (
    CODING_SYSTEM_PROMPT, CodingAgentService, MultiAgentService,
    create_coding_delegation_tool, is_coding_request,
)
from assistent.services.graph_builder import build_agent_graph
from viio_documents.tools.search_tool import create_knowledge_search_tool

PROGRAMMER = {"user_id": "programmer-1", "role": "programmer"}
ADMIN = {"user_id": "admin-1", "role": "admin"}


class SupervisorModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def specialist(responses, saver=None):
    # GenericFakeChatModel cannot bind tools: a binding would fail this test.
    model = GenericFakeChatModel(messages=iter(responses))
    graph = build_agent_graph(model, tools=[], checkpointer=saver or MemorySaver(), system_prompt=CODING_SYSTEM_PROMPT)
    return CodingAgentService(graph, ["programmer", "admin"])


@pytest.mark.asyncio
async def test_coder_returns_code_without_creating_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code = "```python\ndef add(a, b):\n    return a + b\n```"
    coder = specialist([AIMessage(content=code)])
    general = MagicMock()
    reply, tid = await MultiAgentService(general, coder).chat("/code Создай sum.py", "outer", PROGRAMMER)
    assert reply == code and tid == "outer"
    assert list(tmp_path.iterdir()) == []
    general.chat.assert_not_called()


@pytest.mark.asyncio
async def test_json_tool_request_stays_plain_text(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    text = '{"name":"write_code_file","arguments":{"path":"bad.py","content":"pass"}}'
    reply, _ = await specialist([AIMessage(content=text)]).chat("Напиши JSON", "json", PROGRAMMER)
    assert reply == text
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_supervisor_delegates_and_coder_history_is_separate():
    saver = MemorySaver()
    coder = specialist([AIMessage(content="first code"), AIMessage(content="second code")], saver)
    tool = create_coding_delegation_tool(coder)
    def request(task, identifier):
        return AIMessage(content="", tool_calls=[{"name": "coding_agent", "args": {"task": task}, "id": identifier}])
    model = SupervisorModel(messages=iter([
        request("Напиши функцию", "one"), AIMessage(content="first result"),
        request("Добавь аргумент", "two"), AIMessage(content="second result"),
    ]))
    general = AgentService(build_agent_graph(model, [tool], saver))
    await general.chat("Функция", "parent", PROGRAMMER)
    await general.chat("Продолжи", "parent", PROGRAMMER)
    threads = {item.config["configurable"]["thread_id"] async for item in saver.alist(None)}
    coding_ids = [tid for tid in threads if tid.startswith("coder-text-")]
    assert len(coding_ids) == 1
    state = await coder._graph.aget_state({"configurable": {"thread_id": coding_ids[0]}})
    assert [m.content for m in state.values["messages"] if isinstance(m, HumanMessage)] == ["Напиши функцию", "Добавь аргумент"]
    assert all(m.type != "tool" for m in state.values["messages"])


@pytest.mark.asyncio
async def test_access_and_checkpoint_replacement():
    coder = specialist([AIMessage(content="code")])
    general = AgentService(build_agent_graph(GenericFakeChatModel(messages=iter([AIMessage(content="ok")]))))
    service = MultiAgentService(general, coder)
    saver = MemorySaver()
    service.set_checkpointer(saver)
    assert general.checkpointer is saver and coder.checkpointer is saver
    with pytest.raises(ValueError):
        await service.chat("/code", "thread", PROGRAMMER)
    denied, tid = await service.chat("/code Напиши функцию", "thread", {"user_id": "e", "role": "employee"})
    assert "Программист" in denied and tid == "thread"


@pytest.mark.asyncio
async def test_automatic_route():
    coder = specialist([AIMessage(content="SELECT id FROM products;")])
    general = MagicMock()
    reply, _ = await MultiAgentService(general, coder).chat("Напиши SQL SELECT", "auto", PROGRAMMER)
    assert "SELECT" in reply
    general.chat.assert_not_called()


@pytest.mark.parametrize("provider, client", [("lmstudio", "ChatOpenAI"), ("ollama", "ChatOllama")])
def test_factory_passes_no_tools_to_separate_coder_model(provider, client):
    settings = Settings(_env_file=None, llm_provider=provider, coding_model="dedicated-coder")
    with patch("assistent.models.chat." + client) as models, \
         patch("assistent.build_agent_graph") as graphs:
        create_app(settings=settings)
    general_name = settings.lmstudio_model if provider == "lmstudio" else settings.ollama_model
    assert [call.kwargs["model"] for call in models.call_args_list] == [general_name, settings.coding_model]
    assert graphs.call_args_list[0].kwargs["tools"] == []
    assert {t.name for t in graphs.call_args_list[1].kwargs["tools"]} == {"search_knowledge_base", "coding_agent"}


@pytest.mark.parametrize("message", ["Напиши SQL SELECT", "Создай функцию", "Исправь код", "Перепиши SOAP на REST API"])
def test_coding_intent(message):
    assert is_coding_request(message)


@pytest.mark.parametrize("message", ["Как узнать код рецепта?", "Что такое ИСЛО?"])
def test_domain_intent(message):
    assert not is_coding_request(message)


class CapturingModel(GenericFakeChatModel):
    seen: list = Field(default_factory=list)

    async def ainvoke(self, input, config=None, **kwargs):
        self.seen.append(input)
        return await super().ainvoke(input, config, **kwargs)


@pytest.mark.asyncio
async def test_project_reference_reaches_coder_with_current_acl_but_not_checkpoint():
    store = MagicMock()
    store.similarity_search.return_value = [Document(page_content="AvailableReports: report_type 1=SSRS, 2=ИСЛО", metadata={"title": "Справочник отчётов"})]
    search = create_knowledge_search_tool(store, k=2)
    model = CapturingModel(messages=iter([AIMessage(content="first code"), AIMessage(content="second code")]))
    saver = MemorySaver()
    graph = build_agent_graph(model, [], saver, CODING_SYSTEM_PROMPT)
    coder = CodingAgentService(graph, ["programmer"], knowledge_search=search)
    context = {**PROGRAMMER, "departments": ["it"]}
    await coder.chat("По базе знаний напиши код типов отчётов SSRSReport", "reference-chat", context)
    assert "AvailableReports" in str(model.seen[0])
    access_filter = store.similarity_search.call_args.kwargs["filter"]
    roles = next(item.match.any for item in access_filter.should if item.key == "metadata.allowed_roles")
    assert roles == ["programmer", "employee"]
    assert "admin" not in roles and "manager" not in roles
    store.similarity_search.return_value = []
    await coder.chat("Добавь комментарий", "reference-chat", {**context, "departments": []})
    assert store.similarity_search.call_count == 2
    assert "AvailableReports" not in str(model.seen[1])
    assert "Ничего не найдено" in str(model.seen[1])
    threads = {item.config["configurable"]["thread_id"] async for item in saver.alist(None)}
    state = await graph.aget_state({"configurable": {"thread_id": threads.pop()}})
    assert "AvailableReports" not in str(state.values["messages"])
    assert all(m.type != "tool" for m in state.values["messages"])
    assert state.values["messages"][1].response_metadata["knowledge_reference_chars"] > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["employee", "manager", "guest"])
async def test_coder_rejects_other_roles_before_any_search(role):
    search = MagicMock()
    coder = specialist([AIMessage(content="must not run")])
    coder.knowledge_search = search
    with pytest.raises(PermissionError, match="Программист"):
        await coder.chat("По базе знаний напиши SQL", "denied", {"user_id": "u", "role": role})
    search.ainvoke.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("context", [PROGRAMMER, ADMIN])
@pytest.mark.parametrize("route", ["direct", "automatic", "delegated"])
async def test_programmer_and_admin_can_use_text_only_coder(context, route):
    code = "```python\nanswer = 42\n```"
    coder = specialist([AIMessage(content=code)])
    general = MagicMock()
    service = MultiAgentService(general, coder)
    if route == "delegated":
        reply = await create_coding_delegation_tool(coder).ainvoke(
            {"task": "Напиши Python"},
            config={"configurable": {"user_context": context, "thread_id": "access"}},
        )
    else:
        message = "/code Напиши Python" if route == "direct" else "Напиши Python"
        reply, _ = await service.chat(message, "access", context)
    assert reply == code
    general.chat.assert_not_called()


@pytest.mark.asyncio
async def test_admin_can_be_explicitly_excluded_by_configuration():
    coder = specialist([AIMessage(content="must not run")])
    coder.allowed_roles = ["programmer"]
    with pytest.raises(PermissionError):
        await coder.chat("Напиши Python", "excluded", ADMIN)


@pytest.mark.asyncio
@pytest.mark.parametrize("context", [None, {"role": "admin"}, {"user_id": "u", "role": "employee"}])
async def test_invalid_context_or_unsupported_role_cannot_bypass_access(context):
    coder = specialist([AIMessage(content="must not run")])
    coder.allowed_roles = ["programmer", "admin", "employee"]
    coder.knowledge_search = MagicMock()
    with pytest.raises(PermissionError):
        await coder.chat("По базе знаний напиши Python", "invalid", context)
    coder.knowledge_search.ainvoke.assert_not_called()


def test_default_coder_roles_include_admin_and_programmer():
    assert set(Settings(_env_file=None).coding_allowed_roles) == {"programmer", "admin"}


@pytest.mark.asyncio
async def test_explicit_knowledge_query_can_be_delegated_to_text_only_coder():
    store = MagicMock()
    store.similarity_search.return_value = [Document(page_content="field_name", metadata={"title": "Schema"})]
    model = CapturingModel(messages=iter([AIMessage(content="code")]))
    coder = CodingAgentService(build_agent_graph(model, [], MemorySaver(), CODING_SYSTEM_PROMPT), ["programmer"], create_knowledge_search_tool(store))
    tool = create_coding_delegation_tool(coder)
    result = await tool.ainvoke({"task": "Напиши пример", "knowledge_query": "схема отчёта"}, config={"configurable": {"thread_id": "query", "user_context": PROGRAMMER}})
    assert result == "code"
    assert store.similarity_search.call_args.kwargs["query"] == "схема отчёта"
    assert "field_name" in str(model.seen[0])


@pytest.mark.asyncio
async def test_search_error_does_not_produce_ungrounded_project_code():
    store = MagicMock()
    store.similarity_search.side_effect = RuntimeError("offline")
    model = CapturingModel(messages=iter([AIMessage(content="must not run")]))
    coder = CodingAgentService(build_agent_graph(model, [], MemorySaver(), CODING_SYSTEM_PROMPT), ["programmer"], create_knowledge_search_tool(store))
    result, _ = await coder.chat("По базе знаний напиши SQL", "offline", PROGRAMMER)
    assert "Не удалось получить" in result
    assert not model.seen


def test_documentation_only_request_does_not_invoke_coder():
    assert not is_coding_request("Как добавить новый отчёт в модуль SSRSReport? Опиши шаги по документам, код писать не нужно.")


@pytest.mark.asyncio
async def test_named_document_is_searched_by_title_and_neighbors_are_excluded():
    store = MagicMock()
    store.similarity_search.return_value = [
        Document(page_content="wrong schema", metadata={"title": "Соседний документ"}),
        Document(page_content="report_type 1 2", metadata={"title": "Добавление нового отчета в SSRSReport"}),
    ]
    model = CapturingModel(messages=iter([AIMessage(content="code\nИсточник: <knowledge_reference>")]))
    coder = CodingAgentService(build_agent_graph(model, [], MemorySaver(), CODING_SYSTEM_PROMPT), ["programmer"], create_knowledge_search_tool(store))
    result, _ = await coder.chat('По документу «Добавление нового отчёта в SSRSReport» напиши код', "title", PROGRAMMER)
    assert store.similarity_search.call_args.kwargs["query"] == "Добавление нового отчёта в SSRSReport"
    assert "report_type 1 2" in str(model.seen[0])
    assert "wrong schema" not in str(model.seen[0])
    assert "Источник: Добавление нового отчета в SSRSReport" in result
    assert "<knowledge_reference>" not in result


@pytest.mark.asyncio
async def test_missing_named_document_does_not_substitute_neighboring_document():
    store = MagicMock()
    store.similarity_search.return_value = [Document(page_content="other data", metadata={"title": "Другой"})]
    model = CapturingModel(messages=iter([AIMessage(content="must not run")]))
    coder = CodingAgentService(build_agent_graph(model, [], MemorySaver(), CODING_SYSTEM_PROMPT), ["programmer"], create_knowledge_search_tool(store))
    result, _ = await coder.chat('Напиши по документу «Нужный документ»', "missing", PROGRAMMER)
    assert "не найден среди доступных результатов" in result
    assert not model.seen

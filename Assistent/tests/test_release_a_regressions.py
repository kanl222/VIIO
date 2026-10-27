"""Replay failures observed in the live evaluation at the real service seams."""

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, SystemMessage
from assistent.prompts import REWRITE_SYSTEM_PROMPT
from langchain_core.documents import Document
from langgraph.checkpoint.memory import MemorySaver

from assistent.services.agent_service import AgentService
from assistent.services.coding_agent import CodingAgentService, MultiAgentService, is_coding_request
from assistent.services.graph_builder import build_agent_graph
from viio_documents.tools.search_tool import create_knowledge_search_tool
from test_coding_agent import CapturingModel, PROGRAMMER
from test_required_retrieval import NoToolCallsModel
from assistent.services.request_policy import calculate
from assistent.services.coding_agent import create_coding_delegation_tool
from assistent.services.answer_sources import AgentReply, clean_answer


def test_live_business_report_with_negative_code_instruction():
    assert not is_coding_request(
        "Оптимизировать отчёт о продажах: предложи три изменения структуры, "
        "чтобы директор быстрее видел итог. Код не нужен."
    )


@pytest.mark.asyncio
async def test_short_coder_followup_survives_service_recreation():
    saver = MemorySaver()
    model = CapturingModel(messages=iter([AIMessage(content="first"), AIMessage(content="updated")]))
    coder = CodingAgentService(build_agent_graph(model, [], saver), ["programmer"])
    general = AgentService(build_agent_graph(NoToolCallsModel(messages=iter([AIMessage(content="wrong route")])) , [], saver))
    await MultiAgentService(general, coder).chat("/code Напиши multiply(a,b) на Python", "continuation", PROGRAMMER)
    reply, _ = await MultiAgentService(general, coder).chat(
        "Добавь проверку отрицательных значений аргументов и возвращай None для них.",
        "continuation", PROGRAMMER,
    )
    assert reply == "updated"
    assert "multiply" in str(model.seen[-1])


@pytest.mark.asyncio
async def test_arithmetic_does_not_use_model_or_knowledge():
    store = MagicMock()
    model = NoToolCallsModel(messages=iter([AIMessage(content="401")]))
    service = AgentService(build_agent_graph(model, [create_knowledge_search_tool(store)], MemorySaver()))
    reply, _ = await service.chat("Вычисли 17 * 23. Дай только число.", "arithmetic", PROGRAMMER)
    assert reply == "391"
    store.similarity_search.assert_not_called()


@pytest.mark.asyncio
async def test_rewrite_does_not_receive_knowledge_or_citation_instructions():
    store = MagicMock()
    model = CapturingModel(messages=iter([AIMessage(content="Пожалуйста, пришлите отчёт сегодня.")]))
    # This model deliberately has no bind_tools: text transformations need none.
    service = AgentService(build_agent_graph(model, [], MemorySaver()))
    await service.chat("Перепиши вежливо одним предложением: пришли отчёт сегодня, ты опять задерживаешь.", "rewrite", PROGRAMMER)
    assert [message.content for message in model.seen[-1] if isinstance(message, SystemMessage)] == [REWRITE_SYSTEM_PROMPT]
    assert '<knowledge_reference>' not in str(model.seen[-1])


@pytest.mark.parametrize("expression,expected", [
    ("17 × 23", "391"), ("Вычисли (2 + 3) * -4", "-20"), ("0.1 + 0.2", "0.3"),
    ("Вычисли 10 / 4", "2.5"), ("2 - 2", "0"), ("7 % 3", "1"),
])
def test_supported_arithmetic(expression, expected):
    assert calculate(expression) == expected


@pytest.mark.parametrize("expression", ["2**100000", "1 / 0", "Вычисли " + "(" * 40 + "1" + ")" * 40, "9" * 300 + "+1"])
def test_arithmetic_limits_are_clear_errors(expression):
    assert "Не удалось вычислить" in calculate(expression)


@pytest.mark.parametrize("message", ["Покажи документ 2026-10-27", "__import__('os').system('echo x')", "Вычисли цену лицензии 2031"])
def test_other_content_is_not_executed_as_arithmetic(message):
    assert calculate(message) is None


@pytest.mark.asyncio
async def test_new_topic_resets_coder_and_denied_followup_does_not_run_model():
    saver = MemorySaver()
    model = CapturingModel(messages=iter([AIMessage(content="code")]))
    coder = CodingAgentService(build_agent_graph(model, [], saver), ["programmer"])
    general_model = NoToolCallsModel(messages=iter([AIMessage(content="general"), AIMessage(content="general followup")]))
    service = MultiAgentService(AgentService(build_agent_graph(general_model, [], saver)), coder)
    await service.chat("/code Напиши функцию", "switch", PROGRAMMER)
    denied, _ = await service.chat("Добавь проверку", "switch", {**PROGRAMMER, "role": "employee"})
    assert "Программист" in denied and len(model.seen) == 1
    answer, _ = await service.chat("Почему зимой день короче?", "switch", PROGRAMMER)
    assert answer == "general"
    answer, _ = await service.chat("Добавь подробностей", "switch", PROGRAMMER)
    assert answer == "general followup" and len(model.seen) == 1


@pytest.mark.asyncio
async def test_coder_history_is_not_reused_after_acl_change():
    saver = MemorySaver()
    model = CapturingModel(messages=iter([AIMessage(content="old confidential answer"), AIMessage(content="new")]))
    coder = CodingAgentService(build_agent_graph(model, [], saver), ["programmer"])
    await coder.chat("Напиши функцию с SECRET_INTERNAL_CONSTANT", "acl", {**PROGRAMMER, "departments": ["restricted"]})
    await coder.chat("Напиши функцию суммы", "acl", {**PROGRAMMER, "departments": []})
    assert "SECRET_INTERNAL_CONSTANT" not in str(model.seen[-1])
    assert "old confidential answer" not in str(model.seen[-1])


@pytest.mark.asyncio
async def test_sources_are_separate_and_internal_labels_do_not_reach_model_or_reply():
    store = MagicMock()
    store.similarity_search.return_value = [Document(page_content="dateStart имеет тип date", metadata={"title": 'ИС "ЛО"', "document_id": "doc-private"})]

    class Model(CapturingModel):
        def bind_tools(self, tools, **kwargs):
            return self

    model = Model(messages=iter([AIMessage(content="dateStart — date.\nДокумент: knowledge_reference\nchunk_index: 7")]))
    service = AgentService(build_agent_graph(model, [create_knowledge_search_tool(store)], MemorySaver()))
    answer, _ = await service.chat("Какие поля в ИСЛО?", "sources", PROGRAMMER)
    assert answer == "dateStart — date."
    assert answer.sources == [{"title": 'ИС "ЛО"', "document_id": "doc-private"}]
    assert 'doc-private' not in str(model.seen[0])
    assert 'chunk=' not in str(model.seen[0])
    assert 'dateStart' in str(model.seen[0])


@pytest.mark.asyncio
async def test_delegated_coder_keeps_sources_and_active_route_for_next_turn():
    saver = MemorySaver()
    store = MagicMock()
    store.similarity_search.return_value = [Document(page_content="active=7", metadata={"title": "Статусы", "document_id": "doc-status"})]
    coder_model = CapturingModel(messages=iter([AIMessage(content="statuses={'active':7}"), AIMessage(content="updated code")]))
    coder = CodingAgentService(build_agent_graph(coder_model, [], saver, "Только код"), ["programmer"], create_knowledge_search_tool(store))
    supervisor = NoToolCallsModel(messages=iter([
        AIMessage(content="", tool_calls=[{"name": "coding_agent", "args": {"task": "Напиши словарь", "knowledge_query": "Статусы"}, "id": "delegate"}]),
        AIMessage(content="statuses={'active':7}"),
    ]))
    general = AgentService(build_agent_graph(supervisor, [create_coding_delegation_tool(coder)], saver))
    service = MultiAgentService(general, coder)
    answer, _ = await service.chat("Помоги с реализацией", "delegation", PROGRAMMER)
    assert answer.sources == [{"title": "Статусы", "document_id": "doc-status"}]
    answer, _ = await service.chat("Добавь второй аргумент", "delegation", PROGRAMMER)
    assert answer == "updated code"


@pytest.mark.asyncio
async def test_quoted_title_reaches_coder_without_html_entities():
    store = MagicMock()
    store.similarity_search.return_value = [Document(page_content="field=1", metadata={"title": 'Поля ИС "ЛО"', "document_id": "doc-fields"})]
    model = CapturingModel(messages=iter([AIMessage(content="field=1")]))
    coder = CodingAgentService(build_agent_graph(model, [], MemorySaver(), "Только код"), ["programmer"], create_knowledge_search_tool(store))
    answer, _ = await coder.chat('По документу «Поля ИС "ЛО"» напиши код', "quoted", PROGRAMMER)
    assert answer.sources[0]["title"] == 'Поля ИС "ЛО"'
    assert '&quot;' not in str(model.seen[0])


def test_source_cleanup_preserves_requested_program_code():
    code = '```python\nchunk_index = 7\nknowledge_reference = "input"\n```'
    assert clean_answer(code + '\nДокумент: knowledge_reference\nЧанк №7: служебная запись', []) == code


def test_api_sources_are_structured_and_reply_remains_compatible():
    from fastapi.testclient import TestClient
    from assistent import create_app
    from assistent.config import Settings
    from test_api import MockDocumentService

    class Service:
        async def chat(self, message, thread_id=None, user_context=None):
            return AgentReply("Ответ", [{"title": "Инструкция", "document_id": "doc-source", "url": "javascript:bad", "chunk_index": "7"}]), "thread"

    app = create_app(settings=Settings(_env_file=None, internal_api_key="release-test"), agent_service=Service(),
                     checkpointer=MemorySaver())
    result = TestClient(app).post("/api/v1/chat", headers={"X-Internal-Token": "release-test"}, json={"message": "Вопрос"})
    assert result.status_code == 200
    assert result.json() == {"reply": "Ответ", "thread_id": "thread", "sources": [{"title": "Инструкция", "document_id": "doc-source"}]}

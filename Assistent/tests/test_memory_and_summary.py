from unittest.mock import AsyncMock
import pytest
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from assistent.services.dialogue_summary import compact_dialogue
from assistent.services.personal_memory import MemoryAgent
from viio_documents.services.code_documents import split_code
from viio_documents.services.enhanced_retrieval import rank_documents
from langchain_core.documents import Document


def test_source_chunks_preserve_every_line_and_python_boundaries():
    content = 'import os\n\ndef first():\n    return 1\n\ndef second():\n    return 2\n'
    parts = split_code(content, 'test.py')
    assert ''.join(text for text, _, _ in parts) == content
    assert any(text.startswith('def second') and start == 6 for text, start, _ in parts)
    assert all(len(text) <= 2000 for text, _, _ in split_code('x' * 9000, 'test.ts'))


def test_reranking_prefers_exact_identifiers_and_diverse_sources():
    docs = [Document(page_content='unrelated', metadata={'document_id': 'a'}),
            Document(page_content='def CalculateInvoiceTotal(): pass', metadata={'document_id': 'b'}),
            Document(page_content='CalculateInvoiceTotal duplicate', metadata={'document_id': 'b'})]
    ranked = rank_documents('CalculateInvoiceTotal', docs, 2)
    assert [doc.metadata['document_id'] for doc in ranked] == ['b', 'a']


@pytest.mark.asyncio
async def test_summary_excludes_tools_and_is_not_rebuilt_every_turn():
    messages = []
    for i in range(15):
        messages.extend([HumanMessage(content=f'Goal {i}'), AIMessage(content=f'Answer {i}')])
    messages.insert(3, ToolMessage(content='PRIVATE_TOOL_PAYLOAD', tool_call_id='call'))
    llm = AsyncMock()
    llm.ainvoke.return_value = AIMessage(content='Summary of user goals')
    boundary = {'history_scope': 'owner'}
    state = {'history_scope': 'owner'}
    prepared, update = await compact_dialogue(messages, state, boundary, llm, 24)
    assert update['summary_upto'] > 0 and 'Summary of user goals' in prepared[0].content
    assert 'PRIVATE_TOOL_PAYLOAD' not in str(llm.ainvoke.call_args)
    assert len(messages) == 31  # Full audit/checkpoint was not modified.
    llm.reset_mock()
    await compact_dialogue(messages + [HumanMessage(content='Next')], {**state, **update}, boundary, llm, 24)
    llm.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_summary_scope_change_never_reuses_privileged_summary():
    llm = AsyncMock()
    prepared, update = await compact_dialogue([HumanMessage(content='hello')],
        {'history_scope': 'admin', 'dialogue_summary': 'ADMIN_SECRET', 'summary_upto': 99},
        {'history_scope': 'employee'}, llm, 24)
    assert 'ADMIN_SECRET' not in str(prepared)
    assert update == {'dialogue_summary': '', 'summary_upto': 0}


@pytest.mark.asyncio
async def test_few_large_turns_summarize_before_trimming_user_goal():
    llm = AsyncMock()
    llm.ainvoke.return_value = AIMessage(content='Goal preserved')
    messages = [HumanMessage(content='Project goal: invoices'), AIMessage(content='x' * 6000),
                HumanMessage(content='Second step'), AIMessage(content='Acknowledged'), HumanMessage(content='Continue')]
    prepared, update = await compact_dialogue(messages, {}, {'history_scope': 'owner'}, llm, 24, 4000)
    assert update['summary_upto'] == 2
    assert 'Goal preserved' in prepared[0].content
    assert 'Project goal: invoices' in str(llm.ainvoke.call_args)


@pytest.mark.asyncio
async def test_memory_commands_are_explicit_and_untrusted_client_notes_are_removed():
    agent, facts = AsyncMock(), AsyncMock()
    facts.remember.return_value = 'id'
    facts.list.return_value = [{'id': 'id', 'content': 'Prefer Python'}]
    agent.chat.return_value = ('ok', 'thread')
    service = MemoryAgent(agent, facts)
    context = {'user_id': 'u1', 'role': 'programmer', '_user_notes': 'FORGED'}
    reply, _ = await service.chat('/remember Prefer Python', 'thread', context)
    assert 'id' in reply
    agent.chat.assert_not_called()
    await service.chat('Write a function', 'thread', context)
    forwarded = agent.chat.call_args.args[2]
    assert forwarded['_user_notes'] == 'Prefer Python' and 'FORGED' not in str(forwarded)
    with pytest.raises(PermissionError):
        await service.chat('/facts', 'thread', {})


@pytest.mark.asyncio
async def test_graph_persists_summary_and_clears_it_on_scope_change():
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langgraph.checkpoint.memory import MemorySaver
    from assistent.services.graph_builder import build_agent_graph
    saver = MemorySaver()
    model = FakeListChatModel(responses=['Brief response'])
    graph = build_agent_graph(model, checkpointer=saver, system_prompt='Test assistant', summary_after=12)
    config = {'configurable': {'thread_id': 'summary-thread'}}
    for number in range(15):
        await graph.ainvoke({'messages': [('user', f'Goal number {number}')]}, config,
                            context={'access_scope': 'programmer'})
    saved = await graph.aget_state(config)
    assert saved.values['dialogue_summary'] == 'Brief response'
    assert saved.values['summary_upto'] > 0 and len(saved.values['messages']) == 30
    rebuilt = build_agent_graph(model, checkpointer=saver, system_prompt='Test assistant', summary_after=12)
    assert (await rebuilt.aget_state(config)).values['dialogue_summary'] == 'Brief response'
    await rebuilt.ainvoke({'messages': [('user', 'New role')]}, config, context={'access_scope': 'employee'})
    assert (await rebuilt.aget_state(config)).values['dialogue_summary'] == ''

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage,AIMessage
from assistent import create_app
from assistent.config import Settings
from assistent.services.graph_builder import history_for_turn
from viio_documents.config import Settings as DocumentSettings
from viio_documents.security import authorize

KEYS=dict(documents_read_key='r'*40,documents_admin_key='a'*40,document_worker_key='w'*40,consultant_source_key='s'*40)

@pytest.mark.parametrize('token,path,allowed',[
    ('r','/api/v1/knowledge/search',True),('r','/api/v1/knowledge/access-revision',True),
    ('r','/api/v1/documents/download/id',True),('r','/api/v1/documents',False),
    ('r','/api/v1/documents/bulk-upload',False),('r','/api/v1/documents/publication/id',False),
    ('s','/api/v1/documents/catalog',False),('w','/api/v1/knowledge/search',False),
    ('a','/api/v1/documents',True),('a','/api/v1/documents/publication/id',True),
    ('c','/api/v1/knowledge/search',False),('','/api/v1/documents',False),
])
def test_service_capability_matrix(token,path,allowed):
    settings=DocumentSettings(_env_file=None,secure_runtime=True,**KEYS)
    request=SimpleNamespace(url=SimpleNamespace(path=path),headers={'X-Internal-Token':token*40})
    if allowed:authorize(request,settings)
    else:
        with pytest.raises(HTTPException):authorize(request,settings)

def test_worker_requires_its_own_key():
    s=DocumentSettings(_env_file=None,service_role='document-worker',secure_runtime=True,**KEYS)
    for token in ['r','a','s','']:
        with pytest.raises(HTTPException):authorize(SimpleNamespace(url=SimpleNamespace(path='/api/v1/documents'),headers={'X-Internal-Token':token*40}),s)
    authorize(SimpleNamespace(url=SimpleNamespace(path='/api/v1/documents'),headers={'X-Internal-Token':'w'*40}),s)

def test_previous_read_key_keeps_only_read_scope_during_rotation():
    s=DocumentSettings(_env_file=None,secure_runtime=True,documents_read_key_previous='p'*40,**KEYS)
    s.validate_runtime()
    authorize(SimpleNamespace(url=SimpleNamespace(path='/api/v1/knowledge/search'),headers={'X-Internal-Token':'p'*40}),s)
    with pytest.raises(HTTPException):authorize(SimpleNamespace(url=SimpleNamespace(path='/api/v1/documents'),headers={'X-Internal-Token':'p'*40}),s)

def test_unconfigured_document_key_fails_closed():
    with pytest.raises(HTTPException) as error:
        authorize(SimpleNamespace(url=SimpleNamespace(path='/api/v1/documents'),headers={}),DocumentSettings(_env_file=None))
    assert error.value.status_code==503

def test_production_rejects_missing_or_equal_keys():
    with pytest.raises(ValueError):create_app(Settings(_env_file=None,secure_runtime=True),agent_service=AsyncMock())
    with pytest.raises(ValueError):create_app(Settings(_env_file=None,secure_runtime=True,chat_api_key='k'*40,documents_read_key='k'*40),agent_service=AsyncMock())

def test_mid_generation_revocation_suppresses_answer(monkeypatch):
    epoch=AsyncMock(side_effect=['a'*32,'b'*32])
    monkeypatch.setattr('assistent.router.access_revision',epoch)
    service=SimpleNamespace(chat=AsyncMock(return_value=('PRIVATE OLD ANSWER','thread')))
    app=create_app(Settings(_env_file=None,secure_runtime=True,chat_api_key='c'*40,documents_read_key='r'*40),agent_service=service)
    response=TestClient(app).post('/api/v1/chat',headers={'X-Internal-Token':'c'*40},json={'message':'вопрос'})
    assert response.status_code==200
    assert 'PRIVATE' not in response.text and response.json()['sources']==[]
    assert service.chat.call_args.args[2]['_knowledge_epoch']=='a'*32

def test_revision_outage_prevents_generation(monkeypatch):
    monkeypatch.setattr('assistent.router.access_revision',AsyncMock(side_effect=HTTPException(503,'Unavailable')))
    service=SimpleNamespace(chat=AsyncMock())
    app=create_app(Settings(_env_file=None,secure_runtime=True,chat_api_key='c'*40,documents_read_key='r'*40),agent_service=service)
    response=TestClient(app).post('/api/v1/chat',headers={'X-Internal-Token':'c'*40},json={'message':'вопрос'})
    assert response.status_code==503 and not service.chat.called

def test_new_access_revision_clears_old_tool_and_answer_history():
    history=[HumanMessage(content='старый вопрос'),AIMessage(content='PRIVATE'),HumanMessage(content='новый вопрос')]
    prepared,boundary=history_for_turn({'messages':history,'history_scope':'user:old','history_start':0},
        SimpleNamespace(context={'access_scope':'user:new'}))
    assert prepared==[history[-1]] and 'PRIVATE' not in str(prepared)
    assert boundary['history_scope']=='user:new'

def test_packages_do_not_import_each_other():
    root=Path(__file__).resolve().parents[2]
    for folder,forbidden in [('Assistent','viio_documents'),('Documents','assistent')]:
        for path in (root/folder/'src').rglob('*.py'):
            text=path.read_text(encoding='utf-8')
            assert f'from {forbidden}' not in text and f'import {forbidden}' not in text,path

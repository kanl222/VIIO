import pytest
from fastapi.testclient import TestClient

from assistent import create_app
from assistent.config import Settings
from assistent.application.supervisor_graph import classify_route
from test_api import MockAgentService


@pytest.mark.parametrize('mode,question,expected', [
    ('assistant', 'Покажи таблицы PostgreSQL', 'knowledge'),
    ('coding', '2 + 2', 'coding'),
    ('database', 'Напиши код на Python', 'database'),
])
def test_manual_mode_overrides_question_classification(mode, question, expected):
    assert classify_route(question, {'postgresql_readonly'},
                          {'user_id': 'test-user', 'role': 'programmer', '_agent_mode': mode}) == expected


@pytest.mark.parametrize('mode', ['coding', 'database'])
def test_manual_specialist_does_not_bypass_roles(mode):
    assert classify_route('Запрос', {'postgresql_readonly'},
                          {'role': 'employee', '_agent_mode': mode}) == 'knowledge'


@pytest.mark.parametrize('mode', ['assistant', 'coding', 'database'])
def test_api_forwards_selected_mode_without_changing_message(mode):
    agent = MockAgentService()
    settings = Settings(_env_file=None, chat_api_key='test-key', pgsql_enabled=True)
    client = TestClient(create_app(settings, agent_service=agent))
    response = client.post('/api/v1/chat', headers={'X-Internal-Token': 'test-key'}, json={
        'message': 'Мой вопрос', 'agent_mode': mode,
        'user_context': {'role': 'programmer', 'user_id': 'test-user'},
    })
    assert response.status_code == 200
    assert response.json()['reply'] == 'Эхо: Мой вопрос'
    assert agent.last_user_context['_agent_mode'] == mode


def test_api_rejects_database_mode_for_employee():
    agent = MockAgentService()
    client = TestClient(create_app(Settings(_env_file=None, chat_api_key='test-key'), agent_service=agent))
    response = client.post('/api/v1/chat', headers={'X-Internal-Token': 'test-key'}, json={
        'message': 'Покажи таблицы', 'agent_mode': 'database',
        'user_context': {'role': 'employee'},
    })
    assert response.status_code == 403

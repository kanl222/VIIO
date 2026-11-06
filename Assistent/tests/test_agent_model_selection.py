from unittest.mock import patch
import pytest
from assistent.config import Settings
from assistent.models.chat import create_chat_model
from assistent.models.coder import create_coding_model


@pytest.mark.parametrize('role', ['chat', 'coding', 'database'])
def test_independent_agent_selection(role):
    settings = Settings(_env_file=None, llm_provider='lmstudio',
        **{f'{role}_provider': 'ollama', f'{role}_model': 'selected-model'})
    with patch('assistent.models.chat.ChatOllama') as ollama, patch('assistent.models.chat.ChatOpenAI') as openai:
        if role == 'coding':
            create_coding_model(settings)
        else:
            create_chat_model(settings, agent=role)
        assert ollama.call_args.kwargs['model'] == 'selected-model'
        openai.assert_not_called()


def test_default_model_and_explicit_override():
    settings = Settings(_env_file=None, llm_provider='lmstudio', lmstudio_model='default-model')
    with patch('assistent.models.chat.ChatOpenAI') as model:
        create_chat_model(settings)
        assert model.call_args.kwargs['model'] == 'default-model'
        create_chat_model(settings, model_name='override')
        assert model.call_args.kwargs['model'] == 'override'


def test_yandex_requires_explicit_credentials():
    for field in ('chat_provider', 'coding_provider', 'database_provider'):
        with pytest.raises(ValueError):
            Settings(_env_file=None, **{field: 'yandex'})

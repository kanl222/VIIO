from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from assistent.config import Settings
from assistent.router import create_api_router
from assistent.services.token_usage import TokenUsageCounter


def result(usage=None, raw=None):
    return LLMResult(generations=[[ChatGeneration(message=AIMessage(content='Answer', usage_metadata=usage))]],
                     llm_output=raw)


def test_provider_usage_aggregates_across_concurrent_model_calls():
    counter = TokenUsageCounter()
    response = result(dict(input_tokens=20, output_tokens=5, total_tokens=25))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: counter.on_llm_end(response), range(30)))
    usage = counter.snapshot()
    assert usage['input_tokens'] == 600
    assert usage['output_tokens'] == 150
    assert usage['total_tokens'] == 750
    assert usage['reported_calls'] == 30
    assert usage['unreported_calls'] == 0


def test_missing_usage_is_not_estimated_and_raw_openai_usage_is_supported():
    counter = TokenUsageCounter()
    counter.on_llm_end(result())
    assert counter.snapshot()['total_tokens'] == 0
    assert counter.snapshot()['unreported_calls'] == 1
    counter.on_llm_end(result(raw={'token_usage': {'prompt_tokens': 10, 'completion_tokens': 3}}))
    assert counter.snapshot()['total_tokens'] == 13
    assert counter.snapshot()['reported_calls'] == 1


def test_health_returns_selected_provider_model_and_usage_requires_service_key(monkeypatch):
    counter = TokenUsageCounter()
    monkeypatch.setattr('assistent.services.token_usage.token_usage', counter)
    settings = Settings(_env_file=None, llm_provider='yandex', yandex_api_key='test-key',
                        yandex_folder_id='test-folder', chat_model='selected-model', chat_api_key='test-chat-key')
    app = FastAPI()
    app.include_router(create_api_router(None, settings))
    client = TestClient(app)
    health = client.get('/health').json()
    assert health['provider'] == 'yandex' and health['model'] == 'selected-model'
    assert 'test-key' not in str(health)
    assert client.get('/api/v1/usage').status_code == 401
    assert client.get('/api/v1/usage', headers={'X-Internal-Token': 'wrong'}).status_code == 401
    response = client.get('/api/v1/usage', headers={'X-Internal-Token': 'test-chat-key'})
    assert response.status_code == 200 and response.json()['total_tokens'] == 0


def test_model_factory_attaches_usage_callback():
    from assistent.models.chat import create_chat_model
    from assistent.services.token_usage import token_usage
    model = create_chat_model(Settings(_env_file=None))
    assert token_usage in model.callbacks

from unittest.mock import patch

import httpx
import pytest
from langchain_core.messages import HumanMessage
from pydantic import ValidationError

from assistent.config import Settings
from assistent.infrastructure.llm.chat import create_chat_model


@pytest.mark.parametrize("role", ["chat", "coding", "database"])
def test_global_yandex_and_local_override(role):
    settings = Settings(_env_file=None, llm_provider="yandex",
                        yandex_api_key="test-key", yandex_folder_id="folder")
    with patch("assistent.infrastructure.llm.chat.ChatOpenAI") as constructor:
        create_chat_model(settings, coding=role == "coding", agent=role)
        assert constructor.call_args.kwargs["model"] == "gpt://folder/aliceai-llm"
    overridden = settings.model_copy(update={f"{role}_provider": "ollama"})
    with patch("assistent.infrastructure.llm.chat.ChatOllama") as constructor:
        create_chat_model(overridden, coding=role == "coding", agent=role)
        constructor.assert_called_once()


def test_global_cloud_credentials_validated_for_inherited_roles():
    with pytest.raises(ValidationError, match="YANDEX_API_KEY"):
        Settings(_env_file=None, llm_provider="yandex")
    # Облачный клиент не используется, если для каждой роли явно выбран локальный провайдер.
    Settings(_env_file=None, llm_provider="yandex", chat_provider="lmstudio",
             coding_provider="ollama", database_provider="lmstudio")


@pytest.mark.parametrize("role", ["chat", "coding", "database"])
@pytest.mark.parametrize("name,expected", [
    (None, "gpt://folder/aliceai-llm"),
    ("yandexgpt/latest", "gpt://folder/yandexgpt/latest"),
    ("gpt://other/custom/latest", "gpt://other/custom/latest"),
    ("qwen/qwen3-vl-8b", "gpt://folder/aliceai-llm"),
    ("llama3.1", "gpt://folder/aliceai-llm"),
])
def test_cloud_selected_independently(role, name, expected):
    settings = Settings(_env_file=None, yandex_api_key="test-key", yandex_folder_id="folder",
                        **{f"{role}_provider": "yandex", f"{role}_model": name},
                        coding_temperature=0.1, coding_max_tokens=3000)
    with patch("assistent.infrastructure.llm.chat.ChatOpenAI") as constructor:
        create_chat_model(settings, coding=role == "coding", agent=role)
        args = constructor.call_args.kwargs
        assert args["model"] == expected
        assert args["base_url"] == "https://ai.api.cloud.yandex.net/v1"
        assert args["default_headers"]["Authorization"] == "Api-Key test-key"
        assert args["default_headers"]["OpenAI-Project"] == "folder"
        assert args["max_tokens"] == (3000 if role == "coding" else 2048)
        assert args["max_retries"] == 0
        assert "top_p" not in args
        if role != "chat":
            create_chat_model(settings)
            assert constructor.call_args.kwargs["base_url"] == settings.lmstudio_base_url


@pytest.mark.parametrize("extra", [
    {"yandex_api_key": ""}, {"yandex_folder_id": " "}, {"yandex_model": " "},
])
def test_invalid_cloud_settings_fail_without_secrets(extra):
    values = dict(coding_provider="yandex", yandex_api_key="test-key", yandex_folder_id="folder")
    values.update(extra)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)
    assert "test-key" not in repr(Settings(_env_file=None, yandex_api_key="test-key"))


@pytest.mark.asyncio
async def test_wire_format_and_tool_call_roundtrip():
    import json
    from langchain_core.messages import ToolMessage

    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.url == "https://ai.api.cloud.yandex.net/v1/chat/completions"
        assert request.headers["Authorization"] == "Api-Key test-key"
        assert request.headers["OpenAI-Project"] == "folder"
        assert body["model"] == "gpt://folder/aliceai-llm"
        if len(requests) == 1:
            assert body["tools"][0]["function"]["name"] == "read_project"
            message = {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call-1", "type": "function", "function": {
                    "name": "read_project", "arguments": "{}"}}]}
        else:
            assert body["messages"][-1]["role"] == "tool"
            message = {"role": "assistant", "content": "Прочитал проект"}
        return httpx.Response(200, json={"id": "test", "object": "chat.completion",
            "created": 1, "model": body["model"], "choices": [
                {"index": 0, "message": message, "finish_reason": "tool_calls" if len(requests) == 1 else "stop"}]})

    settings = Settings(_env_file=None, coding_provider="yandex",
                        yandex_api_key="test-key", yandex_folder_id="folder")
    model = create_chat_model(settings, coding=True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        # Привязываем клиент OpenAI с транспортом, задействуя реальную сериализацию LangChain.
        from openai import AsyncOpenAI
        model.async_client = AsyncOpenAI(api_key="test-key", base_url=model.openai_api_base,
            default_headers={"Authorization": "Api-Key test-key", "OpenAI-Project": "folder"},
            http_client=client).chat.completions
        bound = model.bind_tools([{"type": "function", "function": {
            "name": "read_project", "description": "Read code", "parameters": {
                "type": "object", "properties": {}}}}])
        first = await bound.ainvoke([HumanMessage(content="Изучи проект")])
        assert first.tool_calls[0]["name"] == "read_project"
        final = await bound.ainvoke([HumanMessage(content="Изучи проект"), first,
                                    ToolMessage(content="project data", tool_call_id="call-1")])
        assert final.content == "Прочитал проект"

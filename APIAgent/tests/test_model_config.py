"""Generation settings reach both providers independently for each specialist."""

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from apiagent.config import Settings
from apiagent.models import create_chat_model, create_coding_model


@pytest.mark.parametrize("provider, client, budget", [
    ("lmstudio", "ChatOpenAI", "max_tokens"),
    ("ollama", "ChatOllama", "num_predict"),
])
@pytest.mark.parametrize("coder_name", [None, "dedicated-coder"])
def test_independent_generation_settings(provider, client, budget, coder_name):
    settings = Settings(
        _env_file=None, llm_provider=provider, coding_model=coder_name,
        chat_temperature=0.2, chat_top_p=0.8, chat_max_tokens=1536,
        coding_temperature=0.1, coding_top_p=0.9, coding_max_tokens=3072,
    )
    with patch("apiagent.models.chat." + client) as constructor:
        create_chat_model(settings)
        create_coding_model(settings)
    chat, coder = [call.kwargs for call in constructor.call_args_list]
    expected = settings.lmstudio_model if provider == "lmstudio" else settings.ollama_model
    assert (chat["model"], chat["temperature"], chat["top_p"], chat[budget]) == (expected, 0.2, 0.8, 1536)
    assert (coder["model"], coder["temperature"], coder["top_p"], coder[budget]) == (coder_name or expected, 0.1, 0.9, 3072)


def test_generation_settings_from_environment(monkeypatch):
    monkeypatch.setenv("CHAT_TEMPERATURE", "0.3")
    monkeypatch.setenv("CODING_MAX_TOKENS", "8192")
    settings = Settings(_env_file=None)
    assert settings.chat_temperature == 0.3
    assert settings.coding_max_tokens == 8192


@pytest.mark.parametrize("field, value", [
    ("chat_temperature", -0.1), ("coding_temperature", 2.1),
    ("chat_top_p", 0), ("coding_top_p", 1.1),
    ("chat_max_tokens", 0), ("coding_max_tokens", 32769),
])
def test_invalid_generation_settings_fail_fast(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})

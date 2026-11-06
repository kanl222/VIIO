"""Chat model client for the consultant and shared provider configuration."""

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from assistent.config import Settings
from assistent.services.token_usage import token_usage


def create_chat_model(settings: Settings, *, model_name: str | None = None, coding: bool = False,
                      agent: str = 'chat') -> BaseChatModel:
    """Create an unbound client using this specialist's generation configuration."""
    temperature = settings.coding_temperature if coding else settings.chat_temperature
    top_p = settings.coding_top_p if coding else settings.chat_top_p
    max_tokens = settings.coding_max_tokens if coding else settings.chat_max_tokens
    role = 'coding' if coding else agent
    if role not in ('chat', 'coding', 'database'):
        raise ValueError('Unknown agent model role')
    provider = getattr(settings, f'{role}_provider') or settings.llm_provider
    model_name = model_name or getattr(settings, f'{role}_model')
    if provider == "yandex":
        name = (model_name or settings.yandex_model).strip()
        folder = settings.yandex_folder_id.strip()
        return ChatOpenAI(
            callbacks=[token_usage],
            base_url="https://ai.api.cloud.yandex.net/v1",
            api_key=settings.yandex_api_key.strip(),
            default_headers={"Authorization": f"Api-Key {settings.yandex_api_key.strip()}",
                             "OpenAI-Project": folder},
            model=name if name.startswith("gpt://") else f"gpt://{folder}/{name}",
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=settings.yandex_timeout_seconds,
            max_retries=0,
            use_responses_api=False,
        )
    if provider == "lmstudio":
        return ChatOpenAI(
            callbacks=[token_usage],
            base_url=settings.lmstudio_base_url,
            api_key=settings.lmstudio_api_key,
            model=model_name or settings.lmstudio_model,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
        )
    return ChatOllama(
        callbacks=[token_usage],
        base_url=settings.ollama_base_url,
        model=model_name or settings.ollama_model,
        temperature=temperature,
        top_p=top_p,
        num_predict=max_tokens,
    )

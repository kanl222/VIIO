"""Chat model client for the consultant and shared provider configuration."""

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from assistent.config import Settings
from assistent.services.token_usage import token_usage


def is_incompatible_local_model(name: str | None) -> bool:
    """Проверяет, является ли имя модели локальным (LM Studio / Ollama), несовместимым с Yandex Cloud."""
    if not name or not name.strip():
        return False
    n = name.strip().lower()
    if n.startswith(('gpt://', 'yandexgpt/')):
        return False
    if '/' in n:
        return True
    local_keywords = ('qwen', 'llama', 'mistral', 'deepseek', 'phi', 'gemma', 'nomic', 'codellama', 'starcoder')
    return any(k in n for k in local_keywords)


def resolve_yandex_model(model_name: str | None, default_yandex_model: str) -> str:
    """Возвращает имя модели Yandex, игнорируя несовместимые локальные имена моделей."""
    if model_name and model_name.strip() and not is_incompatible_local_model(model_name):
        return model_name.strip()
    return (default_yandex_model or "aliceai-llm").strip()


def create_chat_model(settings: Settings, *, model_name: str | None = None, coding: bool = False,
                      agent: str = 'chat') -> BaseChatModel:
    """Create an unbound client using this specialist's generation configuration."""
    temperature = settings.coding_temperature if coding else settings.chat_temperature
    top_p = settings.coding_top_p if coding else settings.chat_top_p
    max_tokens = settings.coding_max_tokens if coding else settings.chat_max_tokens
    role = 'coding' if coding else agent
    if role not in ('chat', 'coding', 'database', 'routing'):
        raise ValueError('Unknown agent model role')
    provider = getattr(settings, f'{role}_provider') or settings.llm_provider
    if role == 'routing':
        max_tokens = getattr(settings, 'routing_max_tokens', 128)
        temperature = 0
    model_name = model_name or getattr(settings, f'{role}_model')
    if provider == "yandex":
        name = resolve_yandex_model(model_name, settings.yandex_model)
        folder = settings.yandex_folder_id.strip()
        return ChatOpenAI(
            metadata={'viio_provider': provider},
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
    stop_sequences = [
        "<end_of_turn>", "<eos>", "<|im_end|>", "<|endoftext|>",
        "\nuser\n", "\nUser:", "\nAssistant:", "\nassistant\n", "\nmodel\n"
    ]
    timeout_seconds = getattr(settings, 'chat_timeout_seconds', 120)
    if provider == "lmstudio":
        return ChatOpenAI(
            stream_usage=True,
            metadata={'viio_provider': provider},
            callbacks=[token_usage],
            base_url=settings.lmstudio_base_url,
            api_key=settings.lmstudio_api_key,
            model=model_name or settings.lmstudio_model,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            stop=stop_sequences,
            timeout=timeout_seconds,
        )
    return ChatOllama(
        metadata={'viio_provider': provider},
        callbacks=[token_usage],
        base_url=settings.ollama_base_url,
        model=model_name or settings.ollama_model,
        temperature=temperature,
        top_p=top_p,
        num_predict=max_tokens,
        stop=stop_sequences,
        timeout=timeout_seconds,
    )

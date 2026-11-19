"""Independent model client; agent binds only the permitted knowledge search tool."""
from langchain_openai import ChatOpenAI
from langchain_ollama import ChatOllama


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


def create_model(settings):
    if settings.provider == 'yandex':
        name = resolve_yandex_model(settings.model, settings.yandex_model)
        folder = (settings.yandex_folder_id or '').strip()
        api_key = (settings.yandex_api_key or '').strip()
        return ChatOpenAI(base_url='https://ai.api.cloud.yandex.net/v1',
            api_key=api_key,
            default_headers={'Authorization': f'Api-Key {api_key}',
                             'OpenAI-Project': folder},
            model=name if name.startswith('gpt://') else f'gpt://{folder}/{name}',
            temperature=settings.temperature, max_tokens=settings.max_tokens,
            max_retries=0)
    if settings.provider == 'lmstudio':
        return ChatOpenAI(base_url=settings.lmstudio_base_url, api_key=settings.lmstudio_api_key,
            model=settings.model or settings.lmstudio_model, temperature=settings.temperature,
            top_p=settings.top_p, max_tokens=settings.max_tokens, streaming=True)
    return ChatOllama(base_url=settings.ollama_base_url, model=settings.model or settings.ollama_model,
        temperature=settings.temperature, top_p=settings.top_p, num_predict=settings.max_tokens)

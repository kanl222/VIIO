"""Independent model client; agent binds only the permitted knowledge search tool."""
from langchain_openai import ChatOpenAI
from langchain_ollama import ChatOllama


def create_model(settings):
    if settings.provider == 'yandex':
        name = settings.model or settings.yandex_model
        return ChatOpenAI(base_url='https://ai.api.cloud.yandex.net/v1',
            api_key=settings.yandex_api_key,
            default_headers={'Authorization': f'Api-Key {settings.yandex_api_key}',
                             'OpenAI-Project': settings.yandex_folder_id},
            model=name if name.startswith('gpt://') else f'gpt://{settings.yandex_folder_id}/{name}',
            temperature=settings.temperature, max_tokens=settings.max_tokens,
            max_retries=0)
    if settings.provider == 'lmstudio':
        return ChatOpenAI(base_url=settings.lmstudio_base_url, api_key=settings.lmstudio_api_key,
            model=settings.model or settings.lmstudio_model, temperature=settings.temperature,
            top_p=settings.top_p, max_tokens=settings.max_tokens, streaming=True)
    return ChatOllama(base_url=settings.ollama_base_url, model=settings.model or settings.ollama_model,
        temperature=settings.temperature, top_p=settings.top_p, num_predict=settings.max_tokens)

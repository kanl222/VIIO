"""Independent model client; agent binds only the permitted knowledge search tool."""
from langchain_openai import ChatOpenAI
from langchain_ollama import ChatOllama


def create_model(settings):
    if settings.provider == 'lmstudio':
        return ChatOpenAI(base_url=settings.lmstudio_base_url, api_key=settings.lmstudio_api_key,
            model=settings.model or settings.lmstudio_model, temperature=settings.temperature,
            top_p=settings.top_p, max_tokens=settings.max_tokens)
    return ChatOllama(base_url=settings.ollama_base_url, model=settings.model or settings.ollama_model,
        temperature=settings.temperature, top_p=settings.top_p, num_predict=settings.max_tokens)

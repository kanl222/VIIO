"""Chat model client for the consultant and shared provider configuration."""

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from apiagent.config import Settings


def create_chat_model(settings: Settings, *, model_name: str | None = None, coding: bool = False) -> BaseChatModel:
    """Create an unbound client using this specialist's generation configuration."""
    temperature = settings.coding_temperature if coding else settings.chat_temperature
    top_p = settings.coding_top_p if coding else settings.chat_top_p
    max_tokens = settings.coding_max_tokens if coding else settings.chat_max_tokens
    if settings.llm_provider == "lmstudio":
        return ChatOpenAI(
            base_url=settings.lmstudio_base_url,
            api_key=settings.lmstudio_api_key,
            model=model_name or settings.lmstudio_model,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
        )
    return ChatOllama(
        base_url=settings.ollama_base_url,
        model=model_name or settings.ollama_model,
        temperature=temperature,
        top_p=top_p,
        num_predict=max_tokens,
    )

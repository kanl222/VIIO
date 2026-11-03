"""Embedding model clients for knowledge search and document indexing."""

from langchain_core.embeddings import Embeddings
from langchain_ollama import OllamaEmbeddings
from langchain_openai import OpenAIEmbeddings

from viio_rag.config import Settings


def create_embeddings(settings: Settings) -> Embeddings:
    if settings.llm_provider == "lmstudio":
        return OpenAIEmbeddings(
            base_url=settings.lmstudio_base_url,
            api_key=settings.lmstudio_api_key,
            model=settings.lmstudio_embedding_model,
            check_embedding_ctx_length=False,
        )
    return OllamaEmbeddings(
        base_url=settings.ollama_base_url,
        model=settings.ollama_embedding_model,
    )

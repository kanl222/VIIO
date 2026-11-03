"""Configuration of the standalone retrieval service."""
from typing import Literal
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from viio_rag.prompts import ASSISTANT_YANDEX_RAG_PROMPT, CONSULTANT_YANDEX_RAG_PROMPT


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore', hide_input_in_errors=True)
    service_role: Literal['rag'] = 'rag'
    secure_runtime: bool = False
    llm_provider: Literal['lmstudio', 'ollama'] = 'lmstudio'
    lmstudio_base_url: str = 'http://localhost:1234/v1'
    lmstudio_api_key: str = Field(default='lm-studio', repr=False)
    lmstudio_embedding_model: str = 'text-embedding-nomic-embed-text-v1.5'
    ollama_base_url: str = 'http://localhost:11434'
    ollama_embedding_model: str = 'nomic-embed-text'
    qdrant_url: str = 'http://localhost:6333'
    qdrant_api_key: str | None = Field(default=None, repr=False)
    qdrant_collection_name: str = 'knowledge_base'
    qdrant_public_collection_name: str = 'knowledge_public'
    postgres_host: str = 'localhost'
    postgres_port: int = 5432
    postgres_db: str = 'agent_db'
    postgres_user: str = 'viio_documents'
    postgres_password: str = Field(default='postgres', repr=False)
    documents_read_key: str | None = Field(default=None, repr=False)
    documents_read_key_previous: str | None = Field(default=None, repr=False)
    consultant_source_key: str | None = Field(default=None, repr=False)
    consultant_api_key: str | None = Field(default=None, repr=False)
    internal_api_key: str | None = Field(default=None, repr=False)
    rag_yandex_api_key: str | None = Field(default=None, repr=False)
    rag_yandex_folder_id: str | None = None
    rag_yandex_index_id: str | None = None
    rag_yandex_model: str = 'aliceai-llm'
    rag_yandex_manifest: str = '/data/rag-state/yandex-rag-runtime.json'
    rag_yandex_assistant_prompt: str = ASSISTANT_YANDEX_RAG_PROMPT
    rag_yandex_consultant_prompt: str = CONSULTANT_YANDEX_RAG_PROMPT

    @model_validator(mode='after')
    def collections_differ(self):
        if self.qdrant_collection_name == self.qdrant_public_collection_name:
            raise ValueError('Public and internal collections must differ')
        return self

    def validate_runtime(self):
        if not self.secure_runtime:
            return
        keys = [self.documents_read_key, self.consultant_source_key]
        if any(not key or len(key) < 32 or 'viio-internal-secret-token' in key for key in keys):
            raise ValueError('RAG requires distinct service keys of at least 32 characters')
        if len(set(keys)) != len(keys):
            raise ValueError('RAG service keys must differ')
        previous = self.documents_read_key_previous
        if previous and (len(previous) < 32 or previous in keys):
            raise ValueError('Previous read key must be long and distinct')
